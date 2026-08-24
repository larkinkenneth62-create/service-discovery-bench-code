from __future__ import annotations

import argparse
import csv
import json
import shutil
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from build_qwen_semcap_requests_v1_4d import request_from_row


DOC_DIR = Path("docs/phase1")
FINAL_QA_DIR = Path("outputs/final_qa_v1_5e")
STEP3_FINALQA_DIR = Path("outputs/qwen_semcap_judge_v1_4d_step3/finalqa100")
REQUEST_DIR = STEP3_FINALQA_DIR / "requests"
ARCHIVE_DIR = Path("outputs/run_archives/2026-07-01_post_final_qa_v1_5e_closeout")

HUMAN_FIELDS = [
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

REQUEST_BLOCKED_FIELDS = set(HUMAN_FIELDS)

REQUIRED_TAXONOMY = [
    "missing_core_requirement",
    "gold_api_capability_mismatch",
    "wrong_gold_set",
    "dummy_or_test_endpoint",
    "support_list_endpoint_not_action_api",
    "object_scope_uncertain_or_mismatch",
    "forecast_vs_current_weather_uncertain",
    "generic_search_news_image_overtrust",
    "travel_place_recommendation_gap",
    "duplicate_non_representative",
    "composable_raw_not_strong_dependency",
]

ALLOWED_VALUES = {
    "qa_final_decision": {"keep_for_cleaning_candidate", "uncertain", "remove"},
    "qa_semantic_alignment_check": {
        "ok",
        "uncertain",
        "mismatch",
        "semantic_alignment_ok",
        "semantic_alignment_uncertain",
        "semantic_mismatch_uncertain",
    },
    "qa_capability_coverage_check": {"coverage_ok", "coverage_uncertain", "coverage_mismatch"},
    "qa_leakage_check": {
        "no_blocking_leak",
        "no_obvious_leak",
        "api_leak_blocking",
        "service_leak_only",
        "leak_uncertain",
    },
    "qa_candidate_validity_check": {
        "valid",
        "invalid",
        "uncertain",
        "candidate_valid",
        "candidate_no_choice",
        "candidate_invalid",
        "candidate_uncertain",
    },
    "qa_task_type_check": {
        "valid_multi_service",
        "valid_multi_api",
        "valid_composable",
        "ordinary_multi",
        "invalid",
        "uncertain",
        "task_type_ok",
        "task_type_invalid",
        "task_type_uncertain",
        "composable_not_strong_dependency",
    },
    "qa_dedup_check": {
        "unique",
        "representative_duplicate_group",
        "non_representative_duplicate",
        "dedup_ok",
        "dedup_uncertain",
        "duplicate_non_representative",
        "duplicate_representative_ok",
        "not_checked",
    },
    "qa_severity": {"none", "minor", "medium", "major", "high", "critical", "uncertain"},
}


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Missing required CSV: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_json(path: Path, payload: Any) -> None:
    ensure_parent(path)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_md(path: Path, lines: list[str]) -> None:
    ensure_parent(path)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    ensure_parent(path)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def table(counter: dict[str, int] | Counter[str]) -> list[str]:
    if not counter:
        return ["| value | count |", "|---|---:|", "| <empty> | 0 |"]
    lines = ["| value | count |", "|---|---:|"]
    for key, value in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"| {key or '<blank>'} | {value} |")
    return lines


def dist(rows: list[dict[str, str]], field: str) -> Counter[str]:
    return Counter((row.get(field, "") or "<blank>") for row in rows)


def crosstab(rows: list[dict[str, str]], left: str, right: str) -> dict[str, dict[str, int]]:
    out: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        out[row.get(left, "") or "<blank>"][row.get(right, "") or "<blank>"] += 1
    return {key: dict(value) for key, value in sorted(out.items())}


