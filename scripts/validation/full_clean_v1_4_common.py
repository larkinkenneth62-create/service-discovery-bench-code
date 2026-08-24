from __future__ import annotations

import csv
import json
import re
import shutil
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple


OUTPUT_DIR = Path("outputs/full_clean_dryrun_v1_4")
TASK_BUCKET_DIR = OUTPUT_DIR / "task_buckets"
DEDUP_DIR = OUTPUT_DIR / "dedup_precheck"
DOC_DIR = Path("docs/phase1")

RAW_TASK = Path("outputs/toolbench_full_raw_streaming_v1_3/full/toolbench_full_task_level_raw.csv")
RAW_CANDIDATE = Path("outputs/toolbench_full_raw_streaming_v1_3/full/toolbench_full_candidate_level_raw.csv")
V4_2_POLICY = Path("docs/phase1/manual_audit_rule_v4_2_candidate.md")
V1_3_REPORTS = [
    Path("docs/phase1/toolbench_full_raw_validation_report_v1_3.md"),
    Path("docs/phase1/toolbench_full_raw_count_diff_report_v1_3.md"),
    Path("docs/phase1/toolbench_full_raw_datacard_v1_3.md"),
    Path("docs/phase1/toolbench_full_raw_streaming_v1_3_go_no_go_report.md"),
]
SEMCAP_SCRIPT = Path("scripts/validation/run_semcap_heuristic_detector_v1_1.py")
SEMCAP_RULE_DOC = Path("docs/phase1/semantic_capability_detector_v1_rule_candidate.md")
SEMCAP_EVAL_REPORT = Path("docs/phase1/semcap_detector_v1_1_eval_report.md")
V1_2_DANGER = Path("outputs/small_cleaning_dryrun_v1_2/dangerous_error_summary_v1_2.json")
V1_2_GO_NO_GO = Path("docs/phase1/small_cleaning_dryrun_v1_2_go_no_go_report.md")

RAW_TASK_REQUIRED = [
    "task_id",
    "task_type",
    "source_dataset",
    "source_group",
    "source_query_id",
    "query_text",
    "candidate_services_json",
    "candidate_apis_json",
    "gold_services_json",
    "gold_apis_json",
    "candidate_service_count",
    "gold_service_count",
    "candidate_api_count",
    "gold_api_count",
    "query_mentions_any_gold_api",
    "query_mentions_any_gold_service",
    "task_signature",
    "query_signature",
    "metadata_json",
]

RAW_CANDIDATE_REQUIRED = [
    "candidate_row_id",
    "task_id",
    "task_type",
    "source_dataset",
    "source_group",
    "query_text",
    "candidate_service_name",
    "candidate_api_name",
    "candidate_service_description",
    "candidate_api_description",
    "candidate_category",
    "is_gold_service",
    "is_gold_api",
]

DETECTOR_FIELDS_EXTRA = [
    "gold_in_candidate_services",
    "gold_in_candidate_apis",
    "prediction_level",
    "candidate_space_status",
    "task_type_eligibility_status",
    "api_leak_detector_status",
    "api_leak_strength",
    "api_leak_matched_terms",
    "api_leak_reason",
    "service_leak_detector_status",
    "service_leak_matched_terms",
    "service_leak_reason",
    "detector_parse_error",
]

SEMCAP_FIELDS_EXTRA = [
    "v1_semantic_alignment_pred",
    "v1_semantic_alignment_confidence",
    "v1_semantic_alignment_reason",
    "v1_semantic_mismatch_type",
    "v1_capability_coverage_pred",
    "v1_capability_coverage_confidence",
    "v1_core_requirements_json",
    "v1_covered_requirements_json",
    "v1_missing_requirements_json",
    "v1_capability_mismatch_type",
    "v1_capability_coverage_reason",
    "v1_coverage_ok_but_policy_blocked_candidate",
    "requires_human_review_v1",
]

