#!/usr/bin/env python
"""Crosswalk current composable review rows and prepare an unreviewed queue.

Evidence availability is not a composable Gold label. This script never fills
human fields, confirms strong composability, or constructs dependency graphs.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


FIELDS = [
    "source_task_id",
    "source_dataset",
    "source_group",
    "current_policy_status",
    "in_current_v0_4_2_review_pack",
    "step_count",
    "tool_sequence_available",
    "api_sequence_available",
    "argument_flow_available",
    "repeated_entity_available",
    "output_input_evidence_available",
    "control_dependency_available",
    "conditional_dependency_available",
    "trace_or_answer_evidence_path",
    "evidence_completeness_status",
    "recovery_priority",
    "requires_human_dependency_review",
]

PACK_PATHS = [
    "outputs/source_qa_adjudication_v0_4_2/metatool/metatool_disagreement_adjudication_items_v0_4_2.csv",
    "outputs/source_qa_adjudication_v0_4_2/toolbench/toolbench_v1_5f_final_targeted_qa_items_v0_4_2.csv",
    "outputs/source_qa_adjudication_v0_4_2/stabletoolbench/stabletoolbench_supplemental_adjudication_items_v0_4_2.csv",
    "outputs/source_qa_adjudication_v0_4_2/shortcutsbench/shortcutsbench_strict_qa_items_v0_4_2.csv",
]


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def as_bool(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def build_pack_index(root: Path) -> tuple[set[str], dict[str, dict[str, str]]]:
    ids: set[str] = set()
    rows_by_id: dict[str, dict[str, str]] = {}
    for relative in PACK_PATHS:
        path = root / relative
        if not path.exists():
            raise FileNotFoundError(f"Required v0.4.2 review pack is missing: {path}")
        for row in read_csv(path):
            task_id = str(row.get("task_id") or "").strip()
            if task_id:
                ids.add(task_id)
                rows_by_id[task_id] = row
    return ids, rows_by_id


def evidence_status(row: dict[str, str]) -> str:
    sequence = as_bool(row.get("tool_api_sequence_available"))
    argument = as_bool(row.get("argument_flow_available"))
    output_input = as_bool(row.get("output_input_evidence_available"))
    repeated = as_bool(row.get("repeated_entity_available"))
    control = as_bool(row.get("control_conditional_evidence_available"))
    source_signal = as_bool(row.get("source_dependency_evidence_available"))
    if sequence and argument and output_input:
        return "strong_objective_evidence_available"
    if argument or output_input or repeated or control or source_signal:
        return "partial_objective_evidence"
    if sequence:
        return "sequence_only"
    if str(row.get("task_id") or "").strip():
        return "no_dependency_evidence"
    return "source_unavailable"


def recovery_priority(row: dict[str, str], status: str) -> str:
    if status == "strong_objective_evidence_available":
        return "P0_objective_evidence_review"
    if status == "partial_objective_evidence":
        return "P1_partial_evidence_review"
    if row.get("source_dataset") == "ToolBench" and row.get("current_policy_status") == "downgrade_to_uncertain":
        return "P2_uncertain_toolbench_recovery"
    if status == "sequence_only":
        return "P2_sequence_inspection"
    return "P3_no_evidence_hold"


def trace_path(row: dict[str, str], pack_row: dict[str, str] | None) -> str:
    if pack_row:
        candidate = str(pack_row.get("solvable_source_path") or "").strip()
        if candidate:
            return candidate
    if as_bool(row.get("trace_or_trajectory_available")) or as_bool(row.get("answer_record_available")):
        models = str(row.get("trace_models_json") or "").strip()
        return f"inventory_trace_models:{models}" if models else "inventory_trace_or_answer_available"
    return ""


def transform(row: dict[str, str], in_pack: bool, pack_row: dict[str, str] | None) -> dict[str, str]:
    status = evidence_status(row)
    combined_control = as_bool(row.get("control_conditional_evidence_available"))
    sequence = as_bool(row.get("tool_api_sequence_available"))
    return {
        "source_task_id": str(row.get("task_id") or "").strip(),
        "source_dataset": str(row.get("source_dataset") or "").strip(),
        "source_group": str(row.get("source_group") or "").strip(),
        "current_policy_status": str(row.get("current_policy_status") or "").strip(),
        "in_current_v0_4_2_review_pack": str(in_pack).lower(),
        "step_count": str(row.get("step_count") or "0"),
        "tool_sequence_available": str(sequence).lower(),
        "api_sequence_available": str(sequence).lower(),
        "argument_flow_available": str(as_bool(row.get("argument_flow_available"))).lower(),
        "repeated_entity_available": str(as_bool(row.get("repeated_entity_available"))).lower(),
        "output_input_evidence_available": str(as_bool(row.get("output_input_evidence_available"))).lower(),
        "control_dependency_available": str(combined_control).lower(),
        "conditional_dependency_available": str(combined_control).lower(),
        "trace_or_answer_evidence_path": trace_path(row, pack_row),
        "evidence_completeness_status": status,
        "recovery_priority": recovery_priority(row, status),
        "requires_human_dependency_review": "true",
    }


def write_report(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# Composable Evidence Crosswalk and Recovery Queue v0.1",
        "",
        f"- Generated at: `{summary['generated_at']}`",
        f"- Inventory input: `{summary['inventory_input']}`",
        f"- Inventory rows: **{summary['composable_inventory_rows']}**",
        "- Scope: evidence availability and queue preparation only; no composable Gold labels.",
        "",
        "## Current Review Crosswalk",
        "",
        f"- ToolBench composable rows in current human pack: **{summary['current_toolbench_composable_rows_in_human_pack']}**",
        f"- StableToolBench G3 rows in current human pack: **{summary['current_stable_g3_rows_in_human_pack']}**",
        f"- Total composable inventory rows in current packs: **{summary['composable_in_current_review_pack_count']}**",
        f"- Direct inventory crosswalk matches: **{summary['composable_inventory_rows_crosswalked_to_current_pack']}**",
        f"- Current pack rows absent from the prior inventory: **{summary['current_pack_rows_missing_from_inventory']}**",
        "",
        "## Recovery Queue",
        "",
        f"- Unreviewed uncertain composable candidates: **{summary['unreviewed_uncertain_composable_candidates']}**",
        f"- A new recovery QA pack is likely required after current adjudication: `{str(summary['new_recovery_qa_pack_likely_required']).lower()}`",
        "- No second duplicate QA pack was generated in this run.",
        "",
        "## Objective Evidence Availability",
        "",
        f"- Strong objective evidence available: **{summary['strong_objective_evidence_available_count']}**",
        f"- Partial objective evidence: **{summary['partial_evidence_count']}**",
        f"- Sequence-only: **{summary['sequence_only_count']}**",
        f"- No dependency evidence: **{summary['no_evidence_count']}**",
        f"- Source unavailable: **{summary['source_unavailable_count']}**",
        f"- Rows with a usable trace/answer path: **{summary['rows_with_usable_trace_or_answer_path']}**",
        "",
        "## Interpretation",
        "",
        "`evidence_completeness_status` describes what objective evidence can be inspected; it is not a "
        "strong-composable label. Neither ToolBench G3 nor StableToolBench G3 is accepted automatically. "
        "The combined source field for control/conditional evidence is surfaced in both columns because "
        "the inventory does not separate those two evidence types.",
        "",
        "The expected dependency-confirmed count cannot yet be claimed. Current v0.4.2 human adjudication "
        "must finish before deciding whether and how many uncertain rows need a new recovery QA pack.",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare composable evidence crosswalk and recovery queue v0.1.")
    parser.add_argument("--project-root", default=".")
    parser.add_argument(
        "--inventory", default="outputs/composable_recovery_inventory_v0_1/candidate_inventory.csv"
    )
    parser.add_argument(
        "--inventory-summary",
        default="outputs/composable_recovery_inventory_v0_1/evidence_availability_summary.json",
    )
    parser.add_argument("--output-dir", default="outputs/composable_recovery_preparation_v0_1")
    parser.add_argument(
        "--report", default="docs/phase1/composable_evidence_crosswalk_and_recovery_queue_v0_1.md"
    )
    args = parser.parse_args()

    root = Path(args.project_root).resolve()
    inventory_path = root / args.inventory
    inventory_summary_path = root / args.inventory_summary
    for required in [inventory_path, inventory_summary_path]:
        if not required.exists():
            parser.error(f"Required inventory input does not exist: {required}")
    pack_ids, pack_rows = build_pack_index(root)
    inventory_rows = read_csv(inventory_path)
    inventory_summary = json.loads(inventory_summary_path.read_text(encoding="utf-8-sig"))

    transformed = [
        transform(row, str(row.get("task_id") or "").strip() in pack_ids, pack_rows.get(str(row.get("task_id") or "").strip()))
        for row in inventory_rows
    ]
    crosswalk_matched = [row for row in transformed if row["in_current_v0_4_2_review_pack"] == "true"]
    tool_pack_composable = [
        row
        for row in pack_rows.values()
        if str(row.get("source_dataset") or "").strip() == "ToolBench"
        and "composable" in str(row.get("task_type") or "").lower()
    ]
    stable_pack_g3 = [
        row
        for row in pack_rows.values()
        if str(row.get("source_dataset") or "").strip() == "StableToolBench"
        and str(row.get("source_group") or row.get("stable_group") or "").strip() == "G3"
    ]
    inventory_ids = {row["source_task_id"] for row in transformed}
    unmatched_pack_rows = [
        row for row in tool_pack_composable + stable_pack_g3
        if str(row.get("task_id") or "").strip() not in inventory_ids
    ]
    unmatched_crosswalk: list[dict[str, str]] = []
    for row in unmatched_pack_rows:
        unmatched_crosswalk.append(
            {
                "source_task_id": str(row.get("task_id") or "").strip(),
                "source_dataset": str(row.get("source_dataset") or "").strip(),
                "source_group": str(row.get("source_group") or row.get("stable_group") or "").strip(),
                "current_policy_status": str(
                    row.get("v1_5f_dryrun_decision")
                    or row.get("stable_policy_decision")
                    or row.get("prior_policy_decision")
                    or ""
                ).strip(),
                "in_current_v0_4_2_review_pack": "true",
                "step_count": "0",
                "tool_sequence_available": "false",
                "api_sequence_available": "false",
                "argument_flow_available": "false",
                "repeated_entity_available": "false",
                "output_input_evidence_available": "false",
                "control_dependency_available": "false",
                "conditional_dependency_available": "false",
                "trace_or_answer_evidence_path": str(row.get("solvable_source_path") or "").strip(),
                "evidence_completeness_status": "source_unavailable",
                "recovery_priority": "P3_no_evidence_hold",
                "requires_human_dependency_review": "true",
            }
        )
    crosswalk = crosswalk_matched + unmatched_crosswalk
    recovery = [
        row
        for row in transformed
        if row["in_current_v0_4_2_review_pack"] == "false"
        and row["source_dataset"] == "ToolBench"
        and row["current_policy_status"] == "downgrade_to_uncertain"
    ]
    evidence_counts = Counter(row["evidence_completeness_status"] for row in transformed)
    output_dir = root / args.output_dir
    write_csv(output_dir / "composable_current_review_crosswalk.csv", crosswalk)
    write_csv(output_dir / "composable_unreviewed_recovery_queue.csv", recovery)

    distribution_path = output_dir / "composable_evidence_distribution.csv"
    distribution_path.parent.mkdir(parents=True, exist_ok=True)
    with distribution_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["evidence_completeness_status", "count"])
        writer.writeheader()
        for label in [
            "strong_objective_evidence_available",
            "partial_objective_evidence",
            "sequence_only",
            "no_dependency_evidence",
            "source_unavailable",
        ]:
            writer.writerow({"evidence_completeness_status": label, "count": evidence_counts.get(label, 0)})

    current_tool = len(tool_pack_composable)
    current_stable = len(stable_pack_g3)
    summary = {
        "generated_at": now_iso(),
        "scope": "evidence_crosswalk_and_queue_only_no_composable_gold_label",
        "inventory_input": str(inventory_path.resolve()),
        "inventory_summary_input": str(inventory_summary_path.resolve()),
        "composable_inventory_rows": len(transformed),
        "inventory_candidate_count_declared": inventory_summary.get("candidate_count"),
        "current_toolbench_composable_rows_in_human_pack": current_tool,
        "current_stable_g3_rows_in_human_pack": current_stable,
        "composable_in_current_review_pack_count": len(crosswalk),
        "composable_inventory_rows_crosswalked_to_current_pack": len(crosswalk_matched),
        "current_pack_rows_missing_from_inventory": len(unmatched_crosswalk),
        "unreviewed_uncertain_composable_candidates": len(recovery),
        "strong_objective_evidence_available_count": evidence_counts.get("strong_objective_evidence_available", 0),
        "partial_evidence_count": evidence_counts.get("partial_objective_evidence", 0),
        "sequence_only_count": evidence_counts.get("sequence_only", 0),
        "no_evidence_count": evidence_counts.get("no_dependency_evidence", 0),
        "source_unavailable_count": evidence_counts.get("source_unavailable", 0),
        "rows_with_usable_trace_or_answer_path": sum(bool(row["trace_or_answer_evidence_path"]) for row in transformed),
        "expected_dependency_confirmed_count_claimable": False,
        "new_recovery_qa_pack_likely_required": len(recovery) > 0,
        "duplicate_composable_qa_pack_generated": False,
        "strong_composable_labels_generated": 0,
        "dependency_graph_generated": False,
        "human_fields_autofilled": 0,
        "outputs": {
            "current_review_crosswalk": str((output_dir / "composable_current_review_crosswalk.csv").resolve()),
            "unreviewed_recovery_queue": str((output_dir / "composable_unreviewed_recovery_queue.csv").resolve()),
            "evidence_distribution": str(distribution_path.resolve()),
        },
    }
    summary_path = output_dir / "composable_recovery_preparation_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(root / args.report, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
