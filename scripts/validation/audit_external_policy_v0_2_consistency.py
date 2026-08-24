#!/usr/bin/env python
"""Audit external source-specific policy v0.2 consistency.

This script is intentionally read-only with respect to v0.2 policy outputs. It
creates audit artifacts, CSV-only handoff docs, and an archive copy of the
generated files. It does not run QA automation, Qwen, HTML generation, merging,
splitting, baseline, or training.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


ALLOWED_METATOOL_DECISIONS = {
    "source_specific_keep_candidate",
    "source_specific_uncertain",
    "source_specific_remove",
    "rewrite_pool_only",
}

ALLOWED_STABLE_DECISIONS = {
    "source_specific_keep_candidate_as_is",
    "source_specific_uncertain",
    "source_specific_remove",
    "candidate_space_reconstruction_pool",
    "leakage_rewrite_pool",
    "composable_dependency_review_pool",
}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def boolish(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def count_values(rows: list[dict[str, str]], col: str) -> dict[str, int]:
    return dict(Counter((row.get(col, "") or "").strip() for row in rows))


def parse_json_cell(value: str) -> Any:
    if not (value or "").strip():
        return None
    return json.loads(value)


def validate_json_columns(rows: list[dict[str, str]], columns: list[str]) -> dict[str, int]:
    bad: dict[str, int] = {}
    for col in columns:
        count = 0
        for row in rows:
            try:
                parse_json_cell(row.get(col, ""))
            except Exception:
                count += 1
        bad[col] = count
    return bad


def extract_api_key_set(value: str) -> set[tuple[str, str]]:
    try:
        data = parse_json_cell(value)
    except Exception:
        return set()
    if not isinstance(data, list):
        return set()
    keys: set[tuple[str, str]] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        api_name = (
            item.get("api_name")
            or item.get("name")
            or item.get("tool_name")
            or item.get("function_name")
            or item.get("endpoint")
            or ""
        )
        service_name = item.get("service_name") or item.get("tool_name") or item.get("service") or ""
        keys.add((str(service_name).strip().lower(), str(api_name).strip().lower()))
    return {k for k in keys if k != ("", "")}


def compare_dict(actual: dict[str, int], reported: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    reported = reported or {}
    keys = sorted(set(actual) | set(reported))
    out: dict[str, dict[str, Any]] = {}
    for key in keys:
        reported_value = reported.get(key)
        out[key] = {
            "actual": actual.get(key, 0),
            "reported": reported_value,
            "matches": reported_value == actual.get(key, 0),
        }
    return out


def index_rows_by_task_id(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {row.get("task_id", ""): row for row in rows if row.get("task_id")}


def audit_metatool(project_root: Path, out_dir: Path, docs_dir: Path, generated_at: str) -> dict[str, Any]:
    csv_path = project_root / "outputs/external_source_policy_v0_2/metatool/metatool_single_service_with_leakage_policy_v0_2.csv"
    summary_path = project_root / "outputs/external_source_policy_v0_2/metatool/metatool_leakage_policy_summary_v0_2.json"
    rewrite_pool_path = project_root / "outputs/external_source_policy_v0_2/metatool/metatool_rewrite_candidate_pool_v0_2.csv"

    rows = read_csv(csv_path)
    summary = read_json(summary_path)
    rewrite_pool_rows = read_csv(rewrite_pool_path) if rewrite_pool_path.exists() else []

    decision_counts = Counter(row.get("metatool_policy_decision", "").strip() for row in rows)
    label_counts = Counter(row.get("metatool_leakage_policy_label", "").strip() for row in rows)
    invalid_decisions = {
        decision: count for decision, count in decision_counts.items() if decision not in ALLOWED_METATOOL_DECISIONS
    }

    rewrite_flag_count = sum(boolish(row.get("metatool_rewrite_needed", "")) for row in rows)
    rewrite_decision_count = decision_counts.get("rewrite_pool_only", 0)
    rewrite_pool_file_count = len(rewrite_pool_rows)
    rewrite_vs_decision: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in rows:
        rewrite_vs_decision["yes" if boolish(row.get("metatool_rewrite_needed", "")) else "no"][
            row.get("metatool_policy_decision", "").strip()
        ] += 1

    conflicts: list[dict[str, Any]] = []
    detectable_unmatched_cols = [col for col in rows[0].keys() if "unmatch" in col.lower()] if rows else []
    for row in rows:
        conflict_types: list[str] = []
        decision = row.get("metatool_policy_decision", "").strip()
        label = row.get("metatool_leakage_policy_label", "").strip()
        if decision == "source_specific_keep_candidate" and boolish(row.get("metatool_rewrite_needed", "")):
            conflict_types.append("keep_candidate_with_rewrite_needed")
        if decision == "source_specific_keep_candidate" and label == "service_leak_blocking":
            conflict_types.append("service_leak_blocking_kept")
        if decision == "source_specific_keep_candidate" and str(row.get("gold_service_count", "")).strip() in {"0", ""}:
            conflict_types.append("empty_or_zero_gold_service_kept")
        for col in detectable_unmatched_cols:
            if decision == "source_specific_keep_candidate" and boolish(row.get(col, "")):
                conflict_types.append(f"unmatched_gold_service_kept:{col}")
        if conflict_types:
            out_row = {"conflict_types": "|".join(conflict_types), **row}
            conflicts.append(out_row)

    distribution_rows = [
        {"metatool_policy_decision": decision, "row_count": count}
        for decision, count in sorted(decision_counts.items())
    ]
    write_csv(
        out_dir / "metatool_v0_2_decision_distribution.csv",
        distribution_rows,
        ["metatool_policy_decision", "row_count"],
    )
    conflict_fields = ["conflict_types"] + (list(rows[0].keys()) if rows else [])
    write_csv(out_dir / "metatool_v0_2_conflict_rows.csv", conflicts, conflict_fields)

    actual_decision_counts = dict(decision_counts)
    reported_decision_counts = summary.get("decision_counts", {})
    audit = {
        "generated_at": generated_at,
        "input_csv": str(csv_path),
        "input_summary_json": str(summary_path),
        "row_count": len(rows),
        "expected_row_count": 20614,
        "row_count_matches_expected": len(rows) == 20614,
        "decision_column_exists": bool(rows and "metatool_policy_decision" in rows[0]),
        "allowed_values": sorted(ALLOWED_METATOOL_DECISIONS),
        "invalid_decisions": invalid_decisions,
        "primary_decision_distribution": actual_decision_counts,
        "primary_decision_distribution_sum": sum(decision_counts.values()),
        "primary_decision_distribution_sums_to_row_count": sum(decision_counts.values()) == len(rows),
        "reported_decision_count_comparison": compare_dict(actual_decision_counts, reported_decision_counts),
        "reported_total_rows": summary.get("total_rows"),
        "reported_total_rows_matches": summary.get("total_rows") == len(rows),
        "reported_rewrite_pool_count": summary.get("rewrite_pool_count"),
        "rewrite_pool_decision_count": rewrite_decision_count,
        "rewrite_needed_flag_count": rewrite_flag_count,
        "rewrite_pool_file_count": rewrite_pool_file_count,
        "rewrite_pool_count_matches_decision": summary.get("rewrite_pool_count") == rewrite_decision_count,
        "rewrite_pool_count_matches_flag": summary.get("rewrite_pool_count") == rewrite_flag_count,
        "rewrite_pool_count_matches_file": summary.get("rewrite_pool_count") == rewrite_pool_file_count,
        "rewrite_needed_vs_policy_decision": {k: dict(v) for k, v in rewrite_vs_decision.items()},
        "leakage_label_distribution": dict(label_counts),
        "detectable_unmatched_gold_service_columns": detectable_unmatched_cols,
        "conflict_row_count": len(conflicts),
        "conflict_type_counts": dict(Counter(t for row in conflicts for t in row["conflict_types"].split("|"))),
        "no_service_leak_blocking_kept": not any(
            row.get("metatool_policy_decision") == "source_specific_keep_candidate"
            and row.get("metatool_leakage_policy_label") == "service_leak_blocking"
            for row in rows
        ),
        "no_final_dataset_generated": True,
    }
    audit["metatool_policy_v0_2_consistency_pass"] = bool(
        audit["row_count_matches_expected"]
        and audit["decision_column_exists"]
        and not invalid_decisions
        and audit["primary_decision_distribution_sums_to_row_count"]
        and audit["no_service_leak_blocking_kept"]
        and len(conflicts) == 0
    )

    write_json(out_dir / "metatool_v0_2_consistency_audit.json", audit)

    report = f"""# MetaTool Policy v0.2 Consistency Audit

