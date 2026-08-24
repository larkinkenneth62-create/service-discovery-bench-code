from __future__ import annotations

import csv
import hashlib
import json
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence


OUTPUT_DIR = Path("outputs/final_qa_v1_5d")
ANALYSIS_DIR = OUTPUT_DIR / "analysis"
DOC_DIR = Path("docs/phase1")

V14B_DIR = Path("outputs/full_clean_dryrun_v1_4b")
V14B_SUMMARY = V14B_DIR / "full_clean_dryrun_summary_v1_4b.json"
V14B_TASK_TRACE = V14B_DIR / "full_clean_task_trace_v1_4b.csv"
V14B_CANDIDATE_TRACE = V14B_DIR / "full_clean_candidate_trace_v1_4b.csv"
V14B_DANGEROUS_SUMMARY = V14B_DIR / "dangerous_error_summary_v1_4b.json"
V14B_GO_NO_GO_REPORT = DOC_DIR / "full_clean_dryrun_v1_4b_go_no_go_report.md"
V14B_QA_FRAME = V14B_DIR / "qa/impacted_clean_candidate_qa_frame_v1_4b.csv"
V14B_QA_PLAN = V14B_DIR / "qa/impacted_clean_candidate_qa_plan_v1_4b.md"
V14B_DEDUP_TRACE = V14B_DIR / "dedup_precheck/dryrun_clean_candidate_dedup_trace_v1_4b.csv"
V14B_DEDUP_SUMMARY = V14B_DIR / "dedup_precheck/dedup_precheck_summary_v1_4b.json"

V14B_BUCKET_DIR = V14B_DIR / "task_buckets"
V14B_CLEAN_BUCKET = V14B_BUCKET_DIR / "dryrun_clean_candidate_task_level_v1_4b.csv"
V14B_CLEAN_HIGH_CONF_BUCKET = V14B_BUCKET_DIR / "dryrun_clean_candidate_high_conf_task_level_v1_4b.csv"
V14B_REMOVED_BUCKET = V14B_BUCKET_DIR / "dryrun_removed_task_level_v1_4b.csv"
V14B_UNCERTAIN_BUCKET = V14B_BUCKET_DIR / "dryrun_uncertain_task_level_v1_4b.csv"
V14B_SERVICE_LEAK_BUCKET = V14B_BUCKET_DIR / "dryrun_service_leak_only_task_level_v1_4b.csv"

V15C_FAILURE_PATCH = Path("outputs/final_qa_v1_5c/final_qa_clean_candidate_failure_patch_v1_5c.csv")
V15C_FAILURE_TAXONOMY_DOC = DOC_DIR / "final_qa_clean_candidate_failure_taxonomy_v1_5c.md"
V15C_SEMCAP_RULE_DOC = DOC_DIR / "semcap_v1_2_tightening_rules_candidate.md"
V15C_POLICY_PLAN_DOC = DOC_DIR / "policy_tightening_plan_v1_5c.md"
V15_PROTOCOL_DOC = DOC_DIR / "final_qa_review_protocol_v1_5.md"

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

QA_BASE_FIELDS = [
    "qa_item_id",
    "qa_bucket",
    "qa_subbucket",
    "expected_current_status",
    "task_id",
    "task_type",
    "source_dataset",
    "source_group",
    "prediction_level",
    "query_text",
    "candidate_services_json",
    "candidate_apis_json",
    "gold_services_json",
    "gold_apis_json",
    "candidate_service_count",
    "gold_service_count",
    "candidate_api_count",
    "gold_api_count",
    "v1_4b_dryrun_decision",
    "v1_4b_dryrun_bucket",
    "v1_4b_clean_confidence_bucket",
    "v1_4b_blocking_reasons",
    "v1_4b_warning_reasons",
    "v1_4b_triggered_rules",
    "api_leak_detector_status",
    "service_leak_detector_status",
    "v12_semantic_alignment_pred",
    "v12_semantic_alignment_confidence",
    "v12_capability_coverage_pred",
    "v12_capability_coverage_confidence",
    "v12_capability_coverage_reason",
    "v12_core_requirements_json",
    "v12_covered_requirements_json",
    "v12_missing_requirements_json",
    "v12_gold_set_integrity_status",
    "v12_extra_gold_service_flags_json",
    "v12_generic_search_overtrust_flag",
    "v12_domain_specific_guard_flags_json",
    "v12_tightening_triggered_rules_json",
    "dedup_group_id",
    "dedup_group_size",
    "is_representative_candidate",
    "previous_v1_5c_qa_severity",
    "previous_v1_5c_failure_type",
    "previous_v1_5c_reason",
]

