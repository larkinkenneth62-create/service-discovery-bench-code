from __future__ import annotations

import csv
import hashlib
import json
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence


OUTPUT_DIR = Path("outputs/final_qa_v1_5e")
ANALYSIS_DIR = OUTPUT_DIR / "analysis"
DOC_DIR = Path("docs/phase1")

V14C_DIR = Path("outputs/full_clean_dryrun_v1_4c")
V14C_TASK_TRACE = V14C_DIR / "full_clean_task_trace_v1_4c.csv"
V14C_CANDIDATE_TRACE = V14C_DIR / "full_clean_candidate_trace_v1_4c.csv"
V14C_SUMMARY = V14C_DIR / "full_clean_dryrun_summary_v1_4c.json"
V14C_GO_NO_GO = DOC_DIR / "full_clean_dryrun_v1_4c_go_no_go_report.md"
V14C_LABELS = V14C_DIR / "v1_5d_failed_clean_candidate_labels_v1_4c.json"

V14B_DEDUP_TRACE = Path("outputs/full_clean_dryrun_v1_4b/dedup_precheck/dryrun_clean_candidate_dedup_trace_v1_4b.csv")

V15D_TAXONOMY = DOC_DIR / "final_qa_v1_5d_failure_taxonomy.md"
V15D_REVIEW_SET = Path("outputs/final_qa_v1_5d/final_qa_review_items_v1_5d.csv")
V15D_MERGED = Path("outputs/final_qa_v1_5d/analysis/final_qa_review_items_v1_5d_merged.csv")
V15D_ANALYSIS_REPORT = DOC_DIR / "final_qa_analysis_report_v1_5d.md"
V15D_GO_NO_GO_FOR_V16 = DOC_DIR / "final_qa_v1_5d_go_no_go_for_v1_6.md"

SEMCAP_V13_DOC = DOC_DIR / "semcap_v1_3_tightening_rules_candidate.md"
POLICY_V14C_DOC = DOC_DIR / "policy_v1_4c_tightening_plan.md"
SEMCAP_V12_DOC = DOC_DIR / "semcap_v1_2_tightening_rules_candidate.md"
MANUAL_V42_DOC = DOC_DIR / "manual_audit_rule_v4_2_candidate.md"

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
    "v1_4c_dryrun_decision",
    "v1_4c_dryrun_bucket",
    "v1_4c_clean_confidence_bucket",
    "v1_4c_blocking_reasons",
    "v1_4c_warning_reasons",
    "v1_4c_triggered_rules",
    "api_leak_detector_status",
    "service_leak_detector_status",
    "v13_semantic_alignment_pred",
    "v13_semantic_alignment_confidence",
    "v13_capability_coverage_pred",
    "v13_capability_coverage_confidence",
    "v13_capability_coverage_reason",
    "v13_core_requirements_json",
    "v13_covered_requirements_json",
    "v13_missing_requirements_json",
    "v13_gold_set_integrity_status",
    "v13_extra_gold_service_flags_json",
    "v13_generic_search_overtrust_flag",
    "v13_domain_specific_guard_flags_json",
    "v13_tightening_triggered_rules_json",
    "dedup_group_id",
    "dedup_group_size",
    "is_representative_candidate",
    "qa_sampling_reason",
    "risk_keywords_matched",
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
        "wrong_bucket",
        "unclear",
    ],
    "qa_severity": ["", "none", "minor", "major", "critical"],
}