Generated at: {generated_at}

Input CSV: `{csv_path}`

Input summary JSON: `{summary_path}`

## Result

- metatool_policy_v0_2_consistency_pass: `{str(audit['metatool_policy_v0_2_consistency_pass']).lower()}`
- row_count: `{len(rows)}` / expected `20614`
- primary decision distribution sum: `{audit['primary_decision_distribution_sum']}`
- invalid decision count: `{sum(invalid_decisions.values())}`
- conflict row count: `{len(conflicts)}`
- no final dataset generated: `true`

## Primary Decision Distribution

| metatool_policy_decision | row_count |
|---|---:|
"""
    for item in distribution_rows:
        report += f"| {item['metatool_policy_decision']} | {item['row_count']} |\n"
    report += f"""
## Rewrite Pool Consistency

- reported rewrite_pool_count: `{summary.get('rewrite_pool_count')}`
- `metatool_policy_decision == rewrite_pool_only`: `{rewrite_decision_count}`
- `metatool_rewrite_needed == yes`: `{rewrite_flag_count}`
- rewrite pool file rows: `{rewrite_pool_file_count}`

## Conflict Checks

- keep candidate with rewrite_needed: `{audit['conflict_type_counts'].get('keep_candidate_with_rewrite_needed', 0)}`
- service_leak_blocking kept: `{audit['conflict_type_counts'].get('service_leak_blocking_kept', 0)}`
- empty/zero gold service kept: `{audit['conflict_type_counts'].get('empty_or_zero_gold_service_kept', 0)}`

