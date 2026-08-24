#!/usr/bin/env python
"""Preflight v0.4.2 reviewed packs for future source-specific freeze analysis.

This command is deliberately preparation-only. It validates readiness per source
and documents the future bucket mapping, but never writes frozen pool CSV files.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from check_v0_4_2_adjudication_progress import PACKS
    from source_qa_review_validator_v0_4_2 import (
        CORE_REQUIRED_FIELDS,
        validate_reviewed_pair,
    )
except ImportError:
    from scripts.validation.check_v0_4_2_adjudication_progress import PACKS
    from scripts.validation.source_qa_review_validator_v0_4_2 import (
        CORE_REQUIRED_FIELDS,
        validate_reviewed_pair,
    )


BUCKETS = [
    "human_confirmed_gold",
    "policy_validated_silver",
    "rewrite_pool",
    "reconstruction_pool",
    "dependency_review_pool",
    "hold",
    "excluded",
]


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def nonempty(value: Any) -> bool:
    return bool(str(value or "").strip())


def discover_reviewed(original: Path) -> Path | None:
    expected = original.with_name(f"{original.stem}_reviewed.csv")
    return expected if expected.exists() else None


def analyze_source(project_root: Path, spec: dict[str, Any]) -> dict[str, Any]:
    original = project_root / "outputs/source_qa_adjudication_v0_4_2" / spec["original"]
    reviewed = discover_reviewed(original) if original.exists() else None
    result: dict[str, Any] = {
        "source": spec["source"],
        "source_tier": spec["tier"],
        "expected_rows": spec["expected"],
        "original_path": str(original.resolve()),
        "reviewed_path": str(reviewed.resolve()) if reviewed else "",
        "reviewed_found": reviewed is not None,
        "status": "WAITING_FOR_HUMAN_ADJUDICATION",
        "pending_rows": spec["expected"],
        "fatal_count": 0,
        "future_bucket_counts": {bucket: 0 for bucket in BUCKETS},
        "frozen_pool_written": False,
    }
    if not original.exists():
        result.update(status="ORIGINAL_PACK_MISSING", fatal_count=1)
        return result
    if reviewed is None:
        return result

    summary, issues, _changed, _rows = validate_reviewed_pair(
        original,
        reviewed,
        expected_rows=spec["expected"],
        category=spec["source"],
        source_hint=spec["source"],
    )
    reviewed_rows = read_rows(reviewed)
    completed = sum(
        all(nonempty(row.get(field)) for field in CORE_REQUIRED_FIELDS)
        for row in reviewed_rows
    )
    result.update(
        actual_rows=len(reviewed_rows),
        completed_rows=completed,
        pending_rows=max(spec["expected"] - completed, 0),
        fatal_count=sum(item.get("severity") == "fatal" for item in issues),
        validator_status=summary.get("validation_status", "unknown"),
    )
    if result["fatal_count"]:
        result["status"] = "INVALID_REVIEWED_INPUT"
    elif result["pending_rows"]:
        result["status"] = "WAITING_FOR_HUMAN_ADJUDICATION"
    else:
        result["status"] = "READY_FOR_SOURCE_SPECIFIC_FREEZE_ANALYSIS"
    return result


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_report(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# Source Freeze Analyzer Preparation v0.5",
        "",
        f"- Generated at: `{summary['generated_at']}`",
        f"- Project root: `{summary['project_root']}`",
        f"- Overall status: `{summary['status']}`",
        "- Execution mode: `preflight_only`",
        "- Frozen pool files written: `false`",
        "",
        "## Source Readiness",
        "",
        "| Source | Tier | Reviewed | Pending | Fatal | Status |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for row in summary["sources"]:
        lines.append(
            f"| {row['source']} | {row['source_tier']} | "
            f"{str(row['reviewed_found']).lower()} | {row['pending_rows']} | "
            f"{row['fatal_count']} | {row['status']} |"
        )
    lines.extend(
        [
            "",
            "## Future Independent Buckets",
            "",
            ", ".join(f"`{name}`" for name in BUCKETS),
            "",
            "Each source will be analyzed and frozen independently. A failure in one source "
            "must not roll back another source that has passed its own validation.",
            "",
            "## Guardrails",
            "",
            "- Unreviewed MetaTool full-policy rows may only be Silver.",
            "- The 892 ToolBench rows without row-level human review may only be Silver.",
            "- StableToolBench uses mutually exclusive primary-decision accounting.",
            "- ShortcutsBench may become a small Gold supplement only after all 55 rows are human-confirmed.",
            "- `human_with_model_assistance` and `model_pilot_only` never default to human-confirmed Gold.",
            "- Rewrite, reconstruction, and dependency-review actions are not clean-as-is.",
            "- Blocking leak versus keep conflicts and candidate-space versus keep conflicts must be zero.",
            "- A composable keep requires dependency evidence; group membership alone is insufficient.",
            "",
            "## Current Decision",
            "",
            "The reviewed packs are not complete. The analyzer therefore stops at "
            "`WAITING_FOR_HUMAN_ADJUDICATION` and does not generate frozen pools.",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare and preflight source-specific v0.4.2 freeze analysis without freezing pools."
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument(
        "--output-json",
        default="outputs/source_freeze_analyzer_preparation_v0_5/preflight_summary.json",
    )
    parser.add_argument(
        "--report",
        default="docs/phase1/source_freeze_analyzer_preparation_v0_5.md",
    )
    args = parser.parse_args()

    root = Path(args.project_root).resolve()
    sources = [analyze_source(root, spec) for spec in PACKS]
    ready_count = sum(
        row["status"] == "READY_FOR_SOURCE_SPECIFIC_FREEZE_ANALYSIS" for row in sources
    )
    overall = (
        "READY_FOR_SOURCE_SPECIFIC_FREEZE_ANALYSIS"
        if ready_count == len(sources)
        else "WAITING_FOR_HUMAN_ADJUDICATION"
    )
    summary = {
        "generated_at": now_iso(),
        "project_root": str(root),
        "status": overall,
        "sources_ready": ready_count,
        "sources_total": len(sources),
        "sources": sources,
        "future_buckets": BUCKETS,
        "source_failures_are_independent": True,
        "source_freeze_executed": False,
        "frozen_pool_files_written": 0,
    }
    write_json(root / args.output_json, summary)
    write_report(root / args.report, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
