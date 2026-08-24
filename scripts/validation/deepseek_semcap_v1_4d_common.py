from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence


OUTPUT_DIR = Path("outputs/deepseek_semcap_judge_v1_4d")
REQUEST_DIR = OUTPUT_DIR / "requests"
PREDICTION_DIR = OUTPUT_DIR / "predictions"
EVAL_DIR = OUTPUT_DIR / "eval"
REGRESSION_DIR = OUTPUT_DIR / "regression"
QA_DIR = OUTPUT_DIR / "qa"
DOC_DIR = Path("docs/phase1")

V14C_TASK_TRACE = Path("outputs/full_clean_dryrun_v1_4c/full_clean_task_trace_v1_4c.csv")
V14C_SUMMARY = Path("outputs/full_clean_dryrun_v1_4c/full_clean_dryrun_summary_v1_4c.json")
V14C_GO_NO_GO = DOC_DIR / "full_clean_dryrun_v1_4c_go_no_go_report.md"

V15C_FAILURE_PATCH = Path("outputs/final_qa_v1_5c/final_qa_clean_candidate_failure_patch_v1_5c.csv")
V15C_FAILURE_TAXONOMY = DOC_DIR / "final_qa_clean_candidate_failure_taxonomy_v1_5c.md"
V15D_FAILURE_TAXONOMY = DOC_DIR / "final_qa_v1_5d_failure_taxonomy.md"
V15D_REVIEW_SET = Path("outputs/final_qa_v1_5d/final_qa_review_items_v1_5d.csv")
V15D_MERGED = Path("outputs/final_qa_v1_5d/analysis/final_qa_review_items_v1_5d_merged.csv")
V15D_ANALYSIS_REPORT = DOC_DIR / "final_qa_analysis_report_v1_5d.md"

CALIBRATION_180 = Path("outputs/semcap_detector_v1_implementation_v1_1/combined_semcap_calibration_180.csv")
SEMCAP_PRED_180 = Path("outputs/semcap_detector_v1_implementation_v1_1/semcap_predictions_combined_180_v1.csv")

MANUAL_V42_DOC = DOC_DIR / "manual_audit_rule_v4_2_candidate.md"
SEMCAP_V13_DOC = DOC_DIR / "semcap_v1_3_tightening_rules_candidate.md"
POLICY_V14C_DOC = DOC_DIR / "policy_v1_4c_tightening_plan.md"
SEMCAP_V12_DOC = DOC_DIR / "semcap_v1_2_tightening_rules_candidate.md"

PROMPT_DOC = DOC_DIR / "deepseek_semcap_judge_prompt_v1_4d.md"
GO_NO_GO_DOC = DOC_DIR / "deepseek_semcap_judge_v1_4d_go_no_go_report.md"
SCHEMA_PATH = OUTPUT_DIR / "deepseek_semcap_schema_v1_4d.json"

ALLOWED_MODELS = {"deepseek-v4-pro", "deepseek-v4-flash"}
DEFAULT_MODEL = "deepseek-v4-pro"
DEFAULT_BASE_URL = "https://api.deepseek.com"

QA_HUMAN_FIELDS = [
    "qa_final_decision",
    "qa_semantic_alignment_check",
    "qa_capability_coverage_check",
    "qa_leakage_check",
    "qa_candidate_validity_check",
    "qa_task_type_check",
    "qa_dedup_check",
    "qa_error_type",
    "qa_severity",
    "qa_notes",
]

DEEPSEEK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "semantic_alignment_check",
        "semantic_alignment_confidence",
        "capability_coverage_check",
        "capability_coverage_confidence",
        "core_requirements",
        "covered_requirements",
        "missing_requirements",
        "extra_unrelated_gold_services",
        "generic_search_overtrust",
        "domain_specific_gap",
        "wrong_gold_set",
        "decision_risk_level",
        "reason",
    ],
    "properties": {
        "semantic_alignment_check": {"type": "string", "enum": ["ok", "uncertain", "mismatch"]},
        "semantic_alignment_confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "capability_coverage_check": {
            "type": "string",
            "enum": ["coverage_ok", "coverage_uncertain", "coverage_mismatch"],
        },
        "capability_coverage_confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "core_requirements": {"type": "array", "items": {"type": "string"}},
        "covered_requirements": {"type": "array", "items": {"type": "string"}},
        "missing_requirements": {"type": "array", "items": {"type": "string"}},
        "extra_unrelated_gold_services": {"type": "array", "items": {"type": "string"}},
        "generic_search_overtrust": {"type": "boolean"},
        "domain_specific_gap": {"type": "boolean"},
        "wrong_gold_set": {"type": "boolean"},
        "decision_risk_level": {"type": "string", "enum": ["low", "medium", "high"]},
        "reason": {"type": "string"},
    },
}

