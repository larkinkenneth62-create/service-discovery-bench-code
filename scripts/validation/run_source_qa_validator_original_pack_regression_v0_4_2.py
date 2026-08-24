#!/usr/bin/env python
"""Validate the four blank v0.4.2 packs as pending, immutable review inputs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

try:
    from source_qa_review_validator_v0_4_2 import (
        HUMAN_FIELDS,
        ISSUE_FIELDS,
        read_csv,
        validate_rows,
        write_csv,
        write_json,
    )
except ImportError:
    from scripts.validation.source_qa_review_validator_v0_4_2 import (
        HUMAN_FIELDS,
        ISSUE_FIELDS,
        read_csv,
        validate_rows,
        write_csv,
        write_json,
    )


PACKS = [
    ("metatool", "outputs/source_qa_adjudication_v0_4_2/metatool/metatool_disagreement_adjudication_items_v0_4_2.csv", 50),
    ("stabletoolbench", "outputs/source_qa_adjudication_v0_4_2/stabletoolbench/stabletoolbench_supplemental_adjudication_items_v0_4_2.csv", 136),
    ("toolbench", "outputs/source_qa_adjudication_v0_4_2/toolbench/toolbench_v1_5f_final_targeted_qa_items_v0_4_2.csv", 110),
    ("shortcutsbench", "outputs/source_qa_adjudication_v0_4_2/shortcutsbench/shortcutsbench_strict_qa_items_v0_4_2.csv", 55),
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run v0.4.2 blank-pack regression.")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--output-dir", default="outputs/validator_patch_v0_4_2")
    args = parser.parse_args()

    root = Path(args.project_root).resolve()
    output_dir = (root / args.output_dir).resolve() if not Path(args.output_dir).is_absolute() else Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "outputs/source_qa_adjudication_v0_4_2/review_pack_manifest.json"
    if not manifest_path.exists():
        raise SystemExit(f"Review-pack manifest is missing: {manifest_path}")
    pack_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    results: dict[str, dict[str, Any]] = {}
    all_issues: list[dict[str, str]] = []
    total_nonempty_human = 0
    for source, relative, expected in PACKS:
        path = root / relative
        if not path.exists():
            raise SystemExit(f"v0.4.2 pack is missing: {path}")
        _, rows = read_csv(path)
        total_nonempty_human += sum(
            bool(str(row.get(field, "") or "").strip())
            for row in rows
            for field in HUMAN_FIELDS
        )
        summary, issues, _ = validate_rows(
            rows,
            filename=path.name,
            category=source,
            source_hint=source,
            expected_rows=expected,
            reviewed_mode=False,
        )
        for issue in issues:
            issue["category"] = source
        all_issues.extend(issues)
        results[source] = summary

    pending_counts = {source: result["pending_count"] for source, result in results.items()}
    expected_pending = {
        "metatool": 50,
        "stabletoolbench": 136,
        "toolbench": 110,
        "shortcutsbench": 55,
    }
    fatal_count = sum(issue["severity"] == "fatal" for issue in all_issues)
    regression_pass = bool(
        pending_counts == expected_pending
        and fatal_count == 0
        and total_nonempty_human == 0
        and pack_manifest.get("immutable_cell_change_total") == 0
        and pack_manifest.get("all_validation_pass") is True
    )
    summary = {
        "validator_version": "v0.4.2",
        "input_manifest": str(manifest_path),
        "source_results": results,
        "pending_counts": pending_counts,
        "total_pending_human_rows": sum(pending_counts.values()),
        "human_field_nonempty_count": total_nonempty_human,
        "immutable_cell_change_total": pack_manifest.get("immutable_cell_change_total"),
        "validation_fatal_count": fatal_count,
        "validation_warning_count": sum(issue["severity"] == "warning" for issue in all_issues),
        "invalid_reviewed_input": False,
        "bundle_status": "PENDING_HUMAN_ADJUDICATION",
        "regression_pass": regression_pass,
        "can_start_human_adjudication": regression_pass,
        "can_freeze_sources_now": False,
        "can_start_six_task_assembly_now": False,
    }
    write_json(output_dir / "original_pack_regression_summary.json", summary)
    write_csv(output_dir / "original_pack_validation_issues.csv", ISSUE_FIELDS, all_issues)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if regression_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
