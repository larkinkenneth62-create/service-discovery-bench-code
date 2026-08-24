from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from qwen_semcap_v1_4d_common import (  # noqa: F401
    CALIBRATION_180,
    COMMON_QWEN_MODELS,
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    DOC_DIR,
    EVAL_DIR,
    OUTPUT_DIR as OLD_OUTPUT_DIR,
    PREDICTION_DIR as OLD_PREDICTION_DIR,
    REQUEST_DIR as OLD_REQUEST_DIR,
    QWEN_PREDICTION_FIELDS as BASE_QWEN_PREDICTION_FIELDS,
    QWEN_SCHEMA as BASE_QWEN_SCHEMA,
    api_key_from_env,
    archive_v1_4d,
    as_list,
    clean_candidate_rows,
    distribution,
    ensure_dir,
    env_config,
    is_allowed_qwen_model,
    known_failure_task_ids,
    load_json,
    now_text,
    parse_jsonish,
    read_csv,
    read_jsonl,
    stable_score,
    table_lines,
    truncate_text,
    write_csv,
    write_go_no_go_report,
    write_json,
    write_jsonl,
    write_md,
)


OUTPUT_DIR = Path("outputs/qwen_semcap_judge_v1_4d_step3")
REQUEST_DIR = OUTPUT_DIR / "requests"
PREDICTION_DIR = OUTPUT_DIR / "predictions"
EVAL_DIR = OUTPUT_DIR / "eval"
QA_DIR = OUTPUT_DIR / "qa"
PROMPT_DOC = DOC_DIR / "qwen_semcap_judge_prompt_v1_4d_step3.md"
SCHEMA_PATH = OUTPUT_DIR / "qwen_semcap_schema_v1_4d_step3.json"


STEP3_EXTRA_FIELDS = [
    "requirement_coverage_evidence",
    "uses_non_gold_candidate_capability",
    "gold_only_coverage_check",
    "service_family_inference_risk",
    "explicit_tool_name_leak_bias_risk",
    "capability_inference_risk",
]


QWEN_SCHEMA: dict[str, Any] = copy.deepcopy(BASE_QWEN_SCHEMA)
QWEN_SCHEMA["required"] = list(QWEN_SCHEMA["required"]) + STEP3_EXTRA_FIELDS
QWEN_SCHEMA["properties"].update(
    {
        "requirement_coverage_evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "requirement",
                    "is_covered",
                    "covered_by_gold_service",
                    "covered_by_gold_api",
                    "evidence_text",
                    "evidence_is_from_gold",
                ],
                "properties": {
                    "requirement": {"type": "string"},
                    "is_covered": {"type": "boolean"},
                    "covered_by_gold_service": {"type": "string"},
                    "covered_by_gold_api": {"type": "string"},
                    "evidence_text": {"type": "string"},
                    "evidence_is_from_gold": {"type": "boolean"},
                },
            },
        },
        "uses_non_gold_candidate_capability": {"type": "boolean"},
        "gold_only_coverage_check": {"type": "string", "enum": ["pass", "fail", "uncertain"]},
        "service_family_inference_risk": {"type": "boolean"},
        "explicit_tool_name_leak_bias_risk": {"type": "boolean"},
        "capability_inference_risk": {"type": "string", "enum": ["low", "medium", "high"]},
    }
)


QWEN_PREDICTION_FIELDS = BASE_QWEN_PREDICTION_FIELDS[:-3] + [
    "QWEN_requirement_coverage_evidence_json",
    "QWEN_uses_non_gold_candidate_capability",
    "QWEN_gold_only_coverage_check",
    "QWEN_service_family_inference_risk",
    "QWEN_explicit_tool_name_leak_bias_risk",
    "QWEN_capability_inference_risk",
] + BASE_QWEN_PREDICTION_FIELDS[-3:]