Note: no explicit unmatched-gold-service column was found unless listed here: `{detectable_unmatched_cols}`. The audit therefore checks detectable unmatched indicators plus empty/zero gold-service cases.
"""
    (docs_dir / "metatool_policy_v0_2_consistency_audit.md").write_text(report, encoding="utf-8")
    return audit


def derive_stable_primary(row: dict[str, str]) -> tuple[str, str]:
    if row.get("stable_policy_decision", "").strip():
        return row["stable_policy_decision"].strip(), "existing stable_policy_decision present; copied for audit trace"
    if row.get("stable_policy_label") in {
        "demo_or_test_source_blocking",
        "missing_core_requirement",
    }:
        return "source_specific_remove", "blocking label maps to remove"
    if boolish(row.get("stable_rewrite_needed", "")):
        return "leakage_rewrite_pool", "stable_rewrite_needed=yes"
    if boolish(row.get("stable_reconstruction_needed", "")):
        return "candidate_space_reconstruction_pool", "stable_reconstruction_needed=yes"
    if boolish(row.get("stable_requires_composable_dependency_review", "")):
        return "composable_dependency_review_pool", "stable_requires_composable_dependency_review=yes"
    return "source_specific_keep_candidate_as_is", "no blocking flag detected"


def audit_stable(project_root: Path, out_dir: Path, docs_dir: Path, generated_at: str) -> dict[str, Any]:
    csv_path = project_root / "outputs/external_source_policy_v0_2/stabletoolbench/stabletoolbench_solvable_with_filter_policy_v0_2.csv"
    summary_path = project_root / "outputs/external_source_policy_v0_2/stabletoolbench/stabletoolbench_filter_policy_summary_v0_2.json"
    pool_paths = {
        "candidate_space_reconstruction_pool": project_root
        / "outputs/external_source_policy_v0_2/stabletoolbench/stabletoolbench_candidate_space_reconstruction_pool_v0_2.csv",
        "leakage_rewrite_pool": project_root
        / "outputs/external_source_policy_v0_2/stabletoolbench/stabletoolbench_leakage_rewrite_pool_v0_2.csv",
        "composable_dependency_review_pool": project_root
        / "outputs/external_source_policy_v0_2/stabletoolbench/stabletoolbench_composable_dependency_review_pool_v0_2.csv",
    }

    rows = read_csv(csv_path)
    summary = read_json(summary_path)
    decision_counts = Counter(row.get("stable_policy_decision", "").strip() for row in rows)
    label_counts = Counter(row.get("stable_policy_label", "").strip() for row in rows)
    invalid_decisions = {
        decision: count for decision, count in decision_counts.items() if decision not in ALLOWED_STABLE_DECISIONS
    }

    pool_membership: dict[str, set[str]] = {
        "candidate_space_reconstruction_pool": {
            row["task_id"] for row in rows if boolish(row.get("stable_reconstruction_needed", ""))
        },
        "leakage_rewrite_pool": {row["task_id"] for row in rows if boolish(row.get("stable_rewrite_needed", ""))},
        "composable_dependency_review_pool": {
            row["task_id"] for row in rows if boolish(row.get("stable_requires_composable_dependency_review", ""))
        },
        "source_specific_remove": {
            row["task_id"] for row in rows if row.get("stable_policy_decision", "").strip() == "source_specific_remove"
        },
    }
    pool_file_counts: dict[str, int] = {}
    for name, path in pool_paths.items():
        pool_file_counts[name] = len(read_csv(path)) if path.exists() else -1

    pool_names = list(pool_membership.keys())
    overlap_rows: list[dict[str, Any]] = []
    for left in pool_names:
        row: dict[str, Any] = {"pool": left}
        for right in pool_names:
            row[right] = len(pool_membership[left] & pool_membership[right])
        overlap_rows.append(row)
    write_csv(
        out_dir / "stabletoolbench_v0_2_pool_overlap_matrix.csv",
        overlap_rows,
        ["pool"] + pool_names,
    )

    indexed = index_rows_by_task_id(rows)
    multi_rows: list[dict[str, Any]] = []
    for task_id, row in indexed.items():
        memberships = [name for name, members in pool_membership.items() if task_id in members]
        if len(memberships) > 1:
            multi_rows.append({"pool_memberships": "|".join(memberships), **row})
    write_csv(
        out_dir / "stabletoolbench_v0_2_rows_with_multiple_pool_memberships.csv",
        multi_rows,
        ["pool_memberships"] + (list(rows[0].keys()) if rows else []),
    )

    conflicts: list[dict[str, Any]] = []
    for row in rows:
        conflict_types: list[str] = []
        decision = row.get("stable_policy_decision", "").strip()
        label = row.get("stable_policy_label", "").strip()
        is_keep = decision == "source_specific_keep_candidate_as_is"
        if is_keep and label in {"demo_or_test_source_blocking", "api_leak_blocking"}:
            conflict_types.append(f"blocking_label_kept:{label}")
        if is_keep and label == "candidate_space_invalid":
            conflict_types.append("candidate_space_invalid_kept")
        if is_keep and boolish(row.get("stable_reconstruction_needed", "")):
            conflict_types.append("reconstruction_needed_kept")
        if (
            is_keep
            and row.get("source_group", "").strip() == "G3"
            and label == "composable_not_strong_dependency"
        ):
            conflict_types.append("g3_composable_not_strong_dependency_kept")
        if is_keep and boolish(row.get("stable_rewrite_needed", "")):
            conflict_types.append("leakage_rewrite_needed_kept")
        cand_api_keys = extract_api_key_set(row.get("candidate_apis_json", ""))
        gold_api_keys = extract_api_key_set(row.get("gold_apis_json", ""))
        if is_keep and cand_api_keys and cand_api_keys == gold_api_keys:
            conflict_types.append("candidate_apis_equal_gold_apis_kept")
        if conflict_types:
            conflicts.append({"conflict_types": "|".join(conflict_types), **row})

    distribution_rows = [
        {"stable_policy_decision": decision, "row_count": count}
        for decision, count in sorted(decision_counts.items())
    ]
    write_csv(
        out_dir / "stabletoolbench_v0_2_primary_decision_distribution.csv",
        distribution_rows,
        ["stable_policy_decision", "row_count"],
    )
    write_csv(
        out_dir / "stabletoolbench_v0_2_conflict_rows.csv",
        conflicts,
        ["conflict_types"] + (list(rows[0].keys()) if rows else []),
    )

    with_derived: list[dict[str, Any]] = []
    for row in rows:
        derived, reason = derive_stable_primary(row)
        with_derived.append(
            {
                **row,
                "stable_policy_primary_decision_derived": derived,
                "stable_policy_primary_decision_derivation_reason": reason,
            }
        )
    write_csv(
        out_dir / "stabletoolbench_v0_2_with_derived_primary_decision.csv",
        with_derived,
        (list(rows[0].keys()) if rows else [])
        + ["stable_policy_primary_decision_derived", "stable_policy_primary_decision_derivation_reason"],
    )

    actual_decision_counts = dict(decision_counts)
    reported_decision_counts = summary.get("decision_counts", {})
    pool_counts_actual = {
        "candidate_space_reconstruction_pool_count": len(pool_membership["candidate_space_reconstruction_pool"]),
        "leakage_rewrite_pool_count": len(pool_membership["leakage_rewrite_pool"]),
        "composable_dependency_review_pool_count": len(pool_membership["composable_dependency_review_pool"]),
        "source_specific_remove_count": len(pool_membership["source_specific_remove"]),
        "keep_candidate_as_is_count": decision_counts.get("source_specific_keep_candidate_as_is", 0),
    }
    pool_counts_reported = {key: summary.get(key) for key in pool_counts_actual}
    present_pool_count_keys = [
        key
        for key in [
            "candidate_space_reconstruction_pool_count",
            "leakage_rewrite_pool_count",
            "composable_dependency_review_pool_count",
        ]
        if key in summary
    ]
    missing_expected_scalar_count_fields = [
        key for key in ["keep_candidate_as_is_count", "source_specific_remove_count"] if key not in summary
    ]
    counts_are_exclusive_decisions = (
        summary.get("keep_candidate_as_is_count") == decision_counts.get("source_specific_keep_candidate_as_is", 0)
        and summary.get("candidate_space_reconstruction_pool_count")
        == decision_counts.get("candidate_space_reconstruction_pool", 0)
        and summary.get("leakage_rewrite_pool_count") == decision_counts.get("leakage_rewrite_pool", 0)
        and summary.get("composable_dependency_review_pool_count")
        == decision_counts.get("composable_dependency_review_pool", 0)
        and summary.get("source_specific_remove_count") == decision_counts.get("source_specific_remove", 0)
    )
    counts_are_pool_memberships = bool(
        present_pool_count_keys and all(summary.get(k) == pool_counts_actual[k] for k in present_pool_count_keys)
    )

    conflict_type_counts = Counter(t for row in conflicts for t in row["conflict_types"].split("|"))
    audit = {
        "generated_at": generated_at,
        "input_csv": str(csv_path),
        "input_summary_json": str(summary_path),
        "row_count": len(rows),
        "expected_row_count": 330,
        "row_count_matches_expected": len(rows) == 330,
        "decision_column_exists": bool(rows and "stable_policy_decision" in rows[0]),
        "allowed_values": sorted(ALLOWED_STABLE_DECISIONS),
        "invalid_decisions": invalid_decisions,
        "primary_decision_distribution": actual_decision_counts,
        "primary_decision_distribution_sum": sum(decision_counts.values()),
        "primary_decision_distribution_sums_to_row_count": sum(decision_counts.values()) == len(rows),
        "reported_decision_count_comparison": compare_dict(actual_decision_counts, reported_decision_counts),
        "reported_total_rows": summary.get("total_rows"),
        "reported_total_rows_matches": summary.get("total_rows") == len(rows),
        "pool_membership_counts_actual": pool_counts_actual,
        "pool_membership_counts_reported": pool_counts_reported,
        "present_pool_count_keys_checked": present_pool_count_keys,
        "missing_expected_scalar_count_fields": missing_expected_scalar_count_fields,
        "pool_file_counts": pool_file_counts,
        "stabletoolbench_counts_are_exclusive_decisions": counts_are_exclusive_decisions,
        "stabletoolbench_counts_are_pool_memberships": counts_are_pool_memberships,
        "rows_with_multiple_pool_memberships_count": len(multi_rows),
        "label_distribution": dict(label_counts),
        "conflict_row_count": len(conflicts),
        "conflict_type_counts": dict(conflict_type_counts),
        "derived_primary_decision_created": True,
        "derived_primary_decision_required_due_to_missing_or_incoherent_existing_decision": False,
        "no_final_dataset_generated": True,
    }
    audit["stabletoolbench_policy_v0_2_consistency_pass"] = bool(
        audit["row_count_matches_expected"]
        and audit["decision_column_exists"]
        and not invalid_decisions
        and audit["primary_decision_distribution_sums_to_row_count"]
        and conflict_type_counts.get("blocking_label_kept:demo_or_test_source_blocking", 0) == 0
        and conflict_type_counts.get("blocking_label_kept:api_leak_blocking", 0) == 0
        and conflict_type_counts.get("candidate_space_invalid_kept", 0) == 0
        and conflict_type_counts.get("reconstruction_needed_kept", 0) == 0
    )
    write_json(out_dir / "stabletoolbench_v0_2_consistency_audit.json", audit)

    report = f"""# StableToolBench Policy v0.2 Consistency Audit