POLICY_FIELDS_EXTRA = [
    "dryrun_decision",
    "dryrun_bucket",
    "blocking_reasons",
    "warning_reasons",
    "triggered_rules",
    "is_dryrun_clean_candidate",
    "is_dryrun_removed",
    "is_dryrun_uncertain",
    "is_dryrun_service_leak_only",
    "clean_confidence_bucket",
    "requires_final_qa",
]

GENERIC_API_WORDS = {
    "search",
    "status",
    "summary",
    "detail",
    "details",
    "count",
    "latest",
    "health",
    "places",
    "place",
    "image",
    "images",
    "news",
    "subtitle",
    "json",
    "srt",
    "text",
    "format",
    "list",
    "all",
    "get",
    "api",
    "project",
    "projects",
}

GENERIC_SERVICE_WORDS = {
    "search",
    "weather",
    "news",
    "images",
    "image",
    "companies",
    "company",
    "data",
    "api",
    "tracking",
    "tools",
}


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def write_md(path: Path, lines: Sequence[str]) -> None:
    ensure_dir(path.parent)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")


def write_json(path: Path, payload: object) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def open_csv_writer(path: Path, fieldnames: Sequence[str]) -> tuple[Any, csv.DictWriter]:
    ensure_dir(path.parent)
    f = path.open("w", encoding="utf-8-sig", newline="")
    writer = csv.DictWriter(f, fieldnames=list(fieldnames), extrasaction="ignore")
    writer.writeheader()
    return f, writer


def append_csv_row(writer: csv.DictWriter, row: Dict[str, Any], fieldnames: Sequence[str]) -> None:
    writer.writerow({field: row.get(field, "") for field in fieldnames})


def norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def parse_json_list(value: Any) -> list:
    try:
        data = json.loads(str(value or "[]"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def to_int(value: Any) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return 0


def truthy_yes(value: Any) -> bool:
    return norm(value) in {"yes", "true", "1"}


def value_counter(rows: Iterable[Dict[str, Any]], field: str) -> Dict[str, int]:
    return dict(Counter((str(row.get(field, "")) or "<blank>").strip() or "<blank>" for row in rows))


def table_lines(counter: Dict[str, int]) -> List[str]:
    lines = ["| value | count |", "|---|---|"]
    for key, count in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"| {key} | {count} |")
    return lines


def is_camel_or_endpoint(name: str) -> bool:
    raw = str(name or "")
    return bool(re.search(r"[/_:{}]", raw) or re.search(r"[a-z][A-Z]", raw))


def query_mentions(query: str, term: str) -> bool:
    q = norm(query)
    t = norm(term)
    return bool(t and len(t) >= 3 and t in q)


def prediction_level(row: Dict[str, Any]) -> str:
    task_type = norm(row.get("task_type"))
    if "_api_" in task_type or "api_recommendation" in task_type:
        return "api"
    return "service"


def validate_gold_in_candidate(row: Dict[str, Any]) -> tuple[str, str]:
    candidate_services = parse_json_list(row.get("candidate_services_json"))
    candidate_apis = parse_json_list(row.get("candidate_apis_json"))
    gold_services = parse_json_list(row.get("gold_services_json"))
    gold_apis = parse_json_list(row.get("gold_apis_json"))
    candidate_service_names = {norm(item.get("service_name")) for item in candidate_services if isinstance(item, dict)}
    candidate_api_keys = {(norm(item.get("service_name")), norm(item.get("api_name"))) for item in candidate_apis if isinstance(item, dict)}
    candidate_api_names = {norm(item.get("api_name")) for item in candidate_apis if isinstance(item, dict)}
    gold_service_names = {norm(item) for item in gold_services}
    gold_api_keys = {(norm(item.get("service_name")), norm(item.get("api_name"))) for item in gold_apis if isinstance(item, dict)}
    gold_api_names = {norm(item.get("api_name")) for item in gold_apis if isinstance(item, dict)}
    service_status = "yes" if gold_service_names and gold_service_names.issubset(candidate_service_names) else "no"
    api_status = "yes" if gold_api_keys and gold_api_keys.issubset(candidate_api_keys) else "no"
    if api_status == "no" and gold_api_names and gold_api_names.issubset(candidate_api_names):
        api_status = "yes_api_name_only"
    if not gold_services:
        service_status = "unknown"
    if not gold_apis:
        api_status = "unknown"
    return service_status, api_status


def candidate_space_status(row: Dict[str, Any], level: str) -> str:
    if level == "api":
        return "valid_api_choice_space" if to_int(row.get("candidate_api_count")) > max(1, to_int(row.get("gold_api_count"))) else "invalid_api_no_choice_space"
    return "valid_service_choice_space" if to_int(row.get("candidate_service_count")) > max(1, to_int(row.get("gold_service_count"))) else "invalid_service_no_choice_space"


def task_type_eligibility(row: Dict[str, Any], level: str) -> str:
    task_type = norm(row.get("task_type"))
    if level == "api":
        return "valid_api_level" if "api" in task_type else "invalid_task_type_not_api_level"
    return "valid_service_level" if "service" in task_type else "invalid_task_type_not_service_level"


def detect_api_leak(row: Dict[str, Any]) -> tuple[str, str, str, str]:
    query = row.get("query_text", "")
    gold_apis = parse_json_list(row.get("gold_apis_json"))
    strong: list[str] = []
    weak: list[str] = []
    for item in gold_apis:
        if not isinstance(item, dict):
            continue
        api_name = str(item.get("api_name", "") or "")
        if not query_mentions(query, api_name):
            continue
        api_norm = norm(api_name)
        tokens = [tok for tok in re.split(r"[^a-z0-9]+", api_norm) if tok]
        if api_norm in GENERIC_API_WORDS or all(tok in GENERIC_API_WORDS for tok in tokens):
            weak.append(api_name)
        elif is_camel_or_endpoint(api_name) or len(api_norm) >= 4:
            strong.append(api_name)
        else:
            weak.append(api_name)
    if strong:
        return "api_leak_blocking", "strong", "; ".join(sorted(set(strong))), "gold API endpoint/name appears in query"
    if weak:
        return "api_leak_weak_or_generic", "weak_or_generic", "; ".join(sorted(set(weak))), "generic API-name mention"
    return "no_blocking_api_leak", "none", "", "no gold API name detected in query"


def detect_service_leak(row: Dict[str, Any]) -> tuple[str, str, str]:
    query = row.get("query_text", "")
    gold_services = parse_json_list(row.get("gold_services_json"))
    strong: list[str] = []
    ambiguous: list[str] = []
    for service in gold_services:
        service_name = str(service or "")
        if not query_mentions(query, service_name):
            continue
        if norm(service_name) in GENERIC_SERVICE_WORDS:
            ambiguous.append(service_name)
        else:
            strong.append(service_name)
    if strong:
        return "service_leak_only", "; ".join(sorted(set(strong))), "gold service name appears in query"
    if ambiguous:
        return "ambiguous_service_leak", "; ".join(sorted(set(ambiguous))), "generic service word appears in query"
    return "no_service_leak", "", "no gold service name detected in query"


def deterministic_detect(row: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(row)
    parse_error = ""
    try:
        for field in ["candidate_services_json", "candidate_apis_json", "gold_services_json", "gold_apis_json"]:
            parse_json_list(row.get(field))
        service_gold, api_gold = validate_gold_in_candidate(row)
    except Exception as exc:
        service_gold, api_gold = "unknown", "unknown"
        parse_error = f"{type(exc).__name__}: {exc}"
    level = prediction_level(row)
    api_status, api_strength, api_terms, api_reason = detect_api_leak(row)
    service_status, service_terms, service_reason = detect_service_leak(row)
    out.update(
        {
            "gold_in_candidate_services": service_gold,
            "gold_in_candidate_apis": api_gold,
            "prediction_level": level,
            "candidate_space_status": candidate_space_status(row, level),
            "task_type_eligibility_status": task_type_eligibility(row, level),
            "api_leak_detector_status": api_status,
            "api_leak_strength": api_strength,
            "api_leak_matched_terms": api_terms,
            "api_leak_reason": api_reason,
            "service_leak_detector_status": service_status,
            "service_leak_matched_terms": service_terms,
            "service_leak_reason": service_reason,
            "detector_parse_error": parse_error,
        }
    )
    return out


def semcap_predict(row: Dict[str, Any]) -> Dict[str, Any]:
    scripts_dir = Path(__file__).resolve().parent
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from semcap_v1_1_common import run_semcap_v1_detector

    pred = run_semcap_v1_detector(row, record_id=row.get("task_id", ""))
    out = dict(row)
    out.update(
        {
            "v1_semantic_alignment_pred": pred.get("semantic_alignment_pred", ""),
            "v1_semantic_alignment_confidence": pred.get("semantic_alignment_confidence", ""),
            "v1_semantic_alignment_reason": pred.get("semantic_alignment_reason", ""),
            "v1_semantic_mismatch_type": pred.get("semantic_mismatch_type", ""),
            "v1_capability_coverage_pred": pred.get("capability_coverage_pred", ""),
            "v1_capability_coverage_confidence": pred.get("capability_coverage_confidence", ""),
            "v1_core_requirements_json": pred.get("core_requirements_json", "[]"),
            "v1_covered_requirements_json": pred.get("covered_requirements_json", "[]"),
            "v1_missing_requirements_json": pred.get("missing_requirements_json", "[]"),
            "v1_capability_mismatch_type": pred.get("capability_mismatch_type", ""),
            "v1_capability_coverage_reason": pred.get("capability_coverage_reason", ""),
            "v1_coverage_ok_but_policy_blocked_candidate": pred.get("coverage_ok_but_policy_blocked_candidate", ""),
            "requires_human_review_v1": pred.get("requires_human_review", ""),
        }
    )
    return out


def clean_confidence_bucket(row: Dict[str, Any]) -> str:
    if row.get("dryrun_decision") != "dryrun_clean_candidate":
        return ""
    if norm(row.get("v1_semantic_alignment_confidence")) == "high" and norm(row.get("v1_capability_coverage_confidence")) == "high":
        return "clean_candidate_high_conf"
    return "clean_candidate_medium_conf"


def policy_decide(row: Dict[str, Any]) -> Dict[str, Any]:
    level = norm(row.get("prediction_level"))
    blocking: list[str] = []
    warnings: list[str] = []
    rules: list[str] = []

    if norm(row.get("api_leak_detector_status")) == "api_leak_blocking" or norm(row.get("api_leak_strength")) == "strong":
        blocking.append("api_leak")
        rules.append("remove_blocking_api_leak")
    if row.get("gold_in_candidate_services") not in {"yes"} or row.get("gold_in_candidate_apis") not in {"yes", "yes_api_name_only"}:
        blocking.append("gold_missing")
        rules.append("remove_gold_missing")
    if norm(row.get("candidate_space_status")).startswith("invalid"):
        blocking.append("choice_space_invalid")
        rules.append("remove_choice_space_invalid")
    if norm(row.get("task_type_eligibility_status")).startswith("invalid"):
        blocking.append("task_type_invalid")
        rules.append("remove_task_type_invalid")
    if norm(row.get("v1_semantic_alignment_pred")) == "mismatch":
        blocking.append("semantic_mismatch")
        rules.append("remove_semantic_mismatch")
    if norm(row.get("v1_capability_coverage_pred")) == "coverage_mismatch":
        blocking.append("capability_mismatch")
        rules.append("remove_capability_mismatch")
    if row.get("detector_parse_error"):
        warnings.append("detector_parse_error")
        rules.append("uncertain_detector_parse_error")
    if norm(row.get("v1_semantic_alignment_pred")) == "uncertain":
        warnings.append("semantic_uncertain")
        rules.append("uncertain_semcap")
    if norm(row.get("v1_capability_coverage_pred")) == "coverage_uncertain":
        warnings.append("coverage_uncertain")
        rules.append("uncertain_semcap")
    if norm(row.get("api_leak_detector_status")) == "api_leak_weak_or_generic":
        warnings.append("weak_or_generic_api_leak")
        rules.append("uncertain_weak_api_leak")
    if norm(row.get("service_leak_detector_status")) == "ambiguous_service_leak":
        warnings.append("ambiguous_service_leak")
        rules.append("uncertain_ambiguous_service_leak")
    if not row.get("v1_semantic_alignment_pred") or not row.get("v1_capability_coverage_pred"):
        warnings.append("missing_semantic_capability")
        rules.append("uncertain_detector_parse_error")

    if blocking:
        priority = ["api_leak", "gold_missing", "choice_space_invalid", "task_type_invalid", "semantic_mismatch", "capability_mismatch"]
        primary = next((item for item in priority if item in blocking), blocking[0])
        bucket = {
            "api_leak": "removed_api_leak",
            "gold_missing": "removed_gold_missing",
            "choice_space_invalid": "removed_choice_space_invalid",
            "task_type_invalid": "removed_task_type_invalid",
            "semantic_mismatch": "removed_semantic_mismatch",
            "capability_mismatch": "removed_capability_mismatch",
        }[primary]
        decision = "dryrun_removed"
    elif level == "service" and norm(row.get("service_leak_detector_status")) == "service_leak_only":
        decision = "dryrun_service_leak_only"
        bucket = "dryrun_service_leak_only"
        rules.append("separate_service_level_service_leak")
    elif warnings:
        decision = "dryrun_uncertain"
        if "semantic_uncertain" in warnings or "coverage_uncertain" in warnings:
            bucket = "uncertain_semcap"
        elif "weak_or_generic_api_leak" in warnings:
            bucket = "uncertain_weak_api_leak"
        elif "ambiguous_service_leak" in warnings:
            bucket = "uncertain_ambiguous_service_leak"
        elif "detector_parse_error" in warnings:
            bucket = "uncertain_detector_parse_error"
        else:
            bucket = "dryrun_uncertain"
    elif norm(row.get("v1_semantic_alignment_pred")) == "ok" and norm(row.get("v1_capability_coverage_pred")) == "coverage_ok":
        decision = "dryrun_clean_candidate"
        bucket = "dryrun_clean_candidate"
        rules.append("keep_dryrun_clean_candidate")
    else:
        decision = "dryrun_uncertain"
        bucket = "uncertain_semcap"
        warnings.append("semcap_not_clean_ok")

    out = dict(row)
    out.update(
        {
            "dryrun_decision": decision,
            "dryrun_bucket": bucket,
            "blocking_reasons": ";".join(sorted(set(blocking))),
            "warning_reasons": ";".join(sorted(set(warnings))),
            "triggered_rules": ";".join(sorted(set(rules))),
            "is_dryrun_clean_candidate": str(decision == "dryrun_clean_candidate").lower(),
            "is_dryrun_removed": str(decision == "dryrun_removed").lower(),
            "is_dryrun_uncertain": str(decision == "dryrun_uncertain").lower(),
            "is_dryrun_service_leak_only": str(decision == "dryrun_service_leak_only").lower(),
            "requires_final_qa": "true",
        }
    )
    out["clean_confidence_bucket"] = clean_confidence_bucket(out)
    return out


def dangerous_flags(row: Dict[str, Any]) -> List[str]:
    if row.get("dryrun_decision") != "dryrun_clean_candidate":
        return []
    flags: list[str] = []
    if norm(row.get("api_leak_detector_status")) == "api_leak_blocking" or norm(row.get("api_leak_strength")) == "strong":
        flags.append("strong_or_blocking_api_leak_into_clean")
    if row.get("gold_in_candidate_services") != "yes" or row.get("gold_in_candidate_apis") not in {"yes", "yes_api_name_only"}:
        flags.append("gold_missing_into_clean")
    if norm(row.get("prediction_level")) == "service" and norm(row.get("candidate_space_status")).startswith("invalid_service"):
        flags.append("service_level_no_choice_space_into_clean")
    if norm(row.get("prediction_level")) == "api" and norm(row.get("candidate_space_status")).startswith("invalid_api"):
        flags.append("api_level_no_api_choice_space_into_clean")
    if norm(row.get("v1_semantic_alignment_pred")) == "mismatch":
        flags.append("semantic_mismatch_into_clean")
    if norm(row.get("v1_capability_coverage_pred")) == "coverage_mismatch":
        flags.append("capability_mismatch_into_clean")
    if norm(row.get("v1_semantic_alignment_pred")) == "uncertain":
        flags.append("semantic_uncertain_into_clean")
    if norm(row.get("v1_capability_coverage_pred")) == "coverage_uncertain":
        flags.append("coverage_uncertain_into_clean")
    if norm(row.get("prediction_level")) == "service" and norm(row.get("service_leak_detector_status")) == "service_leak_only":
        flags.append("service_level_service_leak_into_clean")
    if norm(row.get("task_type_eligibility_status")).startswith("invalid"):
        flags.append("task_type_invalid_into_clean")
    if not row.get("v1_semantic_alignment_pred") or not row.get("v1_capability_coverage_pred"):
        flags.append("missing_semantic_capability_into_clean")
    if row.get("detector_parse_error"):
        flags.append("detector_parse_error_into_clean")
    return sorted(set(flags))


def archive_v1_4(root: Path) -> List[str]:
    archive_dir = root / "outputs" / "run_archives" / f"{datetime.now().strftime('%Y-%m-%d')}_full_clean_dryrun_v1_4"
    ensure_dir(archive_dir)
    paths = [
        Path("scripts/validation/check_full_clean_dryrun_v1_4_inputs.py"),
        Path("scripts/validation/run_full_raw_detectors_v1_4.py"),
        Path("scripts/validation/run_full_raw_semcap_v1_4.py"),
        Path("scripts/validation/apply_full_clean_dryrun_policy_v1_4.py"),
        Path("scripts/validation/export_full_clean_dryrun_buckets_v1_4.py"),
        Path("scripts/validation/build_full_clean_candidate_trace_v1_4.py"),
        Path("scripts/validation/check_full_clean_dryrun_dangerous_errors_v1_4.py"),
        Path("scripts/validation/run_dedup_precheck_v1_4.py"),
        Path("scripts/validation/prepare_final_qa_sampling_frame_v1_4.py"),
        Path("scripts/validation/summarize_full_clean_dryrun_v1_4.py"),
        Path("scripts/validation/full_clean_v1_4_common.py"),
        OUTPUT_DIR,
        DOC_DIR / "full_raw_detector_report_v1_4.md",
        DOC_DIR / "full_raw_semcap_report_v1_4.md",
        DOC_DIR / "full_clean_task_trace_report_v1_4.md",
        DOC_DIR / "full_clean_candidate_trace_report_v1_4.md",
        DOC_DIR / "full_clean_dryrun_bucket_report_v1_4.md",
        DOC_DIR / "full_clean_dryrun_dangerous_error_report_v1_4.md",
        DOC_DIR / "dedup_precheck_report_v1_4.md",
        DOC_DIR / "final_qa_sampling_frame_report_v1_4.md",
        DOC_DIR / "full_clean_dryrun_summary_report_v1_4.md",
        DOC_DIR / "full_clean_dryrun_v1_4_go_no_go_report.md",
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
            "# Full Clean Dry-Run v1.4 Archive Manifest",
            "",
            f"Generated time: {now_text()}",
            f"Archive directory: `{archive_dir}`",
            "",
            "No final clean dataset, split, baseline, model training, raw overwrite, or new human review was run.",
            "",
            "## Archived files",
            "",
            *[f"- `{path}`" for path in copied],
        ],
    )
    return copied