def missing_by_field(rows: list[dict[str, str]], fields: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for field in fields:
        if field == "qa_error_type":
            out[field] = sum(
                1
                for row in rows
                if not row.get(field, "").strip()
                and (
                    row.get("qa_final_decision") in {"remove", "uncertain"}
                    or row.get("qa_severity") in {"medium", "major", "high", "critical"}
                )
            )
        else:
            out[field] = sum(1 for row in rows if not row.get(field, "").strip())
    return out


def invalid_values(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    invalid: list[dict[str, str]] = []
    for row in rows:
        for field, allowed in ALLOWED_VALUES.items():
            value = row.get(field, "").strip()
            if value and value not in allowed:
                invalid.append({"qa_item_id": row.get("qa_item_id", ""), "field": field, "value": value})
    return invalid


def infer_taxonomy(row: dict[str, str]) -> set[str]:
    text = " ".join(
        [
            row.get("qa_error_type", ""),
            row.get("qa_notes", ""),
            row.get("qa_subbucket", ""),
            row.get("qa_sampling_reason", ""),
            row.get("risk_keywords_matched", ""),
            row.get("v1_4c_blocking_reasons", ""),
            row.get("v1_4c_warning_reasons", ""),
        ]
    ).lower()
    found: set[str] = set()

    direct = row.get("qa_error_type", "").strip()
    if direct in REQUIRED_TAXONOMY:
        found.add(direct)

    keyword_map = {
        "missing_core_requirement": ["missing_core_requirement", "missing core", "missing requirement", "not cover", "cannot provide"],
        "gold_api_capability_mismatch": ["gold_api_capability_mismatch", "capability mismatch", "coverage_mismatch", "api cannot", "cannot implement"],
        "wrong_gold_set": ["wrong_gold_set", "wrong gold", "gold service", "gold api", "unrelated gold"],
        "dummy_or_test_endpoint": ["dummy", "test endpoint", "sandbox", "healthcheck", "checkhealth"],
        "support_list_endpoint_not_action_api": ["support list", "list endpoint", "not action", "only lists", "metadata only"],
        "object_scope_uncertain_or_mismatch": ["object scope", "scope", "package", "parcel", "mail", "container", "country", "region"],
        "forecast_vs_current_weather_uncertain": ["forecast", "current weather", "weather"],
        "generic_search_news_image_overtrust": ["generic search", "news", "image", "overtrust", "generic"],
        "travel_place_recommendation_gap": ["travel", "place", "restaurant", "hotel", "attraction", "recommendation"],
        "duplicate_non_representative": ["duplicate", "non representative", "non-representative"],
        "composable_raw_not_strong_dependency": ["composable", "ordinary_multi", "not strong dependency", "parallel"],
    }
    for family, terms in keyword_map.items():
        if any(term in text for term in terms):
            found.add(family)

    if row.get("task_type") == "composable_service_discovery_raw" and row.get("qa_final_decision") != "keep_for_cleaning_candidate":
        found.add("composable_raw_not_strong_dependency")
    if row.get("qa_dedup_check") == "duplicate_non_representative":
        found.add("duplicate_non_representative")
    return found


def taxonomy_summary(rows: list[dict[str, str]]) -> tuple[dict[str, int], dict[str, list[dict[str, str]]]]:
    target_rows = [row for row in rows if row.get("qa_final_decision") in {"remove", "uncertain"} or row.get("qa_severity") == "critical"]
    counts = {family: 0 for family in REQUIRED_TAXONOMY}
    examples: dict[str, list[dict[str, str]]] = {family: [] for family in REQUIRED_TAXONOMY}
    for row in target_rows:
        for family in infer_taxonomy(row):
            if family not in counts:
                continue
            counts[family] += 1
            if len(examples[family]) < 5:
                examples[family].append(
                    {
                        "qa_item_id": row.get("qa_item_id", ""),
                        "task_id": row.get("task_id", ""),
                        "decision": row.get("qa_final_decision", ""),
                        "severity": row.get("qa_severity", ""),
                        "error_type": row.get("qa_error_type", "")[:180],
                        "query": row.get("query_text", "")[:220],
                    }
                )
    return counts, examples


def sanitize_for_request(row: dict[str, str]) -> dict[str, str]:
    out = dict(row)
    out["record_id"] = row.get("qa_item_id", "")
    out["dryrun_bucket_v1_4c"] = row.get("v1_4c_dryrun_bucket", "")
    for field in REQUEST_BLOCKED_FIELDS:
        out.pop(field, None)
    return out


def build_finalqa_requests(rows: list[dict[str, str]]) -> tuple[list[dict[str, Any]], bool]:
    requests: list[dict[str, Any]] = []
    label_leak_found = False
    for row in rows:
        req = request_from_row(sanitize_for_request(row), "finalQA100_v1_5e")
        req["record_id"] = row.get("qa_item_id", "")
        req["custom_id"] = f"qwen_semcap::finalQA100_v1_5e::{row.get('qa_item_id', '')}"
        req["source_kind"] = "finalQA100_v1_5e"
        if any(field in req for field in REQUEST_BLOCKED_FIELDS):
            label_leak_found = True
        requests.append(req)
    return requests, label_leak_found


def completion_summary(rows: list[dict[str, str]]) -> dict[str, Any]:
    keep = sum(1 for row in rows if row.get("qa_final_decision") == "keep_for_cleaning_candidate")
    uncertain = sum(1 for row in rows if row.get("qa_final_decision") == "uncertain")
    remove = sum(1 for row in rows if row.get("qa_final_decision") == "remove")
    critical = sum(1 for row in rows if row.get("qa_severity") == "critical")
    comp = [row for row in rows if row.get("task_type") == "composable_service_discovery_raw"]
    comp_dist = dist(comp, "qa_final_decision")
    return {
        "row_count": len(rows),
        "keep_for_cleaning_candidate": keep,
        "uncertain": uncertain,
        "remove": remove,
        "remove_rate": round(remove / len(rows), 4) if rows else 0,
        "uncertain_rate": round(uncertain / len(rows), 4) if rows else 0,
        "critical": critical,
        "composable_service_discovery_raw": dict(comp_dist),
    }


def write_go_no_go(reviewed: Path, rows: list[dict[str, str]], summary: dict[str, Any], invalid: list[dict[str, str]]) -> Path:
    final_dist = dist(rows, "qa_final_decision")
    severity_dist = dist(rows, "qa_severity")
    bucket_tab = crosstab(rows, "task_type", "qa_final_decision")
    critical = summary["critical"]
    decision = "NO_GO_V1_6_AS_IS"
    lines = [
        "# Final QA v1.5e Go / No-Go Report",
        "",
        f"Generated time: {now_text()}",
        f"Input reviewed CSV: `{reviewed}`",
        f"Sample count: {len(rows)}",
        "",
        f"Final decision: `{decision}`",
        "",
        "## Human Final Distribution",
        "",
        *table(final_dist),
        "",
        "## Severity Distribution",
        "",
        *table(severity_dist),
        "",
        "## Task Type x Human Final",
        "",
    ]
    lines.extend(["| task_type | keep | uncertain | remove |", "|---|---:|---:|---:|"])
    for task_type, values in bucket_tab.items():
        lines.append(
            f"| {task_type} | {values.get('keep_for_cleaning_candidate', 0)} | {values.get('uncertain', 0)} | {values.get('remove', 0)} |"
        )
    lines.extend(
        [
            "",
            "## Gate Decision",
            "",
            f"- critical_count: {critical}",
            f"- remove_count: {summary['remove']}",
            f"- uncertain_count: {summary['uncertain']}",
            f"- remove_rate: {summary['remove_rate']}",
            "- can_accept_final_qa_v1_5e_as_pass: false",
            "- can_generate_final_clean_dataset_now: false",
            "- can_run_qwen_full2168_next: false until finalQA100 judge audit or explicit override",
            "- can_create_split_now: false",
            "- can_run_baseline_now: false",
            "- can_train_model_now: false",
            "",
            "Rationale: v1.5e still contains critical errors and a large remove/uncertain pool, so v1.4c cannot enter v1.6 as-is.",
            "",
            "Recommended next step: build v1.5f tightening policy and run finalQA100 Qwen judge reliability audit before any full2168.",
            "",
            "No Qwen full2168, full cleaning, final clean dataset, split, baseline, or training was run.",
        ]
    )
    if invalid:
        lines.extend(["", "## Invalid / Unexpected Values", ""])
        lines.extend(f"- {item['qa_item_id']} `{item['field']}` = `{item['value']}`" for item in invalid[:50])
    path = DOC_DIR / "final_qa_v1_5e_go_no_go_report.md"
    write_md(path, lines)
    return path


def write_failure_taxonomy(reviewed: Path, rows: list[dict[str, str]], counts: dict[str, int], examples: dict[str, list[dict[str, str]]]) -> Path:
    lines = [
        "# Final QA v1.5e Failure Taxonomy",
        "",
        f"Generated time: {now_text()}",
        f"Input reviewed CSV: `{reviewed}`",
        f"Sample count: {len(rows)}",
        "",
        "This taxonomy is derived from human QA fields and notes. It is a policy-design artifact, not a model prediction.",
        "",
        "## Taxonomy Counts",
        "",
        *table(Counter(counts)),
        "",
        "## Family Notes And Examples",
        "",
    ]
    for family in REQUIRED_TAXONOMY:
        lines.extend([f"### {family}", "", f"- count: {counts.get(family, 0)}"])
        ex = examples.get(family, [])
        if ex:
            for item in ex:
                query = item["query"].replace("\n", " ")
                error_type = item["error_type"] or "<blank>"
                lines.append(
                    f"- {item['qa_item_id']} / {item['task_id']} / {item['decision']} / {item['severity']} / "
                    f"`{error_type}`: {query}"
                )
        else:
            lines.append("- No direct example found in the current reviewed CSV; keep this family as a required check for v1.5f.")
        lines.append("")
    path = DOC_DIR / "final_qa_v1_5e_failure_taxonomy.md"
    write_md(path, lines)
    return path


def write_policy_plan(reviewed: Path, rows: list[dict[str, str]], counts: dict[str, int]) -> Path:
    lines = [
        "# Policy v1.5f Tightening Plan From Final QA v1.5e",
        "",
        f"Generated time: {now_text()}",
        f"Input reviewed CSV: `{reviewed}`",
        f"Sample count: {len(rows)}",
        "",
        "## Goal",
        "",
        "v1.5f should tighten the clean-candidate policy before any v1.6/final-clean step. It must not use hard-coded qa_item_id or task_id rules.",
        "",
        "## Required Tightening Rules",
        "",
        "1. Gold-only coverage gate: every explicit core requirement must be supported by gold service/API evidence, not by non-gold candidates.",
        "2. Missing-core-requirement gate: missing location, contact, recommendation, booking, generation, or action requirements block clean status.",
        "3. Gold API capability mismatch gate: list/support/metadata endpoints must not pass as action APIs.",
        "4. Wrong gold set gate: unrelated extra gold service/API or missing necessary gold service/API blocks clean status.",
        "5. Dummy/test endpoint gate: healthcheck, test, sample, or dummy endpoints cannot satisfy user-facing tasks.",
        "6. Object-scope gate: package/mail/container, forecast/current weather, region/country/carrier scope mismatches must become uncertain or remove.",
        "7. Generic search/news/image overtrust gate: generic services cannot cover domain-specific requirements without explicit gold evidence.",
        "8. Travel/place recommendation gap gate: location/place/restaurant/hotel/concert recommendations require direct support.",
        "9. Duplicate representative gate: non-representative duplicates cannot remain clean candidates.",
        "10. Composable dependency gate: composable raw samples need an explicit dependency chain; ordinary multi-task samples stay uncertain/remove.",
        "",
        "## Failure Families To Track",
        "",
        *table(Counter(counts)),
        "",
        "## Proposed v1.5f Dry-Run Checks",
        "",
        "- Re-run only a dry-run tightening pass on current clean candidates.",
        "- Produce before/after movement counts: keep, uncertain, remove.",
        "- Include impacted-clean-candidate QA before any final clean dataset.",
        "- Keep Qwen Step3 as auxiliary annotation only; do not treat Qwen output as human final.",
        "",
        "## Stop Conditions",
        "",
        "- Do not enter v1.6 while critical final QA errors remain.",
        "- Do not run Qwen full2168 until finalQA100 judge reliability audit is completed or explicitly overridden.",
        "- Do not split, baseline, or train before a formally accepted clean dataset exists.",
    ]
    path = DOC_DIR / "policy_v1_5f_tightening_plan_from_final_qa.md"
    write_md(path, lines)
    return path


def write_reliability_protocol(reviewed: Path, request_path: Path, rows: list[dict[str, str]]) -> Path:
    lines = [
        "# LLM Judge Reliability Protocol v0.1",
        "",
        f"Generated time: {now_text()}",
        f"Input reviewed CSV: `{reviewed}`",
        f"FinalQA100 request JSONL: `{request_path}`",
        f"Sample count: {len(rows)}",
        "",
        "## Scope",
        "",
        "The finalQA100 audit evaluates whether Qwen Step3 is reliable enough as an auxiliary annotation source. It does not replace human final labels.",
        "",
        "## Required Metrics",
        "",
        "- rows",
        "- parse_ok_rate",
        "- schema_failed_count",
        "- invalid_enum_count",
        "- critical_false_keep",
        "- remove_false_keep",
        "- remove_capture",
        "- critical_capture",
        "- high_confidence_keep_precision_like",
        "- coverage_ok_recall_on_keep",
        "- guard downgrade count",
        "- top guarded blocking reasons",
        "",
        "## Candidate Order Perturbation Plan",
        "",
        "1. Build a deterministic perturbed copy of finalQA100 with candidate services/APIs reordered by a fixed seed.",
        "2. Run the same Step3 judge on both original and perturbed copies only after explicit permission.",
        "3. Compare semantic alignment, capability coverage, guard decisions, and high-confidence keep-like outputs.",
        "4. Treat unstable high-confidence keep decisions as a reliability risk.",
        "",
        "## Permissions",
        "",
        "- Do not call Qwen unless the user explicitly sets `ALLOW_QWEN_FINALQA100=true`.",
        "- Do not run Qwen full2168 as part of this protocol.",
        "- Do not put human final labels into Qwen prompts.",
        "- Do not print or save API keys.",
    ]
    path = DOC_DIR / "llm_judge_reliability_protocol_v0_1.md"
    write_md(path, lines)
    return path


def archive_outputs(paths: list[Path]) -> None:
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    for path in paths:
        if path.exists():
            shutil.copy2(path, ARCHIVE_DIR / path.name)


def main() -> int:
    parser = argparse.ArgumentParser(description="Post-final-QA closeout for final QA v1.5e.")
    parser.add_argument("--reviewed-csv", type=Path, default=FINAL_QA_DIR / "final_qa_review_items_v1_5e_gpt_manual_reviewed.csv")
    parser.add_argument("--original-review-csv", type=Path, default=FINAL_QA_DIR / "final_qa_review_items_v1_5e.csv")
    parser.add_argument("--request-output", type=Path, default=REQUEST_DIR / "qwen_step3_requests_finalqa100.jsonl")
    args = parser.parse_args()

    original_rows = read_csv(args.original_review_csv)
    reviewed_rows = read_csv(args.reviewed_csv)
    if len(reviewed_rows) != 100:
        raise ValueError(f"Expected 100 reviewed rows, got {len(reviewed_rows)}")
    if len(original_rows) != 100:
        raise ValueError(f"Expected 100 original review rows, got {len(original_rows)}")

    summary = completion_summary(reviewed_rows)
    invalid = invalid_values(reviewed_rows)
    missing = missing_by_field(reviewed_rows, HUMAN_FIELDS)
    taxonomy_counts, taxonomy_examples = taxonomy_summary(reviewed_rows)
    requests, label_leak_found = build_finalqa_requests(reviewed_rows)
    write_jsonl(args.request_output, requests)

    request_summary = {
        "generated_time": now_text(),
        "reviewed_csv": str(args.reviewed_csv),
        "original_review_csv": str(args.original_review_csv),
        "request_output": str(args.request_output),
        "row_count": len(reviewed_rows),
        "request_count": len(requests),
        "human_label_fields_in_request_payload": label_leak_found,
        "manual_field_missing_counts": missing,
        "invalid_values": invalid,
        "final_summary": summary,
        "qa_final_decision_distribution": dict(dist(reviewed_rows, "qa_final_decision")),
        "qa_severity_distribution": dict(dist(reviewed_rows, "qa_severity")),
        "qa_error_type_distribution": dict(dist(reviewed_rows, "qa_error_type")),
        "failure_taxonomy_counts": taxonomy_counts,
        "go_no_go": {
            "can_accept_final_qa_v1_5e_as_pass": False,
            "can_generate_final_clean_dataset_now": False,
            "can_run_qwen_full2168_next": "false until finalQA100 judge audit or explicit override",
            "recommended_next_step": "v1.5f policy tightening + finalQA100 Qwen reliability audit",
        },
    }
    summary_path = STEP3_FINALQA_DIR / "qwen_step3_finalqa100_request_build_summary.json"
    write_json(summary_path, request_summary)

    go_path = write_go_no_go(args.reviewed_csv, reviewed_rows, summary, invalid)
    taxonomy_path = write_failure_taxonomy(args.reviewed_csv, reviewed_rows, taxonomy_counts, taxonomy_examples)
    plan_path = write_policy_plan(args.reviewed_csv, reviewed_rows, taxonomy_counts)
    protocol_path = write_reliability_protocol(args.reviewed_csv, args.request_output, reviewed_rows)
    archive_outputs(
        [
            Path(__file__),
            args.reviewed_csv,
            args.request_output,
            summary_path,
            go_path,
            taxonomy_path,
            plan_path,
            protocol_path,
        ]
    )

    print(f"reviewed_rows: {len(reviewed_rows)}")
    print(f"requests: {len(requests)}")
    print(f"human_label_fields_in_request_payload: {label_leak_found}")
    print(f"qa_final_decision_distribution: {dict(dist(reviewed_rows, 'qa_final_decision'))}")
    print(f"qa_severity_distribution: {dict(dist(reviewed_rows, 'qa_severity'))}")
    print(f"request_output: {args.request_output}")
    print(f"summary_json: {summary_path}")
    print(f"archive_dir: {ARCHIVE_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