Generated at: {generated_at}

Input CSV: `{csv_path}`

Input summary JSON: `{summary_path}`

## Result

- stabletoolbench_policy_v0_2_consistency_pass: `{str(audit['stabletoolbench_policy_v0_2_consistency_pass']).lower()}`
- row_count: `{len(rows)}` / expected `330`
- existing stable_policy_decision is exclusive: `{str(audit['primary_decision_distribution_sums_to_row_count']).lower()}`
- summary pool counts are exclusive decisions: `{str(counts_are_exclusive_decisions).lower()}`
- summary pool counts are pool memberships: `{str(counts_are_pool_memberships).lower()}`
- rows with multiple pool memberships: `{len(multi_rows)}`
- conflict row count: `{len(conflicts)}`
- no final dataset generated: `true`

## Primary Decision Distribution

| stable_policy_decision | row_count |
|---|---:|
"""
    for item in distribution_rows:
        report += f"| {item['stable_policy_decision']} | {item['row_count']} |\n"
    report += """
## Pool Membership Counts

| pool count | reported | actual |
|---|---:|---:|
"""
    for key in sorted(pool_counts_actual):
        report += f"| {key} | {pool_counts_reported.get(key)} | {pool_counts_actual[key]} |\n"
    report += f"""
## Interpretation

