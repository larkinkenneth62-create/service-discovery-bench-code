#!/usr/bin/env python
"""Source-QA reviewed CSV validator v0.4.2.

Adds final-adjudication leakage precedence, explicit prediction target, and
structured adjudicator provenance while preserving the v0.4.1 candidate-space
rules. The validator is read-only and never fills human fields.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any, Sequence

try:
    from source_qa_review_validator_v0_4_1 import (
        ALLOWED_ENUMS as V041_ALLOWED_ENUMS,
        CHANGED_ROW_FIELDS,
        CORE_REQUIRED_FIELDS as V041_CORE_FIELDS,
        HUMAN_FIELDS as V041_HUMAN_FIELDS,
        ISSUE_FIELDS,
        detect_primary_id,
        determine_validation_target as determine_v041_target,
        get_row_id,
        make_issue,
        normalized_bool,
        now_iso,
        read_csv,
        text,
        validate_rows as validate_rows_v0_4_1,
        write_csv,
        write_json,
    )
except ImportError:
    from scripts.validation.source_qa_review_validator_v0_4_1 import (
        ALLOWED_ENUMS as V041_ALLOWED_ENUMS,
        CHANGED_ROW_FIELDS,
        CORE_REQUIRED_FIELDS as V041_CORE_FIELDS,
        HUMAN_FIELDS as V041_HUMAN_FIELDS,
        ISSUE_FIELDS,
        detect_primary_id,
        determine_validation_target as determine_v041_target,
        get_row_id,
        make_issue,
        normalized_bool,
        now_iso,
        read_csv,
        text,
        validate_rows as validate_rows_v0_4_1,
        write_csv,
        write_json,
    )


VALIDATOR_VERSION = "v0.4.2"

NEW_HUMAN_FIELDS = [
    "adjudicated_leakage_status",
    "adjudicated_prediction_target",
    "adjudicator_type",
]

HUMAN_FIELDS = V041_HUMAN_FIELDS + NEW_HUMAN_FIELDS
CORE_REQUIRED_FIELDS = V041_CORE_FIELDS + NEW_HUMAN_FIELDS

V042_ALLOWED_ENUMS = {
    "adjudicated_leakage_status": {
        "no_obvious_leak",
        "service_leak_blocking",
        "api_leak_blocking",
        "leak_uncertain",
        "not_applicable",
    },
    "adjudicated_prediction_target": {
        "service",
        "api",
        "both",
        "source_only",
        "not_applicable",
    },
    "adjudicator_type": {
        "human_confirmed",
        "human_with_model_assistance",
        "model_pilot_only",
    },
}

PRIOR_LEAK_FIELDS = [
    "qa_leakage_check",
    "prior_qa_leakage_check",
    "metatool_leakage_policy_label",
    "stable_policy_label",
    "api_leak_detector_status",
    "service_leak_detector_status",
    "leakage_check_status",
    "explicit_service_leak_detected",
    "explicit_api_leak_detected",
]


def prior_blocking_levels(row: dict[str, Any]) -> set[str]:
    levels: set[str] = set()
    combined = " ".join(text(row.get(field)).lower() for field in PRIOR_LEAK_FIELDS)
    if "service_leak_blocking" in combined or normalized_bool(
        row.get("explicit_service_leak_detected")
    ) == "true":
        levels.add("service")
    if "api_leak_blocking" in combined or normalized_bool(
        row.get("explicit_api_leak_detected")
    ) == "true":
        levels.add("api")
    return levels


def final_target_for_validation(row: dict[str, Any], source_hint: str = "") -> str:
    final_target = text(row.get("adjudicated_prediction_target")).lower()
    if final_target in {"service", "api"}:
        return final_target
    if final_target == "both":
        return "mixed"
    if final_target in {"source_only", "not_applicable"}:
        return "unknown"
    return determine_v041_target(row, source_hint)


def transform_for_v041(row: dict[str, Any], source_hint: str = "") -> dict[str, Any]:
    """Create a validation-only copy so prior leakage cannot override final human status."""

    transformed = dict(row)
    for field in PRIOR_LEAK_FIELDS:
        transformed[field] = ""

    final_leakage = text(row.get("adjudicated_leakage_status")).lower()
    if final_leakage in {"service_leak_blocking", "api_leak_blocking"}:
        transformed["qa_leakage_check"] = final_leakage

    final_target = text(row.get("adjudicated_prediction_target")).lower()
    if final_target in {"service", "api"}:
        transformed["prediction_level"] = final_target
    elif final_target in {"both", "source_only", "not_applicable"}:
        # Force the v0.4.1 layer to report both candidate-space views without
        # pretending that either is the final benchmark prediction target.
        transformed["prediction_level"] = ""
        transformed["task_type"] = ""
        transformed["task_type_guess"] = "multi_service_or_multi_api_candidate"
        transformed["source_dataset"] = "StableToolBench"
        transformed["stable_group"] = "G2"
        transformed["source_group"] = "G2"
    return transformed


def maps_to_human_confirmed_gold(row: dict[str, Any]) -> bool:
    boolean_fields = [
        "human_confirmed_gold",
        "is_human_confirmed_gold",
        "gold_test_eligible",
    ]
    if any(normalized_bool(row.get(field)) == "true" for field in boolean_fields):
        return True
    provenance_fields = [
        "provenance_type",
        "reviewer_provenance",
        "label_provenance",
        "gold_provenance",
        "release_provenance",
    ]
    return any(
        text(row.get(field)).lower() == "human_confirmed_gold"
        for field in provenance_fields
    )


def validate_rows(
    rows: Sequence[dict[str, Any]],
    *,
    filename: str = "",
    category: str = "source_qa_reviewed",
    source_hint: str = "",
    expected_rows: int | None = None,
    reviewed_mode: bool = False,
) -> tuple[dict[str, Any], list[dict[str, str]], list[dict[str, Any]]]:
    transformed_rows = [transform_for_v041(row, source_hint) for row in rows]
    base_summary, base_issues, row_results = validate_rows_v0_4_1(
        transformed_rows,
        filename=filename,
        category=category,
        source_hint=source_hint,
        expected_rows=expected_rows,
    )

    # v0.4.2 owns completion status. Remove the v0.4.1 seven-field warning.
    issues = [item for item in base_issues if item["issue_type"] != "pending_core_fields"]
    pending_count = 0
    invalid_enum_count = base_summary.get("invalid_enum_count", 0)
    final_target_distribution: Counter[str] = Counter()
    override_count = 0
    precedence_issue_count = 0
    provenance_issue_count = 0

    for index, row in enumerate(rows):
        row_id = get_row_id(row)
        missing = [field for field in CORE_REQUIRED_FIELDS if not text(row.get(field))]
        started = any(text(row.get(field)) for field in CORE_REQUIRED_FIELDS)
        if missing:
            pending_count += 1
            issues.append(
                make_issue(
                    "warning",
                    "pending_core_fields_v0_4_2",
                    category=category,
                    filename=filename,
                    row_id=row_id,
                    observed=",".join(missing),
                    expected="all v0.4.2 core fields populated",
                )
            )
            if reviewed_mode and started and any(field in missing for field in NEW_HUMAN_FIELDS):
                issues.append(
                    make_issue(
                        "fatal",
                        "completed_row_missing_v0_4_2_fields",
                        category=category,
                        filename=filename,
                        row_id=row_id,
                        observed=",".join(field for field in missing if field in NEW_HUMAN_FIELDS),
                        expected="final leakage status, prediction target, and adjudicator type",
                    )
                )

        for field, allowed in V042_ALLOWED_ENUMS.items():
            value = text(row.get(field))
            if value and value not in allowed:
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

        final_leakage = text(row.get("adjudicated_leakage_status")).lower()
        prior_blocking = prior_blocking_levels(row)
        notes = text(row.get("adjudication_notes"))
        action = text(row.get("qa_release_action"))
        decision = text(row.get("adjudicated_final_decision"))
        eligible = normalized_bool(row.get("qa_main_benchmark_eligible_now"))

        if final_leakage == "no_obvious_leak" and prior_blocking:
            override_count += 1
            issues.append(
                make_issue(
                    "warning",
                    "adjudication_overrides_prior_blocking_evidence",
                    category=category,
                    filename=filename,
                    row_id=row_id,
                    observed=",".join(sorted(prior_blocking)),
                    expected="documented human override",
                )
            )
            if not notes:
                precedence_issue_count += 1
                issues.append(
                    make_issue(
                        "fatal",
                        "adjudication_override_explanation_missing",
                        category=category,
                        filename=filename,
                        row_id=row_id,
                        field="adjudication_notes",
                        observed="empty",
                        expected="non-empty explanation for overriding prior blocking evidence",
                    )
                )

        if final_leakage in {"service_leak_blocking", "api_leak_blocking"}:
            if eligible == "true" or action == "keep_as_is" or decision == "keep_as_is":
                precedence_issue_count += 1
                issues.append(
                    make_issue(
                        "fatal",
                        "final_blocking_leak_release_violation",
                        category=category,
                        filename=filename,
                        row_id=row_id,
                        observed=f"leak={final_leakage};action={action};decision={decision};eligible={eligible}",
                        expected="eligible=false and non-keep release action",
                    )
                )
        elif final_leakage == "leak_uncertain":
            if eligible == "true" or action == "keep_as_is" or decision == "keep_as_is":
                precedence_issue_count += 1
                issues.append(
                    make_issue(
                        "fatal",
                        "uncertain_leak_direct_release_violation",
                        category=category,
                        filename=filename,
                        row_id=row_id,
                        observed=f"action={action};decision={decision};eligible={eligible}",
                        expected="hold/rewrite/reconstruct/dependency_review/remove with eligible=false",
                    )
                )

        final_target = text(row.get("adjudicated_prediction_target")).lower()
        final_target_distribution[final_target or "unfilled"] += 1
        if final_target in {"both", "source_only", "not_applicable"} and eligible == "true":
            precedence_issue_count += 1
            issues.append(
                make_issue(
                    "fatal",
                    "source_level_target_marked_benchmark_eligible",
                    category=category,
                    filename=filename,
                    row_id=row_id,
                    observed=final_target,
                    expected="eligible=false until assembly creates explicit service/API benchmark rows",
                )
            )
        if eligible == "true" and final_target not in {"service", "api"}:
            precedence_issue_count += 1
            issues.append(
                make_issue(
                    "fatal",
                    "eligible_final_target_unresolved",
                    category=category,
                    filename=filename,
                    row_id=row_id,
                    observed=final_target or "empty",
                    expected="service or api",
                )
            )

        adjudicator_type = text(row.get("adjudicator_type")).lower()
        maps_gold = maps_to_human_confirmed_gold(row)
        if adjudicator_type == "model_pilot_only" and maps_gold:
            provenance_issue_count += 1
            issues.append(
                make_issue(
                    "fatal",
                    "model_pilot_cannot_be_human_confirmed_gold",
                    category=category,
                    filename=filename,
                    row_id=row_id,
                    observed="model_pilot_only -> human_confirmed_gold",
                    expected="non-gold model pilot provenance",
                )
            )
        if (
            adjudicator_type == "human_with_model_assistance"
            and maps_gold
            and normalized_bool(row.get("independent_human_confirmation")) != "true"
        ):
            provenance_issue_count += 1
            issues.append(
                make_issue(
                    "fatal",
                    "model_assisted_gold_requires_independent_confirmation",
                    category=category,
                    filename=filename,
                    row_id=row_id,
                    observed="human_with_model_assistance without independent confirmation",
                    expected="independent_human_confirmation=true before Gold test",
                )
            )

        row_results[index]["adjudicated_leakage_status"] = final_leakage
        row_results[index]["adjudicated_prediction_target"] = final_target
        row_results[index]["adjudicator_type"] = adjudicator_type
        row_results[index]["prior_blocking_levels"] = ",".join(sorted(prior_blocking))

    fatal_count = sum(item["severity"] == "fatal" for item in issues)
    warning_count = sum(item["severity"] == "warning" for item in issues)
    summary = {
        **base_summary,
        "validator_version": VALIDATOR_VERSION,
        "pending_count": pending_count,
        "invalid_enum_count": invalid_enum_count,
        "final_target_distribution": dict(sorted(final_target_distribution.items())),
        "adjudication_override_count": override_count,
        "precedence_issue_count": precedence_issue_count,
        "provenance_issue_count": provenance_issue_count,
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
    original_fields, original_rows = read_csv(original_path)
    reviewed_fields, reviewed_rows = read_csv(reviewed_path)
    issues: list[dict[str, str]] = []
    changed_rows: list[dict[str, str]] = []
    id_field = detect_primary_id(original_fields)

    missing_columns = [field for field in HUMAN_FIELDS if field not in reviewed_fields]
    if missing_columns:
        issues.append(
            make_issue(
                "fatal",
                "missing_human_columns_v0_4_2",
                category=category,
                filename=reviewed_path.name,
                observed=",".join(missing_columns),
                expected="all v0.4.2 human fields",
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
                expected="same row IDs as original",
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
            changed = {
                "source": category,
                "filename": reviewed_path.name,
                "row_id": row_id,
                "field": field,
                "original_value": before,
                "reviewed_value": after,
            }
            changed_rows.append(changed)
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
        reviewed_mode=True,
    )
    issues.extend(row_issues)
    fatal_count = sum(item["severity"] == "fatal" for item in issues)
    pending = row_summary["pending_count"]
    status = "invalid" if fatal_count else ("pending" if pending else "valid")
    summary = {
        **row_summary,
        "found": True,
        "original_path": str(original_path.resolve()),
        "reviewed_path": str(reviewed_path.resolve()),
        "row_count": len(reviewed_rows),
        "pending_count": pending,
        "immutable_field_changed_count": len(changed_rows),
        "fatal_count": fatal_count,
        "warning_count": sum(item["severity"] == "warning" for item in issues),
        "structure_valid": fatal_count == 0,
        "validation_status": status,
    }
    return summary, issues, changed_rows, row_results


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only source-QA validator v0.4.2.")
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
            reviewed_mode=False,
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
            else "PENDING_HUMAN_ADJUDICATION"
            if summary["validation_status"] == "pending"
            else "READY_FOR_SOURCE_FREEZE_ANALYSIS"
        )

    write_json(output_dir / "validation_summary.json", summary)
    write_csv(output_dir / "validation_issues.csv", ISSUE_FIELDS, issues)
    write_csv(
        output_dir / "row_validation_results.csv",
        list(row_results[0]) if row_results else ["row_id"],
        row_results,
    )
    if changed_rows:
        write_csv(output_dir / "changed_rows.csv", CHANGED_ROW_FIELDS, changed_rows)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary.get("validation_status", "pending") != "invalid" else 1


if __name__ == "__main__":
    raise SystemExit(main())
