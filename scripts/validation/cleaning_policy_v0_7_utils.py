"""Utilities for ServiceDiscoveryBench cleaning policy validation v0.7.

These helpers operate only on audited/manual-review CSVs in v0.7. They are not
allowed to run full cleaning or emit a final clean dataset.
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


OUTPUT_DIR = Path("outputs/cleaning_policy_validation_v0_7")
DOCS_DIR = Path("docs/phase1")

MANUAL40_PATH = Path(
    "outputs/main_four_tasks_manual_check_v0_2/"
    "main_four_tasks_manual_decisions_40_user_approved_round1.csv"
)
ROUND2_PATH = Path(
    "outputs/main_four_tasks_round2_rule_validation_v0_5/"
    "round2_manual_decisions_80_user_approved.normalized_from_user_overlay.csv"
)
ROUND2_COMPARISON_REPORT = Path(
    "docs/phase1/round2_draft_vs_human_comparison_report_v0_5.md"
)
ROUND3_PATH = Path(
    "outputs/main_four_tasks_rule_revision_v0_6/"
    "round3_targeted_validation_items_100_user_reviewed.csv"
)
ROUND3_REPORT = Path("docs/phase1/round3_targeted_manual100_analysis_report.md")
V41_PATH = Path("docs/phase1/manual_audit_rule_v4_1_candidate.md")

AUDIT_EVIDENCE_PATH = OUTPUT_DIR / "audit_evidence_table_manual40_round2_round3.csv"

MANUAL_COLUMNS = [
    "manual_final_decision",
    "semantic_alignment_check",
    "capability_coverage_check",
    "leakage_check",
    "candidate_validity_check",
    "task_type_check",
    "human_notes",
]

EVIDENCE_COLUMNS = [
    "audit_round",
    "review_id",
    "task_id",
    "task_type",
    "source_dataset",
    "source_group",
    "query_text",
    "risk_category",
    "risk_subtype",
    "manual_final_decision",
    "semantic_alignment_check",
    "capability_coverage_check",
    "leakage_check",
    "candidate_validity_check",
    "task_type_check",
    "candidate_service_count",
    "gold_service_count",
    "candidate_api_count",
    "gold_api_count",
    "query_mentions_any_gold_api",
    "query_mentions_any_gold_service",
    "gold_in_candidate_services",
    "gold_in_candidate_apis",
    "human_notes",
]


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


def missing_required_inputs() -> List[Path]:
    required = [
        MANUAL40_PATH,
        ROUND2_PATH,
        ROUND2_COMPARISON_REPORT,
        ROUND3_PATH,
        ROUND3_REPORT,
    ]
    return [path for path in required if not path.exists()]


def write_missing_inputs(paths: Sequence[Path]) -> Path:
    ensure_dirs()
    out = OUTPUT_DIR / "MISSING_INPUTS.md"
    lines = [
        "# Missing Inputs for v0.7",
        "",
        f"生成时间：{now_str()}",
        "",
        "以下关键输入缺失，因此停止 v0.7，不继续猜测。",
        "",
        "| missing path |",
        "|---|",
    ]
    for path in paths:
        lines.append(f"| `{path}` |")
    lines.extend(
        [
            "",
            "Scope: 没有 full cleaning、没有 split、没有 baseline、没有训练模型。",
        ]
    )
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def empty_counts(columns: Sequence[str], rows: Sequence[Dict[str, str]]) -> Dict[str, int]:
    return {col: sum(1 for row in rows if (row.get(col) or "").strip() == "") for col in columns}


def norm_decision(raw: str) -> str:
    value = (raw or "").strip().lower()
    if value in {"keep_for_cleaning_candidate", "keep", "clean_candidate", "clean_ready"}:
        return "keep_for_cleaning_candidate"
    if value in {"remove", "removed", "remove_api_leak", "invalid_candidate_or_gold"}:
        return "remove"
    if value in {"uncertain", "ambiguous", "api_leak_uncertain", "service_leak_only"}:
        return "uncertain"
    return value or "not_available"


def norm_semantic(raw: str) -> str:
    value = (raw or "").strip().lower()
    if value in {"ok", "semantic_alignment_ok", "semantic_ok", "aligned"}:
        return "ok"
    if value in {"mismatch", "semantic_mismatch", "semantic_mismatch_uncertain"}:
        return "mismatch"
    if "uncertain" in value or "ambiguous" in value:
        return "uncertain"
    return value or "not_available"


def norm_coverage(raw: str) -> str:
    value = (raw or "").strip().lower()
    if value in {"coverage_ok", "ok"}:
        return "coverage_ok"
    if value in {"coverage_mismatch", "mismatch"}:
        return "coverage_mismatch"
    if value in {"coverage_uncertain", "uncertain", "ambiguous"}:
        return "coverage_uncertain"
    return value or "not_available"


def norm_leakage(raw: str) -> str:
    value = (raw or "").strip().lower()
    if value in {"no_blocking", "no_blocking_leak", "no_obvious_leak", "none"}:
        return "no_blocking"
    if value in {"api_leak_blocking", "api_leak", "strong_api_leak"}:
        return "api_leak_blocking"
    if value == "service_leak_only":
        return "service_leak_only"
    if value in {"ambiguous", "uncertain", "weak_leak_or_nonblocking"}:
        return "ambiguous"
    return value or "not_available"


def norm_candidate(raw: str) -> str:
    value = (raw or "").strip().lower()
    if value == "valid" or value.endswith("_valid"):
        return "valid"
    if value in {"invalid", "gold_wrong"}:
        return "invalid"
    if "insufficient" in value or "choice_space" in value:
        return "insufficient_choice_space"
    if "uncertain" in value or "ambiguous" in value:
        return "uncertain"
    return value or "not_available"


def norm_task_check(raw: str) -> str:
    value = (raw or "").strip().lower()
    if "valid_multi_service" in value:
        return "valid_multi_service"
    if "valid_multi_api" in value:
        return "valid_multi_api"
    if value in {"valid", "task_valid"}:
        return "valid"
    if value in {"invalid", "not_eligible"}:
        return "invalid"
    if "uncertain" in value or "ordinary_or_unclear" in value or "ambiguous" in value:
        return "uncertain"
    return value or "not_available"


def task_level(task_type: str) -> str:
    value = (task_type or "").lower()
    if "api" in value:
        return "api"
    if "service" in value:
        return "service"
    return "unknown"


def parse_int(raw: object) -> Optional[int]:
    text = str(raw or "").strip()
    if text == "":
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def parse_json_list(raw: str) -> Optional[list]:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except Exception:
        return None
    return data if isinstance(data, list) else None


def json_count(raw: str) -> Optional[int]:
    data = parse_json_list(raw)
    return len(data) if data is not None else None


def resolve_count(row: Dict[str, str], direct_col: str, json_col: str) -> str:
    direct = parse_int(row.get(direct_col, ""))
    if direct is not None:
        return str(direct)
    parsed = json_count(row.get(json_col, ""))
    return str(parsed) if parsed is not None else "not_available"


def _service_names(raw: str) -> Optional[set[str]]:
    data = parse_json_list(raw)
    if data is None:
        return None
    names = set()
    for item in data:
        if isinstance(item, dict):
            name = item.get("service_name") or item.get("name") or item.get("service")
        else:
            name = item
        if str(name or "").strip():
            names.add(str(name).strip().lower())
    return names


def _api_pairs(raw: str) -> Optional[set[tuple[str, str]]]:
    data = parse_json_list(raw)
    if data is None:
        return None
    pairs = set()
    for item in data:
        if isinstance(item, dict):
            api = str(item.get("api_name") or item.get("name") or "").strip().lower()
            service = str(item.get("service_name") or "").strip().lower()
            if api or service:
                pairs.add((service, api))
        elif str(item or "").strip():
            pairs.add(("", str(item).strip().lower()))
    return pairs


def gold_in_candidates(row: Dict[str, str]) -> tuple[str, str]:
    cand_services = _service_names(row.get("candidate_services_json", ""))
    gold_services = _service_names(row.get("gold_services_json", ""))
    cand_apis = _api_pairs(row.get("candidate_apis_json", ""))
    gold_apis = _api_pairs(row.get("gold_apis_json", ""))

    service_status = "unknown"
    api_status = "unknown"
    if cand_services is not None and gold_services is not None:
        service_status = "yes" if gold_services.issubset(cand_services) else "no"
    if cand_apis is not None and gold_apis is not None:
        api_status = "yes" if gold_apis.issubset(cand_apis) else "no"
    return service_status, api_status


def as_boolish(raw: str) -> str:
    value = str(raw or "").strip().lower()
    if value in {"1", "true", "yes", "y"}:
        return "1"
    if value in {"0", "false", "no", "n"}:
        return "0"
    return raw if raw not in {None, ""} else "not_available"


def normalize_row(row: Dict[str, str], audit_round: str) -> Dict[str, object]:
    if audit_round == "manual40":
        review_id = row.get("review_id", "")
        semantic = norm_semantic(row.get("manual_semantic_alignment", ""))
        coverage = "not_available"
        leakage = norm_leakage(row.get("manual_leak_check", ""))
        candidate = norm_candidate(row.get("manual_candidate_gold_validity", ""))
        task_check = norm_task_check(row.get("manual_task_type_check", ""))
        notes = row.get("manual_decision_reason") or row.get("user_approval_note", "")
        risk_category = "not_available"
        risk_subtype = "not_available"
    elif audit_round == "round2":
        review_id = row.get("round2_review_id", "")
        semantic = norm_semantic(row.get("manual_semantic_alignment", ""))
        coverage = "not_available"
        leakage = norm_leakage(row.get("manual_leak_check", ""))
        candidate = norm_candidate(row.get("manual_candidate_gold_validity", ""))
        task_check = norm_task_check(row.get("manual_task_type_check", ""))
        notes = row.get("manual_decision_reason") or row.get("calibration_reason", "")
        risk_category = row.get("user_feedback_category") or row.get("mechanical_screening_bucket") or "not_available"
        risk_subtype = row.get("assistant_warning_tags") or "not_available"
    else:
        review_id = row.get("round3_review_id", "")
        semantic = norm_semantic(row.get("semantic_alignment_check", ""))
        coverage = norm_coverage(row.get("capability_coverage_check", ""))
        leakage = norm_leakage(row.get("leakage_check", ""))
        candidate = norm_candidate(row.get("candidate_validity_check", ""))
        task_check = norm_task_check(row.get("task_type_check", ""))
        notes = row.get("human_notes", "")
        risk_category = row.get("risk_category") or "not_available"
        risk_subtype = row.get("risk_subtype") or "not_available"

    service_in, api_in = gold_in_candidates(row)
    normalized = {
        "audit_round": audit_round,
        "review_id": review_id,
        "task_id": row.get("task_id", ""),
        "task_type": row.get("task_type", ""),
        "source_dataset": row.get("source_dataset", "not_available") or "not_available",
        "source_group": row.get("source_group", "not_available") or "not_available",
        "query_text": row.get("query_text", ""),
        "risk_category": risk_category,
        "risk_subtype": risk_subtype,
        "manual_final_decision": norm_decision(row.get("manual_final_decision", "")),
        "semantic_alignment_check": semantic,
        "capability_coverage_check": coverage,
        "leakage_check": leakage,
        "candidate_validity_check": candidate,
        "task_type_check": task_check,
        "candidate_service_count": row.get("candidate_service_count_resolved")
        or resolve_count(row, "candidate_service_count", "candidate_services_json"),
        "gold_service_count": row.get("gold_service_count_resolved")
        or resolve_count(row, "gold_service_count", "gold_services_json"),
        "candidate_api_count": row.get("candidate_api_count_resolved")
        or resolve_count(row, "candidate_api_count", "candidate_apis_json"),
        "gold_api_count": row.get("gold_api_count_resolved")
        or resolve_count(row, "gold_api_count", "gold_apis_json"),
        "query_mentions_any_gold_api": as_boolish(row.get("query_mentions_any_gold_api", "not_available")),
        "query_mentions_any_gold_service": as_boolish(row.get("query_mentions_any_gold_service", "not_available")),
        "gold_in_candidate_services": service_in,
        "gold_in_candidate_apis": api_in,
        "human_notes": notes,
    }
    return normalized


def load_audit_round(path: Path, audit_round: str) -> List[Dict[str, object]]:
    _, rows = read_csv(path)
    return [normalize_row(row, audit_round) for row in rows]


def count_by(rows: Sequence[Dict[str, object]], *keys: str) -> Counter:
    return Counter(tuple(str(row.get(key, "")) for key in keys) for row in rows)


def crosstab_decision(rows: Sequence[Dict[str, object]], group_key: str) -> List[Dict[str, object]]:
    grouped = defaultdict(Counter)
    for row in rows:
        grouped[str(row.get(group_key, "not_available"))][str(row.get("manual_final_decision", ""))] += 1
    out: List[Dict[str, object]] = []
    for group, counter in sorted(grouped.items()):
        out.append(
            {
                group_key: group,
                "keep_for_cleaning_candidate": counter.get("keep_for_cleaning_candidate", 0),
                "remove": counter.get("remove", 0),
                "uncertain": counter.get("uncertain", 0),
                "total": sum(counter.values()),
            }
        )
    return out


def pct(num: int, den: int) -> str:
    if den == 0:
        return "0.0%"
    return f"{num / den * 100:.1f}%"


def markdown_table(rows: Sequence[Dict[str, object]], cols: Sequence[str], max_rows: int = 12) -> List[str]:
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


def _int_or_none(value: object) -> Optional[int]:
    return parse_int(value)


def apply_v42_policy(row: Dict[str, object]) -> Dict[str, object]:
    """Apply conservative v4.2 policy to an audited/evidence row.

    This returns a trace row. It never emits final clean data.
    """
    reasons: List[str] = []
    warnings: List[str] = []
    rules: List[str] = []
    detector_status = {
        "candidate_space_validator": "deterministic",
        "gold_in_candidate_validator": "deterministic",
        "task_type_eligibility_validator": "deterministic",
        "api_leak_detector": "heuristic_needs_review_for_weak_generic_cases",
        "service_leak_detector": "heuristic",
        "semantic_alignment_detector": "not_fully_automatic",
        "capability_coverage_detector": "not_fully_automatic",
    }
    requires_review = False
    task = task_level(str(row.get("task_type", "")))
    decision = "uncertain"
    bucket = "review_or_uncertain"

    semantic = str(row.get("semantic_alignment_check", "not_available"))
    coverage = str(row.get("capability_coverage_check", "not_available"))
    leakage = str(row.get("leakage_check", "not_available"))
    candidate = str(row.get("candidate_validity_check", "not_available"))
    task_check = str(row.get("task_type_check", "not_available"))
    csc = _int_or_none(row.get("candidate_service_count"))
    gsc = _int_or_none(row.get("gold_service_count"))
    capi = _int_or_none(row.get("candidate_api_count"))
    gapi = _int_or_none(row.get("gold_api_count"))

    service_gold_present = str(row.get("gold_in_candidate_services", "unknown"))
    api_gold_present = str(row.get("gold_in_candidate_apis", "unknown"))

    if leakage == "api_leak_blocking":
        decision, bucket = "remove", "remove_api_leak"
        reasons.append("strong_api_leak")
        rules.append("remove_strong_api_leak")
    elif coverage == "coverage_mismatch":
        decision, bucket = "remove", "remove_capability_mismatch"
        reasons.append("capability_coverage_mismatch")
        rules.append("remove_capability_coverage_mismatch")
    elif semantic == "mismatch":
        decision, bucket = "remove", "remove_semantic_mismatch"
        reasons.append("semantic_mismatch")
        rules.append("remove_semantic_mismatch")
    elif candidate in {"invalid", "insufficient_choice_space"}:
        decision, bucket = "remove", "remove_candidate_invalid"
        reasons.append(f"candidate_validity_{candidate}")
        rules.append("remove_candidate_invalid_or_insufficient")
    elif task_check == "invalid":
        decision, bucket = "remove", "remove_task_type_invalid"
        reasons.append("task_type_invalid")
        rules.append("remove_task_type_invalid")
    elif task == "service" and csc is not None and gsc is not None and csc <= gsc:
        decision, bucket = "remove", "remove_service_choice_space_invalid"
        reasons.append("service_level_candidate_service_count_not_greater_than_gold")
        rules.append("remove_service_level_no_choice_space")
    elif task == "api" and capi is not None and gapi is not None and capi <= gapi:
        decision, bucket = "remove", "remove_api_choice_space_invalid"
        reasons.append("api_level_candidate_api_count_not_greater_than_gold")
        rules.append("remove_api_level_no_api_choice_space")
    elif task == "service" and service_gold_present == "no":
        decision, bucket = "remove", "remove_gold_service_not_in_candidates"
        reasons.append("gold_service_not_in_candidates")
        rules.append("remove_gold_not_in_candidates")
    elif task == "api" and api_gold_present == "no":
        decision, bucket = "remove", "remove_gold_api_not_in_candidates"
        reasons.append("gold_api_not_in_candidates")
        rules.append("remove_gold_not_in_candidates")
    else:
        if leakage == "ambiguous":
            warnings.append("weak_or_ambiguous_api_leak")
            rules.append("uncertain_weak_or_ambiguous_api_leak")
            requires_review = True
        if leakage == "service_leak_only" and task == "service":
            warnings.append("service_leak_only_for_service_level")
            rules.append("uncertain_service_leak_for_service_discovery")
            requires_review = True
        if coverage in {"coverage_uncertain", "not_available"}:
            warnings.append(f"capability_coverage_{coverage}")
            rules.append("uncertain_capability_coverage_missing_or_uncertain")
            requires_review = True
        if semantic in {"uncertain", "not_available"}:
            warnings.append(f"semantic_alignment_{semantic}")
            rules.append("uncertain_semantic_missing_or_uncertain")
            requires_review = True
        if candidate in {"uncertain", "not_available"}:
            warnings.append(f"candidate_validity_{candidate}")
            rules.append("uncertain_candidate_validity")
            requires_review = True
        valid_task = (
            task_check == "valid"
            or (task == "service" and task_check == "valid_multi_service")
            or (task == "api" and task_check == "valid_multi_api")
        )
        if not valid_task:
            warnings.append(f"task_type_check_{task_check}")
            rules.append("uncertain_task_type_boundary")
            requires_review = True

        service_choice_ok = True
        api_choice_ok = True
        if task == "service" and csc is not None and gsc is not None:
            service_choice_ok = csc > gsc
        if task == "api" and capi is not None and gapi is not None:
            api_choice_ok = capi > gapi

        gold_present_ok = (
            (task == "service" and service_gold_present in {"yes", "unknown"})
            or (task == "api" and api_gold_present in {"yes", "unknown"})
            or task == "unknown"
        )

        if (
            leakage in {"no_blocking", "service_leak_only"}
            and not (task == "service" and leakage == "service_leak_only")
            and semantic == "ok"
            and coverage == "coverage_ok"
            and candidate == "valid"
            and valid_task
            and service_choice_ok
            and api_choice_ok
            and gold_present_ok
            and not requires_review
        ):
            decision = "keep_for_cleaning_candidate"
            bucket = "clean_ready_candidate"
            rules.append("keep_all_v42_gates_pass")
            if task == "api" and csc == 1:
                rules.append("api_level_single_service_not_fatal")
        else:
            decision = "uncertain"
            bucket = "needs_human_or_llm_review"
            requires_review = True
            if not rules:
                rules.append("uncertain_default_fail_closed")

    return {
        **row,
        "cleaning_decision": decision,
        "cleaning_bucket": bucket,
        "blocking_reasons": ";".join(reasons),
        "warning_reasons": ";".join(warnings),
        "triggered_rules": ";".join(rules),
        "detector_status": json.dumps(detector_status, ensure_ascii=False, sort_keys=True),
        "requires_human_or_llm_review": str(requires_review).lower(),
    }


def summarize_policy_trace(rows: Sequence[Dict[str, object]], group_key: str) -> List[Dict[str, object]]:
    grouped: Dict[str, List[Dict[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(group_key, "not_available"))].append(row)
    out: List[Dict[str, object]] = []
    for group, subset in sorted(grouped.items()):
        total = len(subset)
        keep_rows = [r for r in subset if r.get("cleaning_decision") == "keep_for_cleaning_candidate"]
        remove_rows = [r for r in subset if r.get("cleaning_decision") == "remove"]
        uncertain_rows = [r for r in subset if r.get("cleaning_decision") == "uncertain"]
        out.append(
            {
                group_key: group,
                "rows": total,
                "policy_keep": len(keep_rows),
                "policy_remove": len(remove_rows),
                "policy_uncertain": len(uncertain_rows),
                "human_keep": sum(1 for r in subset if r.get("manual_final_decision") == "keep_for_cleaning_candidate"),
                "human_remove": sum(1 for r in subset if r.get("manual_final_decision") == "remove"),
                "human_uncertain": sum(1 for r in subset if r.get("manual_final_decision") == "uncertain"),
                "agreement_count": sum(1 for r in subset if r.get("cleaning_decision") == r.get("manual_final_decision")),
                "agreement_rate": pct(sum(1 for r in subset if r.get("cleaning_decision") == r.get("manual_final_decision")), total),
            }
        )
    return out