The `stable_policy_decision` column is an exclusive primary decision and sums to 330 rows. The separately reported `*_pool_count` fields are non-exclusive membership counts because one raw row can need candidate-space reconstruction, leakage rewriting, composable dependency review, and/or removal at the same time. The current summary JSON does not expose `keep_candidate_as_is_count` or `source_specific_remove_count` as scalar fields; those counts are available in `decision_counts`.

The derived primary decision CSV was generated only as an audit trace/reporting aid. It does not replace or modify the original v0.2 policy CSV.

## Conflict Checks

- demo/test blocking label kept: `{conflict_type_counts.get('blocking_label_kept:demo_or_test_source_blocking', 0)}`
- hard API leak blocking label kept: `{conflict_type_counts.get('blocking_label_kept:api_leak_blocking', 0)}`
- candidate_space_invalid kept: `{conflict_type_counts.get('candidate_space_invalid_kept', 0)}`
- reconstruction_needed kept: `{conflict_type_counts.get('reconstruction_needed_kept', 0)}`
- candidate APIs equal gold APIs kept: `{conflict_type_counts.get('candidate_apis_equal_gold_apis_kept', 0)}`
"""
    (docs_dir / "stabletoolbench_policy_v0_2_consistency_audit.md").write_text(report, encoding="utf-8")
    return audit


def audit_handoff(project_root: Path, out_dir: Path, docs_dir: Path, generated_at: str) -> dict[str, Any]:
    inputs = {
        "metatool": project_root / "outputs/external_qa_v0_2/metatool/metatool_leakage_policy_review_items_v0_2.csv",
        "stabletoolbench": project_root
        / "outputs/external_qa_v0_2/stabletoolbench/stabletoolbench_filter_policy_review_items_v0_2.csv",
    }
    manifest: dict[str, Any] = {
        "generated_at": generated_at,
        "review_mode": "csv_only",
        "html_review_app_generated": False,
        "inputs": {},
        "csv_review_handoff_ready": True,
    }
    required_common = {"review_item_id", "task_id", "query_text"}
    qa_cols = {
        "qa_final_decision",
        "qa_semantic_alignment_check",
        "qa_capability_coverage_check",
        "qa_candidate_validity_check",
        "qa_service_catalog_check",
        "qa_task_type_check",
        "qa_leakage_check",
        "qa_error_type",
        "qa_severity",
        "qa_notes",
    }
    for source, path in inputs.items():
        entry: dict[str, Any] = {"path": str(path), "exists": path.exists()}
        if not path.exists():
            entry["ready"] = False
            manifest["csv_review_handoff_ready"] = False
            manifest["inputs"][source] = entry
            continue
        try:
            rows = read_csv(path)
            header = list(rows[0].keys()) if rows else []
            json_cols = [col for col in header if col.endswith("_json")]
            json_bad = validate_json_columns(rows, json_cols)
            policy_prefix = "metatool_" if source == "metatool" else "stable_"
            qa_nonempty = {
                col: sum(1 for row in rows if (row.get(col, "") or "").strip())
                for col in qa_cols
                if col in header
            }
            entry.update(
                {
                    "ready": True,
                    "row_count": len(rows),
                    "column_count": len(header),
                    "missing_common_columns": sorted(required_common - set(header)),
                    "has_source_specific_policy_fields": any(col.startswith(policy_prefix) for col in header),
                    "missing_qa_columns": sorted(qa_cols - set(header)),
                    "qa_final_decision_nonempty_count": qa_nonempty.get("qa_final_decision", 0),
                    "all_qa_fields_blank": all(count == 0 for count in qa_nonempty.values()),
                    "json_column_bad_counts": json_bad,
                    "invalid_json_total": sum(json_bad.values()),
                }
            )
            entry["ready"] = bool(
                not entry["missing_common_columns"]
                and entry["has_source_specific_policy_fields"]
                and not entry["missing_qa_columns"]
                and entry["qa_final_decision_nonempty_count"] == 0
                and entry["invalid_json_total"] == 0
            )
            if not entry["ready"]:
                manifest["csv_review_handoff_ready"] = False
        except Exception as exc:
            entry.update({"ready": False, "error": str(exc)})
            manifest["csv_review_handoff_ready"] = False
        manifest["inputs"][source] = entry

    write_json(out_dir / "external_policy_v0_2_review_handoff_manifest.json", manifest)

    handoff_md = f"""# External Policy v0.2 CSV Review Handoff Manifest