QA_OUTPUT_FIELDS = QA_BASE_FIELDS + QA_HUMAN_FIELDS

QA_FIELD_OPTIONS = {
    "qa_final_decision": ["", "pass", "fail", "uncertain"],
    "qa_semantic_alignment_check": ["", "ok", "uncertain", "mismatch", "not_applicable"],
    "qa_capability_coverage_check": ["", "coverage_ok", "coverage_uncertain", "coverage_mismatch", "not_applicable"],
    "qa_leakage_check": ["", "no_blocking", "api_leak_blocking", "service_leak_only", "ambiguous"],
    "qa_candidate_validity_check": ["", "valid", "insufficient_choice_space", "uncertain", "not_applicable"],
    "qa_task_type_check": ["", "valid", "invalid", "uncertain", "not_applicable"],
    "qa_dedup_check": ["", "unique", "duplicate_ok", "duplicate_should_remove", "uncertain"],
    "qa_error_type": [
        "",
        "none",
        "api_leak",
        "service_leak",
        "choice_space_invalid",
        "gold_missing",
        "semantic_mismatch",
        "capability_mismatch",
        "wrong_gold_set",
        "generic_search_overtrust",
        "domain_specific_gap",
        "duplicate_issue",
        "regression_not_fixed",
        "wrong_bucket",
        "unclear",
    ],
    "qa_severity": ["", "none", "minor", "major", "critical"],
}


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