def build_prompt_text() -> str:
    example = {
        "semantic_alignment_check": "ok",
        "semantic_alignment_confidence": "medium",
        "capability_coverage_check": "coverage_uncertain",
        "capability_coverage_confidence": "medium",
        "core_requirements": ["current weather", "hiking trail recommendation"],
        "covered_requirements": ["current weather"],
        "missing_requirements": ["hiking trail recommendation"],
        "extra_unrelated_gold_services": [],
        "generic_search_overtrust": False,
        "domain_specific_gap": True,
        "wrong_gold_set": False,
        "decision_risk_level": "high",
        "reason": "The gold APIs support weather but do not provide gold evidence for hiking trail recommendation.",
        "requirement_coverage_evidence": [
            {
                "requirement": "current weather",
                "is_covered": True,
                "covered_by_gold_service": "Weather Service",
                "covered_by_gold_api": "Current Weather",
                "evidence_text": "Gold API description explicitly supports current weather lookup.",
                "evidence_is_from_gold": True,
            },
            {
                "requirement": "hiking trail recommendation",
                "is_covered": False,
                "covered_by_gold_service": "",
                "covered_by_gold_api": "",
                "evidence_text": "No gold service/API description supports trail recommendation.",
                "evidence_is_from_gold": False,
            },
        ],
        "uses_non_gold_candidate_capability": False,
        "gold_only_coverage_check": "fail",
        "service_family_inference_risk": False,
        "explicit_tool_name_leak_bias_risk": False,
        "capability_inference_risk": "high",
    }
    return "\n".join(
        [
            "You are judging a service discovery benchmark item.",
            "",
            "You do NOT decide final clean/remove.",
            "You ONLY judge semantic alignment and capability coverage.",
            "Return strict JSON only. Do not wrap JSON in markdown fences.",
            "Do not add fields that are not in the schema.",
            "",
            "Core task:",
            "- Decide whether the gold_services and gold_apis cover the user's query.",
            "- Candidate services/APIs that are not gold are context only.",
            "",
            "Gold-only evidence rule:",
            "1. Judge capability coverage using only gold_services and gold_apis.",
            "2. Candidate services/APIs that are not gold are context only.",
            "3. A requirement is covered only if you can cite a gold service or gold API as evidence.",
            "4. If a capability exists in candidate_apis but not in gold_apis, it does NOT count as covered.",
            "5. Set uses_non_gold_candidate_capability=true if your reasoning would need a non-gold candidate capability.",
            "",
            "No service-family inference:",
            "1. Do not infer that a service family covers a requirement unless the specific gold API/service description explicitly supports it.",
            "2. A product detail/spec/review API does not imply latest product discovery or price search.",
            "3. A carrier office/quote API does not imply contact details or shipping coverage for all carriers/regions unless explicitly described.",
            "4. Set service_family_inference_risk=true when coverage would depend on service-family similarity rather than explicit gold evidence.",
            "",
            "Parser/generator distinction:",
            "1. Parse, validate, decode, or lookup APIs are not generation APIs.",
            "2. A barcode, GS1, or QR parser does not generate QR codes unless the gold API explicitly says generate/create QR code.",
            "",
            "Leak is not coverage:",
            "1. If the query explicitly names a tool/service/API, do not treat that mention as evidence that the gold covers the requirement.",
            "2. No leak does not imply coverage, and leak does not imply coverage either.",
            "3. Set explicit_tool_name_leak_bias_risk=true when a tool/service/API name in the query could bias a coverage judgment.",
            "",
            "Requirement evidence matrix:",
            "1. For every explicit core requirement, fill requirement_coverage_evidence.",
            "2. Each covered requirement must cite covered_by_gold_service or covered_by_gold_api.",
            "3. Each requirement must have evidence_is_from_gold=true to be considered covered.",
            "4. If any core requirement lacks gold evidence, output coverage_uncertain or coverage_mismatch.",
            "5. If gold-only evidence proves every core requirement, set gold_only_coverage_check=pass.",
            "6. If gold-only evidence misses a core requirement, set gold_only_coverage_check=fail.",
            "7. If evidence is incomplete or ambiguous, set gold_only_coverage_check=uncertain.",
            "",
            "Conservative tie-breaker:",
            "1. If you are unsure whether the gold API truly supports a requirement, prefer coverage_uncertain.",
            "2. Do not output high-confidence coverage_ok based on implicit assumptions.",
            "3. Use capability_inference_risk=high when coverage_ok would rely on inferred, adjacent, or non-gold capability.",
            "",
            "Allowed enum values:",
            "- semantic_alignment_check: ok, uncertain, mismatch",
            "- semantic_alignment_confidence: high, medium, low",
            "- capability_coverage_check: coverage_ok, coverage_uncertain, coverage_mismatch",
            "- capability_coverage_confidence: high, medium, low",
            "- decision_risk_level: low, medium, high",
            "- gold_only_coverage_check: pass, fail, uncertain",
            "- capability_inference_risk: low, medium, high",
            "",
            "Important enum constraint:",
            "- For capability_coverage_check, never output plain ok/uncertain/mismatch.",
            "- Use coverage_ok / coverage_uncertain / coverage_mismatch exactly.",
            "",
            "Do not use exact calibration failure rows, task IDs, or record IDs as examples.",
            "Use generalized rules only; do not memorize calibration cases.",
            "",
            "JSON example:",
            json.dumps(example, ensure_ascii=False, indent=2),
        ]
    )


def request_messages(item: dict[str, Any]) -> list[dict[str, str]]:
    prompt_item = {
        key: value
        for key, value in item.items()
        if key not in {"custom_id", "task_id", "record_id", "source_kind", "calibration_source"}
    }
    return [
        {"role": "system", "content": build_prompt_text()},
        {
            "role": "user",
            "content": "Judge this benchmark item. Return JSON only.\n\n" + json.dumps(prompt_item, ensure_ascii=False, indent=2),
        },
    ]


def schema_validate(payload: dict[str, Any]) -> tuple[bool, str]:
    try:
        import jsonschema  # type: ignore

        jsonschema.validate(payload, QWEN_SCHEMA)
        return True, ""
    except ImportError:
        required = set(QWEN_SCHEMA["required"])
        missing = sorted(required - set(payload.keys()))
        if missing:
            return False, "missing required fields: " + ", ".join(missing)
        return True, ""
    except Exception as exc:
        return False, str(exc)