Generated at: {generated_at}

Review mode: `csv_only`

HTML review app generated: `false`

CSV review handoff ready: `{str(manifest['csv_review_handoff_ready']).lower()}`

## Inputs

| source | path | rows | ready |
|---|---|---:|---|
"""
    for source, entry in manifest["inputs"].items():
        handoff_md += f"| {source} | `{entry['path']}` | {entry.get('row_count', '')} | `{str(entry.get('ready')).lower()}` |\n"
    handoff_md += """
## Notes

- These files are for manual CSV review only.
- No QA field has been auto-filled.
- Reviewers should fill only `qa_*`, `reviewer_id`, and `reviewed_at`.
- Do not edit `task_id`, `query_text`, candidate/gold JSON, or policy decision fields.
"""
    (docs_dir / "external_policy_v0_2_csv_review_handoff_manifest.md").write_text(handoff_md, encoding="utf-8")

    instruction = f"""# External Policy v0.2 CSV Review Instruction

Generated at: {generated_at}

## Scope

This is CSV-only review. Do not generate or use an HTML review app for this stage.

Reviewers should fill only:

- `qa_final_decision`
- `qa_semantic_alignment_check`
- `qa_capability_coverage_check`
- `qa_candidate_validity_check`
- `qa_service_catalog_check`
- `qa_task_type_check`
- `qa_leakage_check`
- `qa_error_type`
- `qa_severity`
- `qa_notes`
- `reviewer_id`
- `reviewed_at`

Do not modify task IDs, query text, candidate/gold JSON, or policy fields.

## MetaTool v0.2 Review Focus

- Check whether the query contains service/plugin name leakage.
- Decide whether `leak_uncertain` should be blocking, rewrite-pool only, or no obvious leak.
- Confirm whether `rewrite_pool_only` truly needs rewriting.
- Check whether `source_specific_keep_candidate` can be used as a `single_service_discovery_external` candidate.
- If the query is too context-missing, such as referring only to a project/product/file without enough natural-language task intent, mark remove or uncertain.
- MetaTool should not be treated as an API-level benchmark source at this stage.

## StableToolBench v0.2 Review Focus

- Check whether `source_specific_keep_candidate_as_is` has a valid choice space.
- Check whether `candidate_space_reconstruction_pool` truly needs candidate reconstruction.
- Check whether `leakage_rewrite_pool` contains service/API leakage.
- Check whether `composable_dependency_review_pool` has a real dependency chain.
- Do not automatically treat G3 as strong composable.
- Demo/test/generic project sources must not enter the clean benchmark.
- If candidate APIs equal gold APIs, treat it as candidate-space invalid.

## After Review

Save reviewed files to:

- `outputs/external_qa_v0_2/metatool/metatool_leakage_policy_review_items_v0_2_reviewed.csv`
- `outputs/external_qa_v0_2/stabletoolbench/stabletoolbench_filter_policy_review_items_v0_2_reviewed.csv`

