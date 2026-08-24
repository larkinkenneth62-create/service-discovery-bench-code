#!/usr/bin/env python
"""Read-only progress checker for the four v0.4.2 adjudication packs."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from source_qa_review_validator_v0_4_2 import (
        CORE_REQUIRED_FIELDS,
        detect_primary_id,
        read_csv,
        text,
        validate_reviewed_pair,
    )
except ImportError:
    from scripts.validation.source_qa_review_validator_v0_4_2 import (
        CORE_REQUIRED_FIELDS,
        detect_primary_id,
        read_csv,
        text,
        validate_reviewed_pair,
    )


PACKS = [
    {
        "source": "metatool",
        "tier": "core",
        "expected": 50,
        "original": "metatool/metatool_disagreement_adjudication_items_v0_4_2.csv",
    },
    {
        "source": "toolbench",
        "tier": "core",
        "expected": 110,
        "original": "toolbench/toolbench_v1_5f_final_targeted_qa_items_v0_4_2.csv",
    },
    {
        "source": "stabletoolbench",
        "tier": "core",
        "expected": 136,
        "original": "stabletoolbench/stabletoolbench_supplemental_adjudication_items_v0_4_2.csv",
    },
    {
        "source": "shortcutsbench",
        "tier": "supplementary",
        "expected": 55,
        "original": "shortcutsbench/shortcutsbench_strict_qa_items_v0_4_2.csv",
    },
]

CSV_FIELDS = [
    "source",
    "source_tier",
    "status",
    "original_path",
    "reviewed_path",
    "reviewed_found",
    "expected_rows",
    "original_actual_rows",
    "actual_rows",
    "completed_rows",
    "pending_rows",
    "invalid_enum_rows",
    "immutable_source_field_changes",
    "duplicate_ids",
    "validation_fatal_count",
    "adjudicator_type_distribution_json",
    "release_action_distribution_json",
    "human_confirmed_completed_rows",
    "human_with_model_assistance_completed_rows",
    "model_pilot_only_completed_rows",
]


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def discover_reviewed(original: Path) -> Path | None:
    exact = original.with_name(f"{original.stem}_reviewed.csv")
    if exact.exists():
        return exact
    candidates = sorted(
        (
            path
            for path in original.parent.glob("*_reviewed.csv")
            if path.is_file() and path.stem.startswith(original.stem)
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def duplicate_id_count(fields: list[str], rows: list[dict[str, str]]) -> int:
    id_field = detect_primary_id(fields)
    if not id_field:
        return len(rows)
    values = [text(row.get(id_field)) for row in rows]
    return len(values) - len(set(value for value in values if value)) + sum(not value for value in values)


def completed(row: dict[str, str]) -> bool:
    return all(text(row.get(field)) for field in CORE_REQUIRED_FIELDS)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check v0.4.2 human-adjudication progress read-only.")
    parser.add_argument("--project-root", default=".")
    parser.add_argument(
        "--packs-dir", default="outputs/source_qa_adjudication_v0_4_2"
    )
    parser.add_argument(
        "--output-dir", default="outputs/adjudication_progress_v0_5"
    )
    parser.add_argument(
        "--report", default="docs/phase1/human_adjudication_progress_report_v0_5.md"
    )
    args = parser.parse_args()

    root = Path(args.project_root).resolve()
    packs_dir = root / args.packs_dir
    output_dir = root / args.output_dir
    report_path = root / args.report
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    pack_rows: list[dict[str, Any]] = []
    for spec in PACKS:
        original = packs_dir / spec["original"]
        if not original.exists():
            raise SystemExit(f"Required v0.4.2 original pack is missing: {original}")
        original_fields, original_records = read_csv(original)
        reviewed = discover_reviewed(original)
        if reviewed is None:
            pack_rows.append(
                {
                    "source": spec["source"],
                    "source_tier": spec["tier"],
                    "status": "NOT_STARTED",
                    "original_path": str(original),
                    "reviewed_path": "",
                    "reviewed_found": False,
                    "expected_rows": spec["expected"],
                    "original_actual_rows": len(original_records),
                    "actual_rows": 0,
                    "completed_rows": 0,
                    "pending_rows": spec["expected"],
                    "invalid_enum_rows": 0,
                    "immutable_source_field_changes": 0,
                    "duplicate_ids": 0,
                    "validation_fatal_count": 0,
                    "adjudicator_type_distribution": {},
                    "release_action_distribution": {},
                    "human_confirmed_completed_rows": 0,
                    "human_with_model_assistance_completed_rows": 0,
                    "model_pilot_only_completed_rows": 0,
                    "notes": "Reviewed CSV not found; absence is not a fatal validation error.",
                }
            )
            continue

        reviewed_fields, reviewed_records = read_csv(reviewed)
        validation, issues, _, _ = validate_reviewed_pair(
            original,
            reviewed,
            expected_rows=spec["expected"],
            category=spec["source"],
            source_hint=spec["source"],
        )
        completed_rows = sum(completed(row) for row in reviewed_records)
        pending_rows = max(spec["expected"] - completed_rows, 0)
        invalid_enum_ids = {
            issue["row_id"]
            for issue in issues
            if issue["issue_type"] == "invalid_enum"
        }
        adjudicator_distribution = Counter(
            text(row.get("adjudicator_type")) or "unfilled" for row in reviewed_records
        )
        release_distribution = Counter(
            text(row.get("qa_release_action")) or "unfilled" for row in reviewed_records
        )
        if pending_rows > 0:
            status = "IN_PROGRESS"
        elif validation.get("validation_status") == "valid":
            status = "READY_FOR_SOURCE_SPECIFIC_GO_NO_GO"
        else:
            status = "VALIDATION_FAILED"
        pack_rows.append(
            {
                "source": spec["source"],
                "source_tier": spec["tier"],
                "status": status,
                "original_path": str(original),
                "reviewed_path": str(reviewed),
                "reviewed_found": True,
                "expected_rows": spec["expected"],
                "original_actual_rows": len(original_records),
                "actual_rows": len(reviewed_records),
                "completed_rows": completed_rows,
                "pending_rows": pending_rows,
                "invalid_enum_rows": len(invalid_enum_ids),
                "immutable_source_field_changes": validation.get(
                    "immutable_field_changed_count", 0
                ),
                "duplicate_ids": duplicate_id_count(reviewed_fields, reviewed_records),
                "validation_fatal_count": validation.get("fatal_count", 0),
                "adjudicator_type_distribution": dict(adjudicator_distribution),
                "release_action_distribution": dict(release_distribution),
                "human_confirmed_completed_rows": sum(
                    completed(row) and text(row.get("adjudicator_type")) == "human_confirmed"
                    for row in reviewed_records
                ),
                "human_with_model_assistance_completed_rows": sum(
                    completed(row)
                    and text(row.get("adjudicator_type")) == "human_with_model_assistance"
                    for row in reviewed_records
                ),
                "model_pilot_only_completed_rows": sum(
                    completed(row) and text(row.get("adjudicator_type")) == "model_pilot_only"
                    for row in reviewed_records
                ),
                "notes": (
                    "model_pilot_only rows are completion records but never count as human-confirmed Gold."
                ),
            }
        )

    core = [row for row in pack_rows if row["source_tier"] == "core"]
    supplementary = [row for row in pack_rows if row["source_tier"] == "supplementary"]
    summary = {
        "generated_at": now_iso(),
        "packs_dir": str(packs_dir),
        "packs": pack_rows,
        "core_hard_path": {
            "expected_rows": sum(row["expected_rows"] for row in core),
            "completed_rows": sum(row["completed_rows"] for row in core),
            "pending_rows": sum(row["pending_rows"] for row in core),
            "all_ready": all(
                row["status"] == "READY_FOR_SOURCE_SPECIFIC_GO_NO_GO" for row in core
            ),
        },
        "supplementary": {
            "expected_rows": sum(row["expected_rows"] for row in supplementary),
            "completed_rows": sum(row["completed_rows"] for row in supplementary),
            "pending_rows": sum(row["pending_rows"] for row in supplementary),
            "all_ready": all(
                row["status"] == "READY_FOR_SOURCE_SPECIFIC_GO_NO_GO"
                for row in supplementary
            ),
        },
        "total_expected_rows": sum(row["expected_rows"] for row in pack_rows),
        "total_completed_rows": sum(row["completed_rows"] for row in pack_rows),
        "total_pending_rows": sum(row["pending_rows"] for row in pack_rows),
        "shortcuts_blocks_core_source_go_no_go": False,
        "source_freeze_executed": False,
        "human_fields_autofilled": 0,
    }
    (output_dir / "review_progress_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with (output_dir / "review_progress_by_pack.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in pack_rows:
            output = dict(row)
            output["adjudicator_type_distribution_json"] = json.dumps(
                row["adjudicator_type_distribution"], ensure_ascii=False
            )
            output["release_action_distribution_json"] = json.dumps(
                row["release_action_distribution"], ensure_ascii=False
            )
            writer.writerow(output)

    lines = [
        "# Human Adjudication Progress Report v0.5",
        "",
        f"Generated at: {summary['generated_at']}",
        "",
        f"Packs directory: `{packs_dir}`",
        "",
        "## Per-Pack Progress",
        "",
        "| Source | Tier | Status | Expected | Completed | Pending | Invalid enum rows | Immutable changes | Duplicate IDs |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in pack_rows:
        lines.append(
            f"| {row['source']} | {row['source_tier']} | {row['status']} | "
            f"{row['expected_rows']} | {row['completed_rows']} | {row['pending_rows']} | "
            f"{row['invalid_enum_rows']} | {row['immutable_source_field_changes']} | {row['duplicate_ids']} |"
        )
    lines.extend(
        [
            "",
            "## Core Hard Path",
            "",
            f"- expected rows: {summary['core_hard_path']['expected_rows']}",
            f"- completed rows: {summary['core_hard_path']['completed_rows']}",
            f"- pending rows: {summary['core_hard_path']['pending_rows']}",
            "- MetaTool, ToolBench, and StableToolBench are evaluated independently at the future source-specific gate.",
            "",
            "## Supplementary",
            "",
            f"- expected ShortcutsBench rows: {summary['supplementary']['expected_rows']}",
            f"- pending ShortcutsBench rows: {summary['supplementary']['pending_rows']}",
            "- ShortcutsBench incompleteness does not block independent Go/No-Go for the three core sources.",
            "",
            "## Provenance Rule",
            "",
            "`model_pilot_only` may be counted as a filled form row but is never counted as human-confirmed Gold. `human_with_model_assistance` also requires later independent confirmation for Gold test use.",
            "",
            "No CSV was modified and no human field was filled by this checker.",
        ]
    )
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