DEEPSEEK_PREDICTION_FIELDS = [
    "custom_id",
    "task_id",
    "source_group",
    "task_type",
    "prediction_level",
    "deepseek_model",
    "deepseek_structured_mode",
    "deepseek_thinking",
    "deepseek_parse_status",
    "deepseek_finish_reason",
    "deepseek_semantic_alignment_check",
    "deepseek_semantic_alignment_confidence",
    "deepseek_capability_coverage_check",
    "deepseek_capability_coverage_confidence",
    "deepseek_core_requirements_json",
    "deepseek_covered_requirements_json",
    "deepseek_missing_requirements_json",
    "deepseek_extra_unrelated_gold_services_json",
    "deepseek_generic_search_overtrust",
    "deepseek_domain_specific_gap",
    "deepseek_wrong_gold_set",
    "deepseek_decision_risk_level",
    "deepseek_reason",
    "prompt_token_count",
    "completion_token_count",
    "total_token_count",
    "api_latency_seconds",
]


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: Iterable[dict[str, Any]], fieldnames: Sequence[str]) -> int:
    ensure_dir(path.parent)
    count = 0
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(fieldnames), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})
            count += 1
    return count


def write_json(path: Path, payload: Any) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def write_md(path: Path, lines: Sequence[str]) -> None:
    ensure_dir(path.parent)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    ensure_dir(path.parent)
    count = 0
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    return count


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def distribution(rows: Sequence[dict[str, str]], field: str) -> dict[str, int]:
    return dict(Counter((row.get(field, "") or "<blank>").strip() or "<blank>" for row in rows))


def table_lines(counter: dict[str, int] | Counter) -> list[str]:
    lines = ["| value | count |", "|---|---:|"]
    for key, value in sorted(dict(counter).items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"| {key} | {value} |")
    return lines


def parse_jsonish(value: Any) -> Any:
    if isinstance(value, (list, dict)):
        return value
    text = str(value or "").strip()
    if not text:
        return []
    try:
        return json.loads(text)
    except Exception:
        return []


def as_list(value: Any) -> list[Any]:
    data = parse_jsonish(value)
    return data if isinstance(data, list) else []


def truncate_text(value: Any, limit: int) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[:limit] + "...[truncated]"


def stable_score(*parts: str) -> int:
    text = "||".join(str(part or "") for part in parts)
    return int(hashlib.md5(text.encode("utf-8")).hexdigest(), 16)


def env_config() -> dict[str, Any]:
    model = os.environ.get("DEEPSEEK_API_MODEL", DEFAULT_MODEL)
    return {
        "api_key_exists": bool(os.environ.get("DEEPSEEK_API_KEY")),
        "base_url": os.environ.get("DEEPSEEK_API_BASE_URL", DEFAULT_BASE_URL),
        "model": model,
        "model_allowed": model in ALLOWED_MODELS,
        "structured_mode": os.environ.get("DEEPSEEK_STRUCTURED_MODE", "tool_call_strict"),
        "thinking": os.environ.get("DEEPSEEK_THINKING", "disabled"),
        "allow_full_run": os.environ.get("ALLOW_DEEPSEEK_FULL_RUN", "").lower() == "true",
    }


def clean_candidate_rows() -> list[dict[str, str]]:
    rows = read_csv(V14C_TASK_TRACE)
    return [row for row in rows if row.get("dryrun_decision_v1_4c") == "dryrun_clean_candidate"]


def known_failure_task_ids() -> set[str]:
    ids: set[str] = set()
    if V15C_FAILURE_PATCH.exists():
        for row in read_csv(V15C_FAILURE_PATCH):
            task_id = row.get("task_id", "")
            if task_id:
                ids.add(task_id)
    if V15D_REVIEW_SET.exists():
        for row in read_csv(V15D_REVIEW_SET):
            if row.get("qa_bucket") == "previous_failed_regression" and row.get("task_id"):
                ids.add(row["task_id"])
    if V15D_MERGED.exists():
        for row in read_csv(V15D_MERGED):
            if row.get("qa_final_decision") == "fail" and row.get("task_id"):
                ids.add(row["task_id"])
    return ids


def build_prompt_text() -> str:
    example = {
        "semantic_alignment_check": "ok",
        "semantic_alignment_confidence": "high",
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
        "reason": "The gold APIs cover weather but do not cover hiking trail recommendation.",
    }
    return "\n".join(
        [
            "You are judging a service discovery benchmark item.",
            "",
            "You do NOT decide final clean/remove.",
            "You ONLY judge semantic alignment and capability coverage.",
            "Return strict JSON only.",
            "",
            "Important rules:",
            "1. Do not reward gold just because it is present in the candidate list.",
            "2. No leak does not imply coverage.",
            "3. Every explicit core requirement in the query must be covered.",
            "4. If one major requirement is missing, output coverage_mismatch or coverage_uncertain.",
            "5. Generic search, image search, news search, autosuggest, or entity search is not universal coverage.",
            "6. For service-level tasks, unrelated extra gold services must be listed.",
            "7. If API/service descriptions are insufficient, prefer uncertain.",
            "8. Do not use external knowledge or web search. Judge only from the provided query and service/API descriptions.",
            "9. If a service/API only partially covers the query, output coverage_uncertain or coverage_mismatch.",
            "10. If the gold set contains an unnecessary service that does not cover any query requirement, set wrong_gold_set=true.",
            "",
            "JSON example:",
            json.dumps(example, ensure_ascii=False, indent=2),
        ]
    )