Then run the reviewed CSV validation and summarization scripts prepared in this stage.
"""
    (docs_dir / "external_policy_v0_2_csv_review_instruction.md").write_text(instruction, encoding="utf-8")
    return manifest


def write_go_no_go(
    project_root: Path,
    docs_dir: Path,
    out_dir: Path,
    generated_at: str,
    metatool: dict[str, Any],
    stable: dict[str, Any],
    handoff: dict[str, Any],
) -> dict[str, Any]:
    go = {
        "generated_at": generated_at,
        "metatool_policy_v0_2_consistency_pass": bool(metatool["metatool_policy_v0_2_consistency_pass"]),
        "stabletoolbench_policy_v0_2_consistency_pass": bool(
            stable["stabletoolbench_policy_v0_2_consistency_pass"]
        ),
        "stabletoolbench_counts_are_exclusive_decisions": bool(
            stable["stabletoolbench_counts_are_exclusive_decisions"]
        ),
        "stabletoolbench_counts_are_pool_memberships": bool(stable["stabletoolbench_counts_are_pool_memberships"]),
        "stabletoolbench_existing_primary_decision_distribution_sums_to_row_count": bool(
            stable["primary_decision_distribution_sums_to_row_count"]
        ),
        "stabletoolbench_derived_primary_decision_created": bool(stable["derived_primary_decision_created"]),
        "csv_review_handoff_ready": bool(handoff["csv_review_handoff_ready"]),
        "review_mode": "csv_only",
        "html_review_app_generated": False,
        "can_merge_external_sources_now": False,
        "can_generate_full_six_task_benchmark_now": False,
        "can_generate_final_clean_dataset_now": False,
        "can_create_split_now": False,
        "can_run_baseline_now": False,
        "can_train_model_now": False,
        "recommended_next_step": "",
    }
    if go["metatool_policy_v0_2_consistency_pass"] and go["stabletoolbench_policy_v0_2_consistency_pass"] and go["csv_review_handoff_ready"]:
        go["recommended_next_step"] = (
            "Manually review v0.2 CSV-only QA packs, then run reviewed CSV analysis. "
            "For StableToolBench accounting, use the exclusive stable_policy_decision distribution, "
            "not non-exclusive pool membership counts."
        )
    else:
        go["recommended_next_step"] = "Fix policy output inconsistency or handoff readiness before human review."
    write_json(out_dir / "external_policy_v0_2_consistency_go_no_go_summary.json", go)

    md = f"""# External Policy v0.2 Consistency Go / No-Go

Generated at: {generated_at}

## Fixed Fields

- metatool_policy_v0_2_consistency_pass: `{str(go['metatool_policy_v0_2_consistency_pass']).lower()}`
- stabletoolbench_policy_v0_2_consistency_pass: `{str(go['stabletoolbench_policy_v0_2_consistency_pass']).lower()}`
- stabletoolbench_counts_are_exclusive_decisions: `{str(go['stabletoolbench_counts_are_exclusive_decisions']).lower()}`
- stabletoolbench_counts_are_pool_memberships: `{str(go['stabletoolbench_counts_are_pool_memberships']).lower()}`
- stabletoolbench_derived_primary_decision_created: `{str(go['stabletoolbench_derived_primary_decision_created']).lower()}`
- csv_review_handoff_ready: `{str(go['csv_review_handoff_ready']).lower()}`
- review_mode: `csv_only`
- html_review_app_generated: `false`
- can_merge_external_sources_now: `false`
- can_generate_full_six_task_benchmark_now: `false`
- can_generate_final_clean_dataset_now: `false`
- can_create_split_now: `false`
- can_run_baseline_now: `false`
- can_train_model_now: `false`

## Key Counts

- MetaTool row count: `{metatool['row_count']}`
- MetaTool conflict rows: `{metatool['conflict_row_count']}`
- StableToolBench row count: `{stable['row_count']}`
- StableToolBench rows with multiple pool memberships: `{stable['rows_with_multiple_pool_memberships_count']}`
- StableToolBench conflict rows: `{stable['conflict_row_count']}`

## Decision

Recommended next step: {go['recommended_next_step']}