QUOTAS = {
    "random_clean_candidate": 20,
    "generic_search_news_image_risk": 15,
    "travel_place_hotel_restaurant_venue_risk": 15,
    "finance_currency_crypto_stock_risk": 15,
    "weather_location_geocoding_risk": 10,
    "social_media_profile_content_risk": 10,
    "G3_or_composable_raw_risk": 10,
    "duplicate_or_near_duplicate_risk": 5,
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


def joined_text(row: dict[str, str]) -> str:
    return " ".join(
        str(row.get(field, "") or "").lower()
        for field in [
            "query_text",
            "candidate_services_json",
            "candidate_apis_json",
            "gold_services_json",
            "gold_apis_json",
            "task_type",
            "source_group",
        ]
    )


def matched_keywords(text: str, terms: Sequence[str]) -> list[str]:
    return [term for term in terms if term in text]


RISK_TERMS = {
    "generic_search_news_image_risk": ["search", "news", "image", "photo", "logo", "article", "web search", "book cover", "video", "tts", "voice"],
    "travel_place_hotel_restaurant_venue_risk": ["travel", "hotel", "restaurant", "venue", "nearby", "place", "attraction", "grocery", "gas station", "vacation"],
    "finance_currency_crypto_stock_risk": ["currency", "exchange rate", "crypto", "bitcoin", "stock", "finance", "forex", "market", "earnings"],
    "weather_location_geocoding_risk": ["weather", "forecast", "location", "geocode", "address", "coordinates", "air quality", "current conditions"],
    "social_media_profile_content_risk": ["social", "instagram", "tiktok", "twitter", "profile", "feed", "influencer", "media", "video posts"],
}


def infer_risk_subbucket(row: dict[str, str]) -> tuple[str, list[str]]:
    text = joined_text(row)
    if row.get("dedup_group_id"):
        return "duplicate_or_near_duplicate_risk", ["dedup_group_id"]
    if row.get("source_group") == "G3" or "composable" in row.get("task_type", "").lower():
        return "G3_or_composable_raw_risk", ["G3_or_composable"]
    for subbucket, terms in RISK_TERMS.items():
        hits = matched_keywords(text, terms)
        if hits:
            return subbucket, hits
    return "random_clean_candidate", []


def normalize_review_row(row: dict[str, str], item_index: int, subbucket: str, reason: str, keywords: Sequence[str]) -> dict[str, Any]:
    out = {
        "qa_item_id": f"FQA-1.5E-{item_index:03d}",
        "qa_bucket": "current_clean_candidate_audit",
        "qa_subbucket": subbucket,
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
        "v1_4c_dryrun_decision": row.get("dryrun_decision_v1_4c", ""),
        "v1_4c_dryrun_bucket": row.get("dryrun_bucket_v1_4c", ""),
        "v1_4c_clean_confidence_bucket": row.get("clean_confidence_bucket_v1_4c", ""),
        "v1_4c_blocking_reasons": row.get("blocking_reasons_v1_4c", ""),
        "v1_4c_warning_reasons": row.get("warning_reasons_v1_4c", ""),
        "v1_4c_triggered_rules": row.get("triggered_rules_v1_4c", ""),
        "api_leak_detector_status": row.get("api_leak_detector_status", ""),
        "service_leak_detector_status": row.get("service_leak_detector_status", ""),
        "v13_semantic_alignment_pred": row.get("v12_semantic_alignment_pred", ""),
        "v13_semantic_alignment_confidence": row.get("v12_semantic_alignment_confidence", ""),
        "v13_capability_coverage_pred": row.get("v12_capability_coverage_pred", ""),
        "v13_capability_coverage_confidence": row.get("v12_capability_coverage_confidence", ""),
        "v13_capability_coverage_reason": row.get("v12_capability_coverage_reason", ""),
        "v13_core_requirements_json": row.get("v12_core_requirements_json", ""),
        "v13_covered_requirements_json": row.get("v12_covered_requirements_json", ""),
        "v13_missing_requirements_json": row.get("v12_missing_requirements_json", ""),
        "v13_gold_set_integrity_status": row.get("v12_gold_set_integrity_status", ""),
        "v13_extra_gold_service_flags_json": row.get("v12_extra_gold_service_flags_json", ""),
        "v13_generic_search_overtrust_flag": row.get("v12_generic_search_overtrust_flag", ""),
        "v13_domain_specific_guard_flags_json": row.get("v1_4c_domain_specific_guard_flags_json") or row.get("v12_domain_specific_guard_flags_json", ""),
        "v13_tightening_triggered_rules_json": row.get("triggered_rules_v1_4c", row.get("v12_tightening_triggered_rules_json", "")),
        "dedup_group_id": row.get("dedup_group_id", ""),
        "dedup_group_size": row.get("dedup_group_size", ""),
        "is_representative_candidate": row.get("is_dedup_representative", row.get("is_representative_candidate", "")),
        "qa_sampling_reason": reason,
        "risk_keywords_matched": ";".join(keywords),
    }
    for field in QA_HUMAN_FIELDS:
        out[field] = ""
    return out


def load_v15d_failed_task_ids() -> set[str]:
    labels = load_json(V14C_LABELS)
    task_ids = labels.get("task_ids")
    if isinstance(task_ids, list) and task_ids:
        return {str(task_id) for task_id in task_ids if task_id}
    if not V15D_REVIEW_SET.exists():
        return set()
    failed_ids = set(labels.get("failed_qa_ids", []))
    rows = read_csv(V15D_REVIEW_SET)
    return {row.get("task_id", "") for row in rows if row.get("qa_item_id", "") in failed_ids and row.get("task_id")}


def archive_v1_5e(root: Path) -> list[str]:
    archive_dir = root / "outputs" / "run_archives" / f"{datetime.now().strftime('%Y-%m-%d')}_final_qa_v1_5e"
    ensure_dir(archive_dir)
    paths = [
        Path("scripts/validation/final_qa_v1_5e_common.py"),
        Path("scripts/validation/check_final_qa_v1_5e_inputs.py"),
        Path("scripts/validation/build_final_qa_review_set_v1_5e.py"),
        Path("scripts/validation/build_final_qa_review_app_v1_5e.py"),
        Path("scripts/validation/merge_and_analyze_final_qa_v1_5e.py"),
        OUTPUT_DIR,
        DOC_DIR / "final_qa_review_protocol_v1_5e.md",
        DOC_DIR / "final_qa_v1_5e_go_no_go_report.md",
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
            "# Final QA v1.5e Archive Manifest",
            "",
            f"Generated time: {now_text()}",
            f"Archive directory: `{archive_dir}`",
            "",
            "This archive contains the 100-row v1.5e clean-candidate QA package only.",
            "No final clean dataset, split, baseline, model training, v1.4b/v1.4c overwrite, or automatic QA human labels were produced.",
            "",
            "## Archived Files",
            "",
            *[f"- `{path}`" for path in copied],
        ],
    )
    return copied