def request_messages(item: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": build_prompt_text()},
        {
            "role": "user",
            "content": "Judge this benchmark item. Return JSON only.\n\n" + json.dumps(item, ensure_ascii=False, indent=2),
        },
    ]


def schema_validate(payload: dict[str, Any]) -> tuple[bool, str]:
    try:
        import jsonschema  # type: ignore

        jsonschema.validate(payload, DEEPSEEK_SCHEMA)
        return True, ""
    except ImportError:
        required = set(DEEPSEEK_SCHEMA["required"])
        missing = sorted(required - set(payload.keys()))
        if missing:
            return False, "missing required fields: " + ", ".join(missing)
        return True, ""
    except Exception as exc:
        return False, str(exc)


def archive_v1_4d(root: Path) -> list[str]:
    archive_dir = root / "outputs" / "run_archives" / f"{datetime.now().strftime('%Y-%m-%d')}_deepseek_semcap_judge_v1_4d"
    ensure_dir(archive_dir)
    paths = [
        Path("scripts/validation/deepseek_semcap_v1_4d_common.py"),
        Path("scripts/validation/check_deepseek_semcap_v1_4d_inputs.py"),
        Path("scripts/validation/build_deepseek_semcap_requests_v1_4d.py"),
        Path("scripts/validation/run_deepseek_semcap_judge_v1_4d.py"),
        Path("scripts/validation/evaluate_deepseek_semcap_on_calibration_v1_4d.py"),
        Path("scripts/validation/apply_deepseek_semcap_policy_v1_4d.py"),
        Path("scripts/validation/check_deepseek_semcap_regression_v1_4d.py"),
        Path("scripts/validation/build_deepseek_assisted_qa_v1_4d.py"),
        OUTPUT_DIR,
        PROMPT_DOC,
        DOC_DIR / "deepseek_semcap_sample20_report_v1_4d.md",
        DOC_DIR / "deepseek_semcap_calibration_eval_report_v1_4d.md",
        DOC_DIR / "deepseek_semcap_full2168_run_report_v1_4d.md",
        DOC_DIR / "deepseek_assisted_clean_trace_report_v1_4d.md",
        DOC_DIR / "deepseek_semcap_regression_report_v1_4d.md",
        DOC_DIR / "deepseek_assisted_final_qa_protocol_v1_4d.md",
        GO_NO_GO_DOC,
    ]
    copied: list[str] = []
    for rel in paths:
        src = root / rel
        if not src.exists():
            continue
        dest = archive_dir / rel
        ensure_dir(dest.parent)
        if src.is_dir():
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(src, dest)
        else:
            shutil.copy2(src, dest)
        copied.append(str(dest))
    write_md(
        archive_dir / "ARCHIVE_MANIFEST.md",
        [
            "# DeepSeek SemCap Judge v1.4d Archive Manifest",
            "",
            f"Generated time: {now_text()}",
            f"Archive directory: `{archive_dir}`",
            "",
            "This archive contains v1.4d preparation and available run artifacts only.",
            "It does not contain final clean data, split data, baseline results, model training, API keys, or automatic human labels.",
            "",
            "## Archived Files",
            "",
            *[f"- `{path}`" for path in copied],
        ],
    )
    return copied


def write_go_no_go_report(payload: dict[str, Any]) -> None:
    lines = [
        "# DeepSeek SemCap Judge v1.4d Go / No-Go Report",
        "",
        f"Generated time: {now_text()}",
        "Input stage: v1.4c clean candidates and calibration 180.",
        "",
        "## Go / No-Go Decision v1.4d DeepSeek SemCap",
        "",
        f"Go / No-Go Decision: {payload.get('go_no_go_decision', 'WAITING')}",
        "",
        f"- can_accept_deepseek_sample20: {str(payload.get('can_accept_deepseek_sample20', False)).lower()}",
        f"- can_accept_deepseek_calibration: {str(payload.get('can_accept_deepseek_calibration', False)).lower()}",
        f"- can_accept_deepseek_full_predictions: {str(payload.get('can_accept_deepseek_full_predictions', False)).lower()}",
        f"- can_prepare_deepseek_assisted_final_qa: {str(payload.get('can_prepare_deepseek_assisted_final_qa', False)).lower()}",
        "- can_generate_final_clean_dataset_now: false",
        "- can_create_split_now: false",
        "- can_run_baseline_now: false",
        "- can_train_model_now: false",
        "",
        f"recommended_next_step: {payload.get('recommended_next_step', 'complete the current gated step first')}",
        "",
        "DeepSeek predictions are SemCap evidence only. They are not human final labels and do not replace deterministic policy gates.",
    ]
    write_md(GO_NO_GO_DOC, lines)
