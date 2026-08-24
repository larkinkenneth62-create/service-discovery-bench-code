"""Utilities for ServiceDiscoveryBench v0.8 small full-pipeline trace.

v0.8 is a dry-run trace stage only. It reads small raw/task-level samples,
runs deterministic/heuristic detectors, applies a conservative policy trace,
and writes diagnostics. It must not emit a final clean dataset.
"""

from __future__ import annotations

import csv
import json
import re
import shutil
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


OUTPUT_DIR = Path("outputs/small_full_pipeline_trace_v0_8")
DOCS_DIR = Path("docs/phase1")
ARCHIVE_DIR = Path("outputs/run_archives/2026-06-28_small_full_pipeline_trace_v0_8")

V42_POLICY_DOC = Path("docs/phase1/manual_audit_rule_v4_2_candidate.md")
V07_REPLAY_REPORT = Path("docs/phase1/conservative_cleaning_policy_replay_report_v0_7.md")
V07_DETECTOR_MATRIX = Path("docs/phase1/automatic_detector_readiness_matrix_v0_7.md")
V07_POLICY_SCRIPT = Path("scripts/cleaning/apply_conservative_cleaning_policy_v0_7.py")

SOURCE_CANDIDATES = [
    Path("outputs/toolbench_full_raw_v0_1_streaming_dryrun/G1_task_level.csv"),
    Path("outputs/toolbench_full_raw_v0_1_streaming_dryrun/G2_task_level.csv"),
    Path("outputs/toolbench_full_raw_v0_1_streaming_dryrun/G3_task_level.csv"),
    Path("outputs/main_four_tasks_round2_small_dryrun_v0_4/round2_multi_api_candidates_pool.csv"),
    Path("outputs/main_four_tasks_round2_small_dryrun_v0_4/round2_multi_service_candidates_pool.csv"),
    Path("outputs/main_four_tasks_dryrun_v0_2/multi_api_recommendation_task_level.csv"),
    Path("outputs/main_four_tasks_dryrun_v0_2/multi_service_discovery_task_level.csv"),
]

AUDITED_FILES = [
    Path("outputs/main_four_tasks_manual_check_v0_2/main_four_tasks_manual_decisions_40_user_approved_round1.csv"),
    Path("outputs/main_four_tasks_round2_rule_validation_v0_5/round2_manual_decisions_80_user_approved.normalized_from_user_overlay.csv"),
    Path("outputs/main_four_tasks_rule_revision_v0_6/round3_targeted_validation_items_100_user_reviewed.csv"),
]

REQUIRED_BASE_FIELDS = [
    "task_id",
    "task_type",
    "source_dataset",
    "source_group",
    "query_text",
    "candidate_services_json",
    "candidate_apis_json",
    "gold_services_json",
    "gold_apis_json",
]

SAMPLE_COLUMNS = [
    "v0_8_sample_id",
    "source_file",
    "source_priority",
    "overlaps_audited_sample",
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
    "metadata_json",
]

DETECTOR_COLUMNS = [
    "v0_8_sample_id",
    "task_id",
    "task_type",
    "source_dataset",
    "source_group",
    "query_text",
    "candidate_service_count",
    "gold_service_count",
    "candidate_api_count",
    "gold_api_count",
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
    "semantic_alignment_check",
    "capability_coverage_check",
    "requires_semantic_review",
    "requires_capability_review",
    "candidate_validity_check",
    "task_type_check",
    "leakage_check",
    "query_mentions_any_gold_api",
    "query_mentions_any_gold_service",
    "candidate_services_json",
    "candidate_apis_json",
    "gold_services_json",
    "gold_apis_json",
    "source_file",
    "overlaps_audited_sample",
]

POLICY_COLUMNS = [
    *DETECTOR_COLUMNS,
    "policy_decision",
    "policy_bucket",
    "blocking_reasons",
    "warning_reasons",
    "triggered_rules",
    "requires_human_or_llm_review",
    "can_be_clean_ready_without_semantic_capability_detector",
    "detector_status_summary",
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
    "subtitles",
    "text",
    "json",
    "srt",
    "format",
    "list",
    "all",
    "get",
}


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure_dirs() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)