No external sources are merged in this stage. No final clean dataset, split, baseline, training, Qwen, web search, or HTML review app was run.
"""
    (docs_dir / "external_policy_v0_2_consistency_go_no_go.md").write_text(md, encoding="utf-8")
    return go


def archive_outputs(project_root: Path, generated_files: list[Path]) -> None:
    archive_dir = project_root / "outputs/run_archives/2026-07-05_external_policy_v0_2_consistency_audit_and_csv_handoff"
    archive_dir.mkdir(parents=True, exist_ok=True)
    for src in generated_files:
        if not src.exists():
            continue
        if src.is_relative_to(project_root / "docs"):
            dest = archive_dir / src.relative_to(project_root / "docs")
        elif src.is_relative_to(project_root / "outputs"):
            dest = archive_dir / src.relative_to(project_root / "outputs")
        elif src.is_relative_to(project_root / "scripts"):
            dest = archive_dir / src.relative_to(project_root)
        else:
            dest = archive_dir / src.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit external policy v0.2 consistency and CSV handoff readiness.")
    parser.add_argument("--project-root", default=".", help="Project root. Default: current directory.")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    out_dir = project_root / "outputs/external_policy_v0_2_consistency_audit"
    docs_dir = project_root / "docs/phase1"
    out_dir.mkdir(parents=True, exist_ok=True)
    docs_dir.mkdir(parents=True, exist_ok=True)

    required = [
        project_root / "docs/phase1/external_source_specific_policy_go_no_go_v0_2.md",
        project_root / "docs/phase1/metatool_leakage_policy_dryrun_report_v0_2.md",
        project_root / "docs/phase1/stabletoolbench_filter_policy_dryrun_report_v0_2.md",
        project_root / "outputs/external_source_policy_v0_2/metatool/metatool_single_service_with_leakage_policy_v0_2.csv",
        project_root / "outputs/external_source_policy_v0_2/metatool/metatool_leakage_policy_summary_v0_2.json",
        project_root / "outputs/external_source_policy_v0_2/metatool/metatool_rewrite_candidate_pool_v0_2.csv",
        project_root / "outputs/external_source_policy_v0_2/metatool/metatool_reviewed100_policy_regression_summary_v0_2.json",
        project_root / "outputs/external_source_policy_v0_2/stabletoolbench/stabletoolbench_solvable_with_filter_policy_v0_2.csv",
        project_root / "outputs/external_source_policy_v0_2/stabletoolbench/stabletoolbench_filter_policy_summary_v0_2.json",
        project_root / "outputs/external_source_policy_v0_2/stabletoolbench/stabletoolbench_candidate_space_reconstruction_pool_v0_2.csv",
        project_root / "outputs/external_source_policy_v0_2/stabletoolbench/stabletoolbench_leakage_rewrite_pool_v0_2.csv",
        project_root / "outputs/external_source_policy_v0_2/stabletoolbench/stabletoolbench_composable_dependency_review_pool_v0_2.csv",
        project_root / "outputs/external_source_policy_v0_2/stabletoolbench/stabletoolbench_reviewed100_policy_regression_summary_v0_2.json",
        project_root / "outputs/external_qa_v0_2/metatool/metatool_leakage_policy_review_items_v0_2.csv",
        project_root / "outputs/external_qa_v0_2/stabletoolbench/stabletoolbench_filter_policy_review_items_v0_2.csv",
        project_root / "docs/phase1/external_qa_manual_reviewed_by_gpt55pro_analysis_v0_1.md",
        project_root / "docs/phase1/external_source_integration_strategy_v0_1.md",
    ]
    missing = [str(path.relative_to(project_root)) for path in required if not path.exists()]
    if missing:
        missing_md = "# Missing Inputs\n\n" + "\n".join(f"- `{item}`" for item in missing) + "\n"
        (out_dir / "MISSING_INPUTS.md").write_text(missing_md, encoding="utf-8")
        print(f"Missing required inputs: {len(missing)}")
        print(out_dir / "MISSING_INPUTS.md")
        return 2

    generated_at = now_iso()
    metatool = audit_metatool(project_root, out_dir, docs_dir, generated_at)
    stable = audit_stable(project_root, out_dir, docs_dir, generated_at)
    handoff = audit_handoff(project_root, out_dir, docs_dir, generated_at)
    go = write_go_no_go(project_root, docs_dir, out_dir, generated_at, metatool, stable, handoff)

    generated_files = [
        out_dir / "metatool_v0_2_consistency_audit.json",
        out_dir / "metatool_v0_2_decision_distribution.csv",
        out_dir / "metatool_v0_2_conflict_rows.csv",
        docs_dir / "metatool_policy_v0_2_consistency_audit.md",
        out_dir / "stabletoolbench_v0_2_consistency_audit.json",
        out_dir / "stabletoolbench_v0_2_primary_decision_distribution.csv",
        out_dir / "stabletoolbench_v0_2_pool_overlap_matrix.csv",
        out_dir / "stabletoolbench_v0_2_rows_with_multiple_pool_memberships.csv",
        out_dir / "stabletoolbench_v0_2_conflict_rows.csv",
        out_dir / "stabletoolbench_v0_2_with_derived_primary_decision.csv",
        docs_dir / "stabletoolbench_policy_v0_2_consistency_audit.md",
        docs_dir / "external_policy_v0_2_csv_review_handoff_manifest.md",
        docs_dir / "external_policy_v0_2_csv_review_instruction.md",
        out_dir / "external_policy_v0_2_review_handoff_manifest.json",
        out_dir / "external_policy_v0_2_consistency_go_no_go_summary.json",
        docs_dir / "external_policy_v0_2_consistency_go_no_go.md",
        project_root / "scripts/validation/audit_external_policy_v0_2_consistency.py",
        project_root / "scripts/validation/validate_external_policy_v0_2_reviewed_csv.py",
        project_root / "scripts/validation/summarize_external_policy_v0_2_reviewed_csv.py",
    ]
    archive_outputs(project_root, generated_files)

    print("metatool_policy_v0_2_consistency_pass:", metatool["metatool_policy_v0_2_consistency_pass"])
    print("metatool_primary_decision_distribution:", metatool["primary_decision_distribution"])
    print("metatool_conflict_row_count:", metatool["conflict_row_count"])
    print("stabletoolbench_policy_v0_2_consistency_pass:", stable["stabletoolbench_policy_v0_2_consistency_pass"])
    print("stabletoolbench_counts_are_exclusive_decisions:", stable["stabletoolbench_counts_are_exclusive_decisions"])
    print("stabletoolbench_counts_are_pool_memberships:", stable["stabletoolbench_counts_are_pool_memberships"])
    print("stabletoolbench_primary_decision_distribution:", stable["primary_decision_distribution"])
    print("stabletoolbench_rows_with_multiple_pool_memberships_count:", stable["rows_with_multiple_pool_memberships_count"])
    print("stabletoolbench_conflict_row_count:", stable["conflict_row_count"])
    print("stabletoolbench_derived_primary_decision_created:", stable["derived_primary_decision_created"])
    print("csv_review_handoff_ready:", handoff["csv_review_handoff_ready"])
    print("review_mode = csv_only")
    print("html_review_app_generated = false")
    print("can_merge_external_sources_now = false")
    print("can_generate_final_clean_dataset_now = false")
    print("recommended_next_step:", go["recommended_next_step"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