def count_csv_rows(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        next(reader, None)
        return sum(1 for _ in reader)


def csv_schema(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "path": str(path), "row_count": 0, "columns": []}
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        columns = list(reader.fieldnames or [])
    return {"exists": True, "path": str(path), "row_count": count_csv_rows(path), "columns": columns}


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def write_json(path: Path, payload: object) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def write_md(path: Path, lines: Sequence[str]) -> None:
    ensure_dir(path.parent)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")


def stable_score(*parts: str) -> int:
    text = "||".join(str(part or "") for part in parts)
    return int(hashlib.md5(text.encode("utf-8")).hexdigest(), 16)


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


def json_preview(value: Any, limit: int = 1400) -> str:
    data = parse_jsonish(value)
    text = json.dumps(data, ensure_ascii=False, indent=2) if data else str(value or "")
    return text if len(text) <= limit else text[:limit] + "\n..."


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def normalize_review_row(row: dict[str, str], item_index: int, qa_bucket: str, qa_subbucket: str, expected: str) -> dict[str, Any]:
    out = {
        "qa_item_id": f"FQA-1.5D-{item_index:03d}",
        "qa_bucket": qa_bucket,
        "qa_subbucket": qa_subbucket,
        "expected_current_status": expected,
        "task_id": row.get("task_id", ""),
        "task_type": row.get("task_type", ""),
        "source_dataset": row.get("source_dataset", ""),
        "source_group": row.get("source_group", ""),
        "prediction_level": row.get("prediction_level", ""),
        "query_text": row.get("query_text", ""),
        "candidate_services_json": row.get("candidate_services_json", ""),
        "candidate_apis_json": row.get("candidate_apis_json", ""),
        "gold_services_json": row.get("gold_services_json", ""),
        "gold_apis_json": row.get("gold_apis_json", ""),
        "candidate_service_count": row.get("candidate_service_count", ""),
        "gold_service_count": row.get("gold_service_count", ""),
        "candidate_api_count": row.get("candidate_api_count", ""),
        "gold_api_count": row.get("gold_api_count", ""),
        "v1_4b_dryrun_decision": row.get("dryrun_decision_v1_4b", row.get("v1_4b_dryrun_decision", "")),
        "v1_4b_dryrun_bucket": row.get("dryrun_bucket_v1_4b", row.get("v1_4b_dryrun_bucket", "")),
        "v1_4b_clean_confidence_bucket": row.get("clean_confidence_bucket_v1_4b", row.get("v1_4b_clean_confidence_bucket", "")),
        "v1_4b_blocking_reasons": row.get("blocking_reasons_v1_4b", row.get("v1_4b_blocking_reasons", "")),
        "v1_4b_warning_reasons": row.get("warning_reasons_v1_4b", row.get("v1_4b_warning_reasons", "")),
        "v1_4b_triggered_rules": row.get("triggered_rules_v1_4b", row.get("v1_4b_triggered_rules", "")),
        "api_leak_detector_status": row.get("api_leak_detector_status", ""),
        "service_leak_detector_status": row.get("service_leak_detector_status", ""),
        "v12_semantic_alignment_pred": row.get("v12_semantic_alignment_pred", ""),
        "v12_semantic_alignment_confidence": row.get("v12_semantic_alignment_confidence", ""),
        "v12_capability_coverage_pred": row.get("v12_capability_coverage_pred", ""),
        "v12_capability_coverage_confidence": row.get("v12_capability_coverage_confidence", ""),
        "v12_capability_coverage_reason": row.get("v12_capability_coverage_reason", ""),
        "v12_core_requirements_json": row.get("v12_core_requirements_json", ""),
        "v12_covered_requirements_json": row.get("v12_covered_requirements_json", ""),
        "v12_missing_requirements_json": row.get("v12_missing_requirements_json", ""),
        "v12_gold_set_integrity_status": row.get("v12_gold_set_integrity_status", ""),
        "v12_extra_gold_service_flags_json": row.get("v12_extra_gold_service_flags_json", ""),
        "v12_generic_search_overtrust_flag": row.get("v12_generic_search_overtrust_flag", ""),
        "v12_domain_specific_guard_flags_json": row.get("v12_domain_specific_guard_flags_json", ""),
        "v12_tightening_triggered_rules_json": row.get("v12_tightening_triggered_rules_json", ""),
        "dedup_group_id": row.get("dedup_group_id", ""),
        "dedup_group_size": row.get("dedup_group_size", ""),
        "is_representative_candidate": row.get("is_dedup_representative", row.get("is_representative_candidate", "")),
        "previous_v1_5c_qa_severity": row.get("previous_v1_5c_qa_severity", ""),
        "previous_v1_5c_failure_type": row.get("previous_v1_5c_failure_type", ""),
        "previous_v1_5c_reason": row.get("previous_v1_5c_reason", ""),
    }
    for field in QA_HUMAN_FIELDS:
        out[field] = ""
    return out


def infer_clean_subbucket(row: dict[str, str]) -> str:
    text = " ".join(
        [
            row.get("query_text", ""),
            row.get("candidate_services_json", ""),
            row.get("candidate_apis_json", ""),
            row.get("gold_services_json", ""),
            row.get("gold_apis_json", ""),
            row.get("v12_core_requirements_json", ""),
        ]
    ).lower()
    task_type = row.get("task_type", "").lower()
    source_group = row.get("source_group", "")
    if source_group == "G3" or "composable" in task_type:
        return "G3_or_composable_raw_risk"
    if any(term in text for term in ["weather", "forecast", "temperature", "precipitation", "wind speed"]):
        return "weather_or_forecast_risk"
    if any(term in text for term in ["translate", "translation", "language", "dictionary", "phrase", "word"]):
        return "translation_or_language_risk"
    if any(term in text for term in ["domain", "finance", "exchange rate", "currency", "forex", "stock", "ipo"]):
        return "domain_availability_or_finance_rate_risk"
    if any(term in text for term in ["travel", "hotel", "restaurant", "nearby", "place", "attraction", "grocery", "gas station"]):
        return "travel_place_hotel_restaurant_risk"
    if any(term in text for term in ["search", "news", "image", "photo", "logo", "entity", "autosuggest", "article"]):
        return "generic_search_or_news_or_image_risk"
    return "random_clean_candidate"


def archive_v1_5d(root: Path) -> list[str]:
    archive_dir = root / "outputs" / "run_archives" / f"{datetime.now().strftime('%Y-%m-%d')}_final_qa_v1_5d"
    ensure_dir(archive_dir)
    paths = [
        Path("scripts/validation/final_qa_v1_5d_common.py"),
        Path("scripts/validation/check_final_qa_v1_5d_inputs.py"),
        Path("scripts/validation/build_final_qa_review_set_v1_5d.py"),
        Path("scripts/validation/build_final_qa_review_app_v1_5d.py"),
        Path("scripts/validation/merge_and_analyze_final_qa_v1_5d.py"),
        OUTPUT_DIR,
        DOC_DIR / "final_qa_review_protocol_v1_5d.md",
        DOC_DIR / "final_qa_v1_5d_go_no_go_report.md",
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
            "# Final QA v1.5d Archive Manifest",
            "",
            f"Generated time: {now_text()}",
            f"Archive directory: `{archive_dir}`",
            "",
            "This archive contains the impacted clean-candidate QA package only.",
            "No final clean dataset, split, baseline, model training, v1.4/v1.4b overwrite, or automatic QA human labels were produced.",
            "",
            "## Archived Files",
            "",
            *[f"- `{path}`" for path in copied],
        ],
    )
    return copied