def read_csv(path: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    if not path.exists():
        raise FileNotFoundError(f"Missing CSV file: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader.fieldnames or []), list(reader)


def write_csv(path: Path, rows: Sequence[Dict[str, object]], fieldnames: Optional[Sequence[str]] = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        fieldnames = fieldnames_union(rows)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(fieldnames), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def fieldnames_union(rows: Sequence[Dict[str, object]]) -> List[str]:
    names: List[str] = []
    for row in rows:
        for key in row:
            if key not in names:
                names.append(key)
    return names


def markdown_table(rows: Sequence[Dict[str, object]], cols: Sequence[str], max_rows: int = 40) -> List[str]:
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join(["---"] * len(cols)) + "|"]
    for row in rows[:max_rows]:
        vals: List[str] = []
        for col in cols:
            text = str(row.get(col, "")).replace("\n", " ").replace("|", "/")
            if len(text) > 180:
                text = text[:177] + "..."
            vals.append(text)
        lines.append("| " + " | ".join(vals) + " |")
    if not rows:
        lines.append("| " + " | ".join("-" for _ in cols) + " |")
    return lines


def parse_json(raw: str) -> object:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def parse_json_list(raw: str) -> Optional[list]:
    data = parse_json(raw)
    return data if isinstance(data, list) else None


def parse_metadata(row: Dict[str, str]) -> Dict[str, object]:
    data = parse_json(row.get("metadata_json", ""))
    return data if isinstance(data, dict) else {}


def parse_int(raw: object) -> Optional[int]:
    text = str(raw or "").strip()
    if not text or text == "not_available":
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def json_count(raw: str) -> Optional[int]:
    data = parse_json_list(raw)
    if data is None:
        return None
    return len(data)


def resolve_count(row: Dict[str, str], direct_col: str, json_col: str, metadata_key: str) -> str:
    direct = parse_int(row.get(direct_col, ""))
    if direct is not None:
        return str(direct)
    metadata = parse_metadata(row)
    meta_val = parse_int(metadata.get(metadata_key, ""))
    if meta_val is not None:
        return str(meta_val)
    parsed = json_count(row.get(json_col, ""))
    return str(parsed) if parsed is not None else "not_available"


def normalize_text(text: str) -> str:
    text = str(text or "").lower().strip()
    text = text.replace("\\", "/")
    text = re.sub(r"[_\-]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_loose(text: str) -> str:
    text = normalize_text(text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def endpoint_like(api_name: str) -> bool:
    text = str(api_name or "")
    return "/" in text or ":" in text or "{" in text or "}" in text


def camel_case_like(text: str) -> bool:
    return bool(re.search(r"[a-z][A-Z]|[A-Z]{2,}[a-z]", str(text or "")))


def term_in_query(term: str, query: str) -> bool:
    term = str(term or "").strip()
    if not term:
        return False
    q_raw = str(query or "").lower()
    t_raw = term.lower()
    if len(t_raw) >= 3 and t_raw in q_raw:
        return True
    q = normalize_loose(query)
    t = normalize_loose(term)
    if not t:
        return False
    if len(t) <= 2:
        return f" {t} " in f" {q} "
    return t in q


def candidate_services(raw: str) -> List[Dict[str, str]]:
    data = parse_json_list(raw) or []
    out: List[Dict[str, str]] = []
    for item in data:
        if isinstance(item, dict):
            name = item.get("service_name") or item.get("name") or item.get("service")
            out.append({**item, "service_name": str(name or "").strip()})
        elif str(item or "").strip():
            out.append({"service_name": str(item).strip()})
    return [item for item in out if item.get("service_name")]


def gold_services(raw: str) -> List[str]:
    data = parse_json_list(raw) or []
    out: List[str] = []
    for item in data:
        if isinstance(item, dict):
            name = item.get("service_name") or item.get("name") or item.get("service")
        else:
            name = item
        if str(name or "").strip():
            out.append(str(name).strip())
    return out


def api_records(raw: str) -> List[Dict[str, str]]:
    data = parse_json_list(raw) or []
    out: List[Dict[str, str]] = []
    for item in data:
        if isinstance(item, dict):
            api = item.get("api_name") or item.get("name") or item.get("api")
            service = item.get("service_name") or item.get("service") or ""
            out.append({**item, "api_name": str(api or "").strip(), "service_name": str(service or "").strip()})
        elif str(item or "").strip():
            out.append({"api_name": str(item).strip(), "service_name": ""})
    return [item for item in out if item.get("api_name") or item.get("service_name")]


def service_name_set(raw: str) -> Optional[set[str]]:
    data = candidate_services(raw)
    if parse_json_list(raw) is None:
        return None
    return {normalize_loose(item["service_name"]) for item in data if item.get("service_name")}


def gold_service_set(raw: str) -> Optional[set[str]]:
    if parse_json_list(raw) is None:
        return None
    return {normalize_loose(item) for item in gold_services(raw) if item}


def api_pair_set(raw: str) -> Optional[set[tuple[str, str]]]:
    if parse_json_list(raw) is None:
        return None
    pairs = set()
    for item in api_records(raw):
        pairs.add((normalize_loose(item.get("service_name", "")), normalize_loose(item.get("api_name", ""))))
    return pairs


def gold_in_candidates(row: Dict[str, str]) -> Tuple[str, str]:
    cand_services = service_name_set(row.get("candidate_services_json", ""))
    gold_s = gold_service_set(row.get("gold_services_json", ""))
    cand_apis = api_pair_set(row.get("candidate_apis_json", ""))
    gold_a = api_pair_set(row.get("gold_apis_json", ""))
    service_status = "unknown"
    api_status = "unknown"
    if cand_services is not None and gold_s is not None:
        service_status = "yes" if gold_s.issubset(cand_services) else "no"
    if cand_apis is not None and gold_a is not None:
        if gold_a.issubset(cand_apis):
            api_status = "yes"
        else:
            cand_api_names = {api for _, api in cand_apis}
            gold_api_names = {api for _, api in gold_a}
            api_status = "yes_api_name_only" if gold_api_names.issubset(cand_api_names) else "no"
    return service_status, api_status


def prediction_level(task_type: str) -> str:
    value = str(task_type or "").lower()
    if "api" in value:
        return "api"
    if "service" in value or "composable" in value:
        return "service"
    return "unknown"


def boolish(raw: object) -> str:
    value = str(raw or "").strip().lower()
    if value in {"1", "true", "yes", "y"}:
        return "1"
    if value in {"0", "false", "no", "n"}:
        return "0"
    return "not_available" if value == "" else str(raw)


def source_priority(path: Path) -> str:
    text = str(path)
    if "toolbench_full_raw_v0_1_streaming_dryrun" in text:
        return "A_streaming_task_level_dryrun"
    if "round2_small_dryrun" in text or "main_four_tasks_dryrun" in text:
        return "B_main_four_tasks_or_round2_pool"
    return "other"


def candidate_input_files() -> List[Path]:
    return [path for path in SOURCE_CANDIDATES if path.exists()]


def audited_task_ids() -> set[str]:
    ids: set[str] = set()
    for path in AUDITED_FILES:
        if not path.exists():
            continue
        _, rows = read_csv(path)
        for row in rows:
            task_id = (row.get("task_id") or "").strip()
            if task_id:
                ids.add(task_id)
    return ids


def normalize_input_row(row: Dict[str, str], source_file: Path, audited_ids: set[str]) -> Dict[str, object]:
    metadata = parse_metadata(row)
    out: Dict[str, object] = {}
    out["source_file"] = str(source_file)
    out["source_priority"] = source_priority(source_file)
    task_id = row.get("task_id", "")
    out["overlaps_audited_sample"] = "yes" if task_id in audited_ids else "no"
    for field in REQUIRED_BASE_FIELDS:
        out[field] = row.get(field, "")
    out["source_query_id"] = row.get("source_query_id") or metadata.get("source_query_id", "")
    out["candidate_service_count"] = resolve_count(row, "candidate_service_count", "candidate_services_json", "candidate_service_count")
    out["gold_service_count"] = resolve_count(row, "gold_service_count", "gold_services_json", "gold_service_count")
    out["candidate_api_count"] = resolve_count(row, "candidate_api_count", "candidate_apis_json", "candidate_api_count")
    out["gold_api_count"] = resolve_count(row, "gold_api_count", "gold_apis_json", "gold_api_count")
    out["query_mentions_any_gold_api"] = boolish(
        row.get("query_mentions_any_gold_api", metadata.get("query_mentions_any_gold_api", ""))
    )
    out["query_mentions_any_gold_service"] = boolish(
        row.get("query_mentions_any_gold_service", metadata.get("query_mentions_any_gold_service", ""))
    )
    out["metadata_json"] = row.get("metadata_json", "")
    return out


def load_all_candidate_rows() -> List[Dict[str, object]]:
    ids = audited_task_ids()
    rows: List[Dict[str, object]] = []
    seen_source_task: set[tuple[str, str]] = set()
    for path in candidate_input_files():
        _, source_rows = read_csv(path)
        for row in source_rows:
            normalized = normalize_input_row(row, path, ids)
            key = (str(path), str(normalized.get("task_id", "")))
            if key in seen_source_task:
                continue
            seen_source_task.add(key)
            rows.append(normalized)
    return rows


def count_by(rows: Sequence[Dict[str, object]], key: str) -> Dict[str, int]:
    return dict(sorted(Counter(str(row.get(key, "not_available")) for row in rows).items()))


def count_by_level(rows: Sequence[Dict[str, object]]) -> Dict[str, int]:
    return dict(sorted(Counter(prediction_level(str(row.get("task_type", ""))) for row in rows).items()))


def status_distribution(rows: Sequence[Dict[str, object]], key: str) -> List[Dict[str, object]]:
    return [{"value": k, "count": v} for k, v in count_by(rows, key).items()]


def run_detectors(row: Dict[str, str]) -> Dict[str, object]:
    level = prediction_level(row.get("task_type", ""))
    csc = parse_int(row.get("candidate_service_count"))
    gsc = parse_int(row.get("gold_service_count"))
    capi = parse_int(row.get("candidate_api_count"))
    gapi = parse_int(row.get("gold_api_count"))
    service_in, api_in = gold_in_candidates(row)

    if level == "service":
        if csc is None or gsc is None:
            candidate_space_status = "candidate_space_unknown"
        elif csc > gsc:
            candidate_space_status = "valid_service_choice_space"
        else:
            candidate_space_status = "invalid_service_no_choice_space"
        task_type_status = "valid_service_level"
        task_type_check = "valid_multi_service"
    elif level == "api":
        if capi is None or gapi is None:
            candidate_space_status = "candidate_space_unknown"
        elif capi > gapi:
            candidate_space_status = "valid_api_choice_space"
        else:
            candidate_space_status = "invalid_api_no_choice_space"
        task_type_status = "valid_api_level"
        task_type_check = "valid_multi_api"
    else:
        candidate_space_status = "candidate_space_unknown"
        task_type_status = "unknown_task_level"
        task_type_check = "uncertain"

    candidate_validity = "valid"
    if service_in == "no" or (level == "api" and api_in == "no"):
        candidate_validity = "invalid"
    elif service_in == "unknown" or (level == "api" and api_in == "unknown"):
        candidate_validity = "uncertain"
    if candidate_space_status.startswith("invalid"):
        candidate_validity = "insufficient_choice_space"

    api_leak = detect_api_leak(row)
    service_leak = detect_service_leak(row)
    leakage_check = "no_blocking"
    if api_leak["api_leak_detector_status"] == "api_leak_blocking":
        leakage_check = "api_leak_blocking"
    elif api_leak["api_leak_detector_status"] == "api_leak_weak_or_generic":
        leakage_check = "ambiguous"
    elif service_leak["service_leak_detector_status"] == "service_leak_only":
        leakage_check = "service_leak_only"
    elif service_leak["service_leak_detector_status"] == "ambiguous_service_leak":
        leakage_check = "ambiguous"

    return {
        **row,
        "gold_in_candidate_services": service_in,
        "gold_in_candidate_apis": api_in,
        "prediction_level": level,
        "candidate_space_status": candidate_space_status,
        "task_type_eligibility_status": task_type_status,
        **api_leak,
        **service_leak,
        "semantic_alignment_check": "missing_or_unavailable",
        "capability_coverage_check": "missing_or_unavailable",
        "requires_semantic_review": "true",
        "requires_capability_review": "true",
        "candidate_validity_check": candidate_validity,
        "task_type_check": task_type_check,
        "leakage_check": leakage_check,
    }


def detect_api_leak(row: Dict[str, str]) -> Dict[str, str]:
    query = row.get("query_text", "")
    matched_blocking: List[str] = []
    matched_weak: List[str] = []
    reasons: List[str] = []
    for api in api_records(row.get("gold_apis_json", "")):
        name = api.get("api_name", "")
        loose = normalize_loose(name)
        tokens = [tok for tok in re.split(r"\s+", loose) if tok]
        if not name:
            continue
        if term_in_query(name, query):
            if endpoint_like(name) or camel_case_like(name):
                matched_blocking.append(name)
                reasons.append("exact endpoint/path/CamelCase API identity appears in query")
            elif loose in GENERIC_API_WORDS or all(tok in GENERIC_API_WORDS for tok in tokens):
                matched_weak.append(name)
                reasons.append("generic API word appears in query")
            elif len(loose) <= 2:
                matched_weak.append(name)
                reasons.append("very short API term appears in query")
            elif len(tokens) >= 2 and any(tok not in GENERIC_API_WORDS for tok in tokens):
                matched_blocking.append(name)
                reasons.append("multi-word non-generic API identity appears in query")
            else:
                matched_weak.append(name)
                reasons.append("ambiguous API-name mention")
    if matched_blocking:
        status = "api_leak_blocking"
        strength = "strong"
    elif matched_weak:
        status = "api_leak_weak_or_generic"
        strength = "weak_or_generic"
    else:
        status = "no_blocking_api_leak"
        strength = "none"
        reasons.append("no gold API name matched query by heuristic")
    matched_terms = matched_blocking + matched_weak
    return {
        "api_leak_detector_status": status,
        "api_leak_strength": strength,
        "api_leak_matched_terms": "; ".join(dict.fromkeys(matched_terms)),
        "api_leak_reason": "; ".join(dict.fromkeys(reasons)),
    }


def detect_service_leak(row: Dict[str, str]) -> Dict[str, str]:
    query = row.get("query_text", "")
    matched: List[str] = []
    ambiguous: List[str] = []
    reasons: List[str] = []
    for service in gold_services(row.get("gold_services_json", "")):
        loose = normalize_loose(service)
        if not service:
            continue
        if term_in_query(service, query):
            tokens = [tok for tok in loose.split() if tok]
            if len(tokens) <= 1 and len(loose) < 6:
                ambiguous.append(service)
                reasons.append("short or generic-looking service name appears in query")
            else:
                matched.append(service)
                reasons.append("gold service name appears in query")
    if matched:
        status = "service_leak_only"
    elif ambiguous:
        status = "ambiguous_service_leak"
    else:
        status = "no_service_leak"
        reasons.append("no gold service name matched query by heuristic")
    return {
        "service_leak_detector_status": status,
        "service_leak_matched_terms": "; ".join(dict.fromkeys(matched + ambiguous)),
        "service_leak_reason": "; ".join(dict.fromkeys(reasons)),
    }


def apply_v42_trace_policy(row: Dict[str, object]) -> Dict[str, object]:
    level = str(row.get("prediction_level", "unknown"))
    blocking: List[str] = []
    warnings: List[str] = []
    rules: List[str] = []
    decision = "uncertain"
    bucket = "needs_human_or_llm_review"
    requires_review = True

    api_leak = str(row.get("api_leak_detector_status", ""))
    candidate_space = str(row.get("candidate_space_status", ""))
    service_in = str(row.get("gold_in_candidate_services", "unknown"))
    api_in = str(row.get("gold_in_candidate_apis", "unknown"))
    semantic = str(row.get("semantic_alignment_check", "missing_or_unavailable"))
    capability = str(row.get("capability_coverage_check", "missing_or_unavailable"))
    service_leak = str(row.get("service_leak_detector_status", ""))

    if api_leak == "api_leak_blocking":
        decision = "remove"
        bucket = "remove_api_leak"
        blocking.append("strong_api_leak")
        rules.append("remove_strong_api_leak")
    elif level == "service" and service_in == "no":
        decision = "remove"
        bucket = "remove_gold_service_not_in_candidates"
        blocking.append("gold_service_not_in_candidates")
        rules.append("remove_gold_missing")
    elif level == "api" and api_in == "no":
        decision = "remove"
        bucket = "remove_gold_api_not_in_candidates"
        blocking.append("gold_api_not_in_candidates")
        rules.append("remove_gold_missing")
    elif level == "service" and candidate_space == "invalid_service_no_choice_space":
        decision = "remove"
        bucket = "remove_service_choice_space_invalid"
        blocking.append("service_level_no_real_choice_space")
        rules.append("remove_service_level_no_choice_space")
    elif level == "api" and candidate_space == "invalid_api_no_choice_space":
        decision = "remove"
        bucket = "remove_api_choice_space_invalid"
        blocking.append("api_level_no_real_api_choice_space")
        rules.append("remove_api_level_no_api_choice_space")
    elif semantic == "mismatch":
        decision = "remove"
        bucket = "remove_semantic_mismatch"
        blocking.append("semantic_mismatch")
        rules.append("remove_semantic_mismatch")
    elif capability == "coverage_mismatch":
        decision = "remove"
        bucket = "remove_capability_mismatch"
        blocking.append("capability_coverage_mismatch")
        rules.append("remove_capability_mismatch")
    else:
        if api_leak == "api_leak_weak_or_generic":
            warnings.append("weak_or_generic_api_leak")
            rules.append("uncertain_weak_or_generic_api_leak")
        if service_leak == "service_leak_only":
            if level == "service":
                warnings.append("service_leak_only_for_service_level")
                rules.append("uncertain_service_leak_for_service_discovery")
            elif level == "api":
                warnings.append("service_leak_only_warning_for_api_level")
                rules.append("api_level_service_leak_not_fatal_but_warn")
        if semantic in {"missing_or_unavailable", "not_available", "uncertain", ""}:
            warnings.append("semantic_alignment_missing_or_unavailable")
            rules.append("uncertain_missing_semantic_alignment")
        if capability in {"missing_or_unavailable", "not_available", "coverage_uncertain", "uncertain", ""}:
            warnings.append("capability_coverage_missing_or_unavailable")
            rules.append("uncertain_missing_capability_coverage")
        if service_in == "unknown" or (level == "api" and api_in == "unknown"):
            warnings.append("gold_in_candidate_unknown")
            rules.append("uncertain_gold_presence_unknown")
        if candidate_space == "candidate_space_unknown":
            warnings.append("candidate_space_unknown")
            rules.append("uncertain_candidate_space_unknown")
        if not rules:
            rules.append("uncertain_default_fail_closed")
    detector_status = {
        "candidate_space_validator": "deterministic",
        "gold_in_candidate_validator": "deterministic_with_basic_normalization",
        "task_type_eligibility_validator": "deterministic",
        "api_leak_detector": "heuristic",
        "service_leak_detector": "heuristic",
        "semantic_alignment_detector": "missing_or_unavailable",
        "capability_coverage_detector": "missing_or_unavailable",
    }
    return {
        **row,
        "policy_decision": decision,
        "policy_bucket": bucket,
        "blocking_reasons": ";".join(blocking),
        "warning_reasons": ";".join(warnings),
        "triggered_rules": ";".join(rules),
        "requires_human_or_llm_review": str(requires_review).lower(),
        "can_be_clean_ready_without_semantic_capability_detector": "false",
        "detector_status_summary": json.dumps(detector_status, ensure_ascii=False, sort_keys=True),
    }


def dangerous_error_rows(rows: Sequence[Dict[str, object]]) -> List[Dict[str, object]]:
    errors: List[Dict[str, object]] = []
    for row in rows:
        policy = str(row.get("policy_decision", ""))
        level = str(row.get("prediction_level", ""))
        reasons: List[str] = []
        if row.get("api_leak_detector_status") == "api_leak_blocking" and policy == "keep_for_cleaning_candidate":
            reasons.append("strong_api_leak_into_keep")
        if policy == "keep_for_cleaning_candidate" and (
            row.get("gold_in_candidate_services") == "no" or (level == "api" and row.get("gold_in_candidate_apis") == "no")
        ):
            reasons.append("gold_missing_into_keep")
        if policy == "keep_for_cleaning_candidate" and row.get("candidate_space_status") == "invalid_service_no_choice_space":
            reasons.append("service_level_no_choice_space_into_keep")
        if policy == "keep_for_cleaning_candidate" and row.get("candidate_space_status") == "invalid_api_no_choice_space":
            reasons.append("api_level_no_api_choice_space_into_keep")
        if policy == "keep_for_cleaning_candidate" and (
            row.get("semantic_alignment_check") == "missing_or_unavailable"
            or row.get("capability_coverage_check") == "missing_or_unavailable"
        ):
            reasons.append("missing_semantic_or_capability_into_keep")
        independent_remove_text = str(row.get("blocking_reasons", "")) + ";" + str(row.get("triggered_rules", ""))
        independent_remove_markers = [
            "gold_",
            "choice_space",
            "semantic_mismatch",
            "capability",
            "no_real",
        ]
        weak_generic_is_only_remove_signal = not any(marker in independent_remove_text for marker in independent_remove_markers)
        if (
            row.get("api_leak_detector_status") == "api_leak_weak_or_generic"
            and policy == "remove"
            and weak_generic_is_only_remove_signal
        ):
            reasons.append("weak_generic_api_leak_over_removed")
        if (
            level == "api"
            and str(row.get("candidate_service_count")) == "1"
            and policy == "remove"
            and row.get("candidate_space_status") != "invalid_api_no_choice_space"
            and "service_count" in str(row.get("blocking_reasons", "") + row.get("triggered_rules", ""))
        ):
            reasons.append("api_level_single_service_false_remove_due_only_to_service_count")
        for reason in reasons:
            errors.append({**row, "dangerous_error_type": reason})
    return errors


def archive_v0_8() -> Path:
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    copied: List[str] = []
    scripts = [
        Path("scripts/validation/check_v0_8_inputs.py"),
        Path("scripts/validation/prepare_small_full_pipeline_sample_v0_8.py"),
        Path("scripts/validation/run_detectors_for_small_sample_v0_8.py"),
        Path("scripts/validation/apply_v4_2_policy_to_small_trace_v0_8.py"),
        Path("scripts/validation/check_v0_8_dangerous_errors.py"),
        Path("scripts/validation/small_full_pipeline_v0_8_utils.py"),
    ]
    for src in scripts:
        if src.exists():
            dst = ARCHIVE_DIR / src
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied.append(str(dst))
    if OUTPUT_DIR.exists():
        dst = ARCHIVE_DIR / "outputs_small_full_pipeline_trace_v0_8"
        shutil.copytree(OUTPUT_DIR, dst, dirs_exist_ok=True)
        copied.append(str(dst))
    docs = [
        DOCS_DIR / "small_full_pipeline_detector_report_v0_8.md",
        DOCS_DIR / "small_full_pipeline_policy_trace_report_v0_8.md",
        DOCS_DIR / "small_full_pipeline_dangerous_error_report_v0_8.md",
        DOCS_DIR / "small_full_pipeline_trace_v0_8_go_no_go_report.md",
    ]
    docs_dest = ARCHIVE_DIR / "docs_phase1"
    for src in docs:
        if src.exists():
            dst = docs_dest / src.name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied.append(str(dst))
    manifest = ARCHIVE_DIR / "ARCHIVE_MANIFEST.md"
    lines = [
        "# Archive Manifest: small_full_pipeline_trace_v0_8",
        "",
        f"Generated time: {now_str()}",
        f"Archive directory: `{ARCHIVE_DIR}`",
        "",
        "Scope: archived v0.8 trace-only scripts, reports, and outputs.",
        "",
        "No full cleaning, no final clean dataset, no split, no baseline, and no model training were run.",
        "",
        "## Files",
        "",
    ]
    for item in copied:
        lines.append(f"- `{item}`")
    manifest.write_text("\n".join(lines), encoding="utf-8")
    return manifest
