#!/usr/bin/env python
"""Prediction-target-aware validator for source-QA reviewed CSVs v0.4.1.

The module is deliberately read-only: it never rewrites reviewed or source rows,
and it never fills human adjudication fields.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence


VALIDATOR_VERSION = "v0.4.1"

HUMAN_FIELDS = [
    "qa_semantic_usability",
    "qa_release_action",
    "qa_main_benchmark_eligible_now",
    "qa_repair_required",
    "qa_repair_reason",
    "qa_dependency_chain_evidence",
    "adjudicated_final_decision",
    "adjudicator_id",
    "adjudicated_at",
    "adjudication_notes",
]

CORE_REQUIRED_FIELDS = [
    "qa_semantic_usability",
    "qa_release_action",
    "qa_main_benchmark_eligible_now",
    "qa_repair_required",
    "adjudicated_final_decision",
    "adjudicator_id",
    "adjudicated_at",
]

CONDITIONAL_FIELDS = [
    "qa_repair_reason",
    "qa_dependency_chain_evidence",
]

ALLOWED_ENUMS = {
    "qa_semantic_usability": {"usable", "uncertain", "unusable"},
    "qa_release_action": {
        "keep_as_is",
        "rewrite_then_reaudit",
        "reconstruct_then_reaudit",
        "dependency_review",
        "hold",
        "remove",
    },
    "qa_main_benchmark_eligible_now": {"true", "false"},
    "qa_repair_required": {"true", "false"},
    "adjudicated_final_decision": {
        "keep_as_is",
        "rewrite_then_reaudit",
        "reconstruct_then_reaudit",
        "dependency_review",
        "hold",
        "remove",
    },
}

REPAIR_ACTIONS = {
    "rewrite_then_reaudit",
    "reconstruct_then_reaudit",
    "dependency_review",
}

NON_ELIGIBLE_ACTIONS = REPAIR_ACTIONS | {"hold", "remove"}

ISSUE_FIELDS = [
    "severity",
    "category",
    "filename",
    "row_id",
    "field",
    "issue_type",
    "observed_value",
    "expected_value",
    "notes",
]

CHANGED_ROW_FIELDS = [
    "source",
    "filename",
    "row_id",
    "field",
    "original_value",
    "reviewed_value",
]


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def text(value: Any) -> str:
    return str(value or "").strip()


def normalized_bool(value: Any) -> str:
    value_text = text(value).lower()
    if value_text in {"true", "1"}:
        return "true"
    if value_text in {"false", "0"}:
        return "false"
    return value_text


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    csv.field_size_limit(min(sys.maxsize, 2**31 - 1))
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def write_csv(path: Path, fields: Sequence[str], rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def make_issue(
    severity: str,
    issue_type: str,
    *,
    category: str = "source_qa_reviewed",
    filename: str = "",
    row_id: str = "",
    field: str = "",
    observed: Any = "",
    expected: Any = "",
    notes: str = "",
) -> dict[str, str]:
    return {
        "severity": severity,
        "category": category,
        "filename": filename,
        "row_id": row_id,
        "field": field,
        "issue_type": issue_type,
        "observed_value": str(observed),
        "expected_value": str(expected),
        "notes": notes,
    }


def detect_primary_id(columns: Sequence[str]) -> str:
    for field in [
        "review_item_id",
        "task_id",
        "canonical_task_id",
        "source_task_id",
        "source_row_id",
        "source_query_id",
    ]:
        if field in columns:
            return field
    return ""


def get_row_id(row: dict[str, Any]) -> str:
    for field in ["review_item_id", "task_id", "canonical_task_id", "source_task_id"]:
        if text(row.get(field)):
            return text(row.get(field))
    return ""


def source_name(row: dict[str, Any], source_hint: str = "") -> str:
    return (text(row.get("source_dataset")) or text(row.get("source")) or source_hint).lower()


def combined_task_type(row: dict[str, Any]) -> str:
    values = [text(row.get("task_type")), text(row.get("task_type_guess"))]
    return " ".join(value.lower() for value in values if value)


def determine_validation_target(row: dict[str, Any], source_hint: str = "") -> str:
    """Return service, api, mixed, or unknown without ID-based hard-coding."""

    prediction_level = text(row.get("prediction_level")).lower()
    if prediction_level in {"service", "api"}:
        return prediction_level

    task_type = combined_task_type(row)
    service_signals = [
        "single_service_discovery",
        "multi_service_discovery",
        "composable_service_discovery",
        "service_discovery",
    ]
    api_signals = [
        "single_api_recommendation",
        "multi_api_recommendation",
        "composable_api_recommendation",
        "api_recommendation",
    ]
    has_service_signal = any(signal in task_type for signal in service_signals)
    has_api_signal = any(signal in task_type for signal in api_signals)
    if has_service_signal and has_api_signal:
        return "mixed"
    if has_service_signal:
        return "service"
    if has_api_signal:
        return "api"

    source = source_name(row, source_hint)
    if "metatool" in source:
        return "service"
    if "shortcutsbench" in source:
        return "service"
    if "stabletoolbench" in source:
        group = (text(row.get("stable_group")) or text(row.get("source_group"))).upper()
        if group == "G1" and "single_or_api_recommendation_candidate" in task_type:
            return "api"
        if group in {"G2", "G3"}:
            return "mixed"
        return "unknown"
    if "toolbench" in source:
        return "unknown"
    return "unknown"


def is_composable_row(row: dict[str, Any]) -> bool:
    task_type = combined_task_type(row)
    group = (text(row.get("stable_group")) or text(row.get("source_group"))).upper()
    return "composable" in task_type or group == "G3"


def parse_json_list(value: Any) -> tuple[bool, list[Any], str]:
    raw = text(value)
    if not raw:
        return False, [], "empty"
    try:
        parsed = json.loads(raw)
    except Exception as exc:
        return False, [], type(exc).__name__
    if not isinstance(parsed, list):
        return False, [], f"expected_list_got_{type(parsed).__name__}"
    return True, parsed, ""


def service_identity(item: Any) -> str:
    if isinstance(item, str):
        return item.strip().casefold()
    if isinstance(item, dict):
        for field in ["service_name", "tool_name", "plugin_name", "name", "service_id", "tool_id"]:
            value = text(item.get(field))
            if value:
                return value.casefold()
    return json.dumps(item, ensure_ascii=False, sort_keys=True).casefold()


def api_identity(item: Any) -> tuple[str, str]:
    if isinstance(item, str):
        return "", item.strip().casefold()
    if isinstance(item, dict):
        service = ""
        api = ""
        for field in ["service_name", "tool_name", "plugin_name", "category_name"]:
            value = text(item.get(field))
            if value:
                service = value.casefold()
                break
        for field in ["api_name", "endpoint_name", "action_name", "name", "api_id"]:
            value = text(item.get(field))
            if value:
                api = value.casefold()
                break
        if api:
            return service, api
    return "", json.dumps(item, ensure_ascii=False, sort_keys=True).casefold()


def has_blocking_leak(row: dict[str, Any], level: str) -> bool:
    fields = [
        "qa_leakage_check",
        "prior_qa_leakage_check",
        "metatool_leakage_policy_label",
        "stable_policy_label",
        "api_leak_detector_status",
        "service_leak_detector_status",
        "leakage_check_status",
    ]
    combined = " ".join(text(row.get(field)).lower() for field in fields)
    if f"{level}_leak_blocking" in combined:
        return True
    explicit_field = f"explicit_{level}_leak_detected"
    return normalized_bool(row.get(explicit_field)) == "true"


def evaluate_service_candidate_space(row: dict[str, Any]) -> dict[str, Any]:
    candidate_ok, candidates, candidate_error = parse_json_list(row.get("candidate_services_json"))
    gold_ok, gold, gold_error = parse_json_list(row.get("gold_services_json"))
    candidate_ids = {service_identity(item) for item in candidates} if candidate_ok else set()
    gold_ids = {service_identity(item) for item in gold} if gold_ok else set()
    gold_subset = bool(candidate_ok and gold_ok and gold_ids.issubset(candidate_ids))
    distractor_count = len(candidate_ids - gold_ids) if candidate_ok and gold_ok else 0
    blocking_leak = has_blocking_leak(row, "service")
    valid = bool(
        candidate_ok
        and gold_ok
        and gold_ids
        and gold_subset
        and len(candidate_ids) > len(gold_ids)
        and distractor_count > 0
        and not blocking_leak
    )
    return {
        "parse_ok": candidate_ok and gold_ok,
        "candidate_parse_error": candidate_error,
        "gold_parse_error": gold_error,
        "candidate_count": len(candidate_ids),
        "gold_count": len(gold_ids),
        "gold_subset": gold_subset,
        "distractor_count": distractor_count,
        "candidate_equals_gold": candidate_ok and gold_ok and candidate_ids == gold_ids,
        "blocking_leak": blocking_leak,
        "valid": valid,
    }


def parse_int(value: Any) -> int | None:
    try:
        return int(text(value))
    except (TypeError, ValueError):
        return None


def evaluate_api_candidate_space(row: dict[str, Any]) -> dict[str, Any]:
    candidate_ok, candidates, candidate_error = parse_json_list(row.get("candidate_apis_json"))
    gold_ok, gold, gold_error = parse_json_list(row.get("gold_apis_json"))
    candidate_ids = {api_identity(item) for item in candidates} if candidate_ok else set()
    gold_ids = {api_identity(item) for item in gold} if gold_ok else set()
    gold_subset = bool(candidate_ok and gold_ok and gold_ids.issubset(candidate_ids))
    computed_distractors = len(candidate_ids - gold_ids) if candidate_ok and gold_ok else 0
    recorded_distractors = parse_int(row.get("negative_distractor_count"))
    effective_distractors = (
        recorded_distractors
        if recorded_distractors is not None and recorded_distractors >= 0
        else computed_distractors
    )
    blocking_leak = has_blocking_leak(row, "api")
    valid = bool(
        candidate_ok
        and gold_ok
        and gold_ids
        and gold_subset
        and len(candidate_ids) > len(gold_ids)
        and candidate_ids != gold_ids
        and effective_distractors > 0
        and not blocking_leak
    )
    return {
        "parse_ok": candidate_ok and gold_ok,
        "candidate_parse_error": candidate_error,
        "gold_parse_error": gold_error,
        "candidate_count": len(candidate_ids),
        "gold_count": len(gold_ids),
        "gold_subset": gold_subset,
        "distractor_count": computed_distractors,
        "negative_distractor_count": effective_distractors,
        "candidate_equals_gold": candidate_ok and gold_ok and candidate_ids == gold_ids,
        "blocking_leak": blocking_leak,
        "valid": valid,
    }


def requires_repair_reason(row: dict[str, Any]) -> bool:
    action = text(row.get("qa_release_action"))
    decision = text(row.get("adjudicated_final_decision"))
    repair = normalized_bool(row.get("qa_repair_required"))
    return repair == "true" or action in REPAIR_ACTIONS or decision in REPAIR_ACTIONS


def requires_dependency_evidence(row: dict[str, Any]) -> bool:
    action = text(row.get("qa_release_action"))
    decision = text(row.get("adjudicated_final_decision"))
    eligible = normalized_bool(row.get("qa_main_benchmark_eligible_now"))
    return is_composable_row(row) and (action == "keep_as_is" or decision == "keep_as_is") and eligible == "true"


def validate_rows(
    rows: Sequence[dict[str, Any]],
    *,
    filename: str = "",
    category: str = "source_qa_reviewed",
    source_hint: str = "",
    expected_rows: int | None = None,
) -> tuple[dict[str, Any], list[dict[str, str]], list[dict[str, Any]]]:
    """Validate row semantics without modifying any row."""

    issues: list[dict[str, str]] = []
    row_results: list[dict[str, Any]] = []
    target_counts: Counter[str] = Counter()
    pending_count = 0
    conditional_missing_count = 0
    invalid_enum_count = 0
    consistency_issue_count = 0
    candidate_space_fatal_count = 0
    shortcuts_false_api_block_count = 0

    if expected_rows is not None and len(rows) != expected_rows:
        issues.append(
            make_issue(
                "fatal",
                "expected_row_count_mismatch",
                category=category,
                filename=filename,
                observed=len(rows),
                expected=expected_rows,
            )
        )

    for row in rows:
        row_id = get_row_id(row)
        validation_target = determine_validation_target(row, source_hint)
        target_counts[validation_target] += 1
        service_space = evaluate_service_candidate_space(row)
        api_space = evaluate_api_candidate_space(row)
        row_result = {
            "row_id": row_id,
            "validation_target": validation_target,
            "service_candidate_space_valid": service_space["valid"],
            "api_candidate_space_valid": api_space["valid"],
            "service_candidate_count": service_space["candidate_count"],
            "service_gold_count": service_space["gold_count"],
            "api_candidate_count": api_space["candidate_count"],
            "api_gold_count": api_space["gold_count"],
        }
        row_results.append(row_result)

        missing_core = [field for field in CORE_REQUIRED_FIELDS if not text(row.get(field))]
        if missing_core:
            pending_count += 1
            issues.append(
                make_issue(
                    "warning",
                    "pending_core_fields",
                    category=category,
                    filename=filename,
                    row_id=row_id,
                    observed=",".join(missing_core),
                    expected="all seven core human fields populated",
                    notes="An unfilled review row is pending, not invalid reviewed input.",
                )
            )

        for field, allowed in ALLOWED_ENUMS.items():
            value = text(row.get(field))
            normalized = normalized_bool(value) if field in {
                "qa_main_benchmark_eligible_now",
                "qa_repair_required",
            } else value
            if value and normalized not in allowed:
                invalid_enum_count += 1
                issues.append(
                    make_issue(
                        "fatal",
                        "invalid_enum",
                        category=category,
                        filename=filename,
                        row_id=row_id,
                        field=field,
                        observed=value,
                        expected=sorted(allowed),
                    )
                )

        if requires_repair_reason(row) and not text(row.get("qa_repair_reason")):
            conditional_missing_count += 1
            issues.append(
                make_issue(
                    "fatal",
                    "conditional_required_field_missing",
                    category=category,
                    filename=filename,
                    row_id=row_id,
                    field="qa_repair_reason",
                    observed="empty",
                    expected="specific repair reason",
                )
            )
        if requires_dependency_evidence(row) and not text(row.get("qa_dependency_chain_evidence")):
            conditional_missing_count += 1
            issues.append(
                make_issue(
                    "fatal",
                    "composable_dependency_evidence_missing",
                    category=category,
                    filename=filename,
                    row_id=row_id,
                    field="qa_dependency_chain_evidence",
                    observed="empty",
                    expected="specific dependency-chain evidence",
                )
            )

        semantic = text(row.get("qa_semantic_usability"))
        action = text(row.get("qa_release_action"))
        eligible = normalized_bool(row.get("qa_main_benchmark_eligible_now"))
        repair = normalized_bool(row.get("qa_repair_required"))
        decision = text(row.get("adjudicated_final_decision"))

        if eligible == "true" and not (semantic == "usable" and action == "keep_as_is"):
            consistency_issue_count += 1
            issues.append(
                make_issue(
                    "fatal",
                    "eligible_without_usable_keep",
                    category=category,
                    filename=filename,
                    row_id=row_id,
                    observed=f"semantic={semantic};action={action}",
                    expected="usable + keep_as_is",
                )
            )
        if action in NON_ELIGIBLE_ACTIONS and eligible == "true":
            consistency_issue_count += 1
            issues.append(
                make_issue(
                    "fatal",
                    "noneligible_action_marked_eligible",
                    category=category,
                    filename=filename,
                    row_id=row_id,
                    field="qa_main_benchmark_eligible_now",
                    observed=action,
                    expected="false",
                )
            )
        if action in REPAIR_ACTIONS and repair != "true":
            consistency_issue_count += 1
            issues.append(
                make_issue(
                    "fatal",
                    "repair_action_without_repair_required",
                    category=category,
                    filename=filename,
                    row_id=row_id,
                    field="qa_repair_required",
                    observed=repair,
                    expected="true",
                )
            )
        if action == "keep_as_is" and repair and repair != "false":
            consistency_issue_count += 1
            issues.append(
                make_issue(
                    "fatal",
                    "keep_as_is_with_repair_required",
                    category=category,
                    filename=filename,
                    row_id=row_id,
                    field="qa_repair_required",
                    observed=repair,
                    expected="false",
                )
            )
        if action and decision and action != decision:
            consistency_issue_count += 1
            issues.append(
                make_issue(
                    "fatal",
                    "final_decision_action_mismatch",
                    category=category,
                    filename=filename,
                    row_id=row_id,
                    observed=decision,
                    expected=action,
                )
            )

        keep_claimed = action == "keep_as_is" or decision == "keep_as_is" or eligible == "true"
        if validation_target == "service":
            if keep_claimed and not service_space["valid"]:
                candidate_space_fatal_count += 1
                issues.append(
                    make_issue(
                        "fatal",
                        "service_candidate_space_invalid_for_keep_as_is",
                        category=category,
                        filename=filename,
                        row_id=row_id,
                        observed=json.dumps(service_space, ensure_ascii=False, sort_keys=True),
                        expected="parseable service candidates with gold subset and at least one non-gold service",
                    )
                )
            if (
                "shortcutsbench" in source_name(row, source_hint)
                and normalized_bool(row.get("candidate_equals_gold")) == "true"
                and not api_space["valid"]
                and any(
                    item["row_id"] == row_id and item["issue_type"] == "api_candidate_space_invalid_for_keep_as_is"
                    for item in issues
                )
            ):
                shortcuts_false_api_block_count += 1
        elif validation_target == "api":
            if keep_claimed and not api_space["valid"]:
                candidate_space_fatal_count += 1
                issues.append(
                    make_issue(
                        "fatal",
                        "api_candidate_space_invalid_for_keep_as_is",
                        category=category,
                        filename=filename,
                        row_id=row_id,
                        observed=json.dumps(api_space, ensure_ascii=False, sort_keys=True),
                        expected="parseable API candidates with gold subset and at least one non-gold API",
                    )
                )
        else:
            issues.append(
                make_issue(
                    "warning",
                    "validation_target_ambiguous",
                    category=category,
                    filename=filename,
                    row_id=row_id,
                    observed=validation_target,
                    expected="explicit service or api prediction target",
                    notes=(
                        f"service_candidate_space_valid={service_space['valid']};"
                        f"api_candidate_space_valid={api_space['valid']}"
                    ),
                )
            )
            if eligible == "true":
                consistency_issue_count += 1
                issues.append(
                    make_issue(
                        "fatal",
                        "eligible_target_level_unresolved",
                        category=category,
                        filename=filename,
                        row_id=row_id,
                        observed=validation_target,
                        expected="resolved service or api target before benchmark eligibility",
                    )
                )

        if eligible == "true" and (
            (validation_target in {"service", "mixed", "unknown"} and service_space["blocking_leak"])
            or (validation_target in {"api", "mixed", "unknown"} and api_space["blocking_leak"])
        ):
            consistency_issue_count += 1
            issues.append(
                make_issue(
                    "fatal",
                    "blocking_leak_marked_eligible",
                    category=category,
                    filename=filename,
                    row_id=row_id,
                    observed=(
                        f"service_blocking={service_space['blocking_leak']};"
                        f"api_blocking={api_space['blocking_leak']}"
                    ),
                    expected="eligible=false",
                )
            )

    fatal_count = sum(item["severity"] == "fatal" for item in issues)
    warning_count = sum(item["severity"] == "warning" for item in issues)
    summary = {
        "validator_version": VALIDATOR_VERSION,
        "generated_at": now_iso(),
        "filename": filename,
        "row_count": len(rows),
        "expected_row_count": expected_rows,
        "pending_count": pending_count,
        "conditional_missing_count": conditional_missing_count,
        "invalid_enum_count": invalid_enum_count,
        "consistency_issue_count": consistency_issue_count,
        "candidate_space_fatal_count": candidate_space_fatal_count,
        "shortcuts_api_equals_gold_fatal_count_after_patch": shortcuts_false_api_block_count,
        "validation_target_distribution": dict(sorted(target_counts.items())),
        "service_candidate_space_valid_count": sum(result["service_candidate_space_valid"] for result in row_results),
        "api_candidate_space_valid_count": sum(result["api_candidate_space_valid"] for result in row_results),
        "fatal_count": fatal_count,
        "warning_count": warning_count,
        "invalid_reviewed_input": fatal_count > 0,
    }
    return summary, issues, row_results


def validate_reviewed_pair(
    original_path: Path,
    reviewed_path: Path,
    *,
    expected_rows: int,
    category: str,
    source_hint: str = "",
) -> tuple[dict[str, Any], list[dict[str, str]], list[dict[str, str]], list[dict[str, Any]]]:
    """Validate a reviewed CSV against its immutable original pack."""

    original_fields, original_rows = read_csv(original_path)
    reviewed_fields, reviewed_rows = read_csv(reviewed_path)
    issues: list[dict[str, str]] = []
    changed_rows: list[dict[str, str]] = []
    id_field = detect_primary_id(original_fields)

    missing_human_columns = [field for field in HUMAN_FIELDS if field not in reviewed_fields]
    if missing_human_columns:
        issues.append(
            make_issue(
                "fatal",
                "missing_human_columns",
                category=category,
                filename=reviewed_path.name,
                observed=",".join(missing_human_columns),
                expected="all v0.3 human columns",
            )
        )

    if not id_field or id_field not in reviewed_fields:
        issues.append(
            make_issue(
                "fatal",
                "missing_primary_id",
                category=category,
                filename=reviewed_path.name,
                observed=id_field,
                expected="review_item_id or task_id",
            )
        )

    original_ids = [text(row.get(id_field)) for row in original_rows] if id_field else []
    reviewed_ids = [text(row.get(id_field)) for row in reviewed_rows] if id_field else []
    if any(not value for value in reviewed_ids):
        issues.append(make_issue("fatal", "empty_primary_id", category=category, filename=reviewed_path.name))
    if len(set(reviewed_ids)) != len(reviewed_ids):
        issues.append(
            make_issue(
                "fatal",
                "duplicate_review_id",
                category=category,
                filename=reviewed_path.name,
                observed=len(reviewed_ids) - len(set(reviewed_ids)),
                expected=0,
            )
        )
    if set(original_ids) != set(reviewed_ids):
        issues.append(
            make_issue(
                "fatal",
                "reviewed_id_set_mismatch",
                category=category,
                filename=reviewed_path.name,
                observed=f"missing={len(set(original_ids)-set(reviewed_ids))};extra={len(set(reviewed_ids)-set(original_ids))}",
                expected="same IDs as original pack",
            )
        )

    original_by_id = {text(row.get(id_field)): row for row in original_rows} if id_field else {}
    editable = set(HUMAN_FIELDS)
    for row in reviewed_rows:
        row_id = text(row.get(id_field)) if id_field else ""
        original = original_by_id.get(row_id)
        if original is None:
            continue
        for field in original_fields:
            if field in editable:
                continue
            before = str(original.get(field, "") or "")
            after = str(row.get(field, "") or "")
            if before == after:
                continue
            changed_rows.append(
                {
                    "source": category,
                    "filename": reviewed_path.name,
                    "row_id": row_id,
                    "field": field,
                    "original_value": before,
                    "reviewed_value": after,
                }
            )
            issues.append(
                make_issue(
                    "fatal",
                    "immutable_field_changed",
                    category=category,
                    filename=reviewed_path.name,
                    row_id=row_id,
                    field=field,
                    observed=after,
                    expected=before,
                )
            )

    row_summary, row_issues, row_results = validate_rows(
        reviewed_rows,
        filename=reviewed_path.name,
        category=category,
        source_hint=source_hint,
        expected_rows=expected_rows,
    )
    issues.extend(row_issues)
    fatal_count = sum(item["severity"] == "fatal" for item in issues)
    warning_count = sum(item["severity"] == "warning" for item in issues)
    pending = row_summary["pending_count"]
    validation_status = "invalid" if fatal_count else ("pending" if pending else "valid")
    summary = {
        **row_summary,
        "found": True,
        "original_path": str(original_path.resolve()),
        "reviewed_path": str(reviewed_path.resolve()),
        "row_count": len(reviewed_rows),
        "pending_count": pending,
        "immutable_field_changed_count": len(changed_rows),
        "json_parse_failure_count": sum(
            item["issue_type"] == "json_parse_failure" for item in issues
        ),
        "fatal_count": fatal_count,
        "warning_count": warning_count,
        "structure_valid": fatal_count == 0,
        "validation_status": validation_status,
    }
    return summary, issues, changed_rows, row_results


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only source-QA reviewed CSV validator v0.4.1."
    )
    parser.add_argument("--mode", choices=["original-pack", "reviewed-pair"], required=True)
    parser.add_argument("--original-csv", required=True)
    parser.add_argument("--reviewed-csv")
    parser.add_argument("--source", required=True)
    parser.add_argument("--expected-rows", type=int, required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    original_path = Path(args.original_csv).resolve()
    if not original_path.exists():
        parser.error(f"Original CSV does not exist: {original_path}")
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.mode == "original-pack":
        _, rows = read_csv(original_path)
        summary, issues, row_results = validate_rows(
            rows,
            filename=original_path.name,
            category=args.source,
            source_hint=args.source,
            expected_rows=args.expected_rows,
        )
        summary["bundle_status"] = "PENDING_HUMAN_ADJUDICATION"
        summary["invalid_reviewed_input"] = False
        changed_rows: list[dict[str, str]] = []
    else:
        if not args.reviewed_csv:
            parser.error("--reviewed-csv is required in reviewed-pair mode")
        reviewed_path = Path(args.reviewed_csv).resolve()
        if not reviewed_path.exists():
            parser.error(f"Reviewed CSV does not exist: {reviewed_path}")
        summary, issues, changed_rows, row_results = validate_reviewed_pair(
            original_path,
            reviewed_path,
            expected_rows=args.expected_rows,
            category=args.source,
            source_hint=args.source,
        )
        summary["bundle_status"] = (
            "INVALID_REVIEWED_INPUT"
            if summary["validation_status"] == "invalid"
            else (
                "PENDING_HUMAN_ADJUDICATION"
                if summary["validation_status"] == "pending"
                else "READY_FOR_SOURCE_FREEZE_ANALYSIS"
            )
        )

    write_json(output_dir / "validation_summary.json", summary)
    write_csv(output_dir / "validation_issues.csv", ISSUE_FIELDS, issues)
    write_csv(output_dir / "row_validation_results.csv", list(row_results[0]) if row_results else ["row_id"], row_results)
    if changed_rows:
        write_csv(output_dir / "changed_rows.csv", CHANGED_ROW_FIELDS, changed_rows)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary.get("validation_status", "pending") != "invalid" else 1


if __name__ == "__main__":
    raise SystemExit(main())
