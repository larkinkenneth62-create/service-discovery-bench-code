"""Apply v0.9 semcap pilot predictions to v0.8 sample policy trace."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List

from semcap_detector_v0_9_utils import (
    DOCS_DIR,
    OUTPUT_DIR,
    apply_pilot_policy,
    count_by,
    distribution_rows,
    ensure_dirs,
    markdown_table,
    now_str,
    read_csv,
    write_csv,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Apply semcap pilot labels to v0.8 sample trace.")
    parser.add_argument("--detector-trace", type=Path, default=Path("outputs/small_full_pipeline_trace_v0_8/small_full_pipeline_detector_trace.csv"))
    parser.add_argument("--predictions", type=Path, default=OUTPUT_DIR / "semcap_predictions_v0_8_sample_heuristic.csv")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--docs-dir", type=Path, default=DOCS_DIR)
    return parser


def write_report(path: Path, rows: List[Dict[str, object]]) -> None:
    lines = [
        "# v0.8 Sample SemCap Pilot Policy Trace Report v0.9",
        "",
        f"Generated time: {now_str()}",
        f"Sample count: {len(rows)}",
        "",
        "Scope: pilot policy trace only. This is not a clean dataset and no split/baseline/training was run.",
        "",
        "Low-confidence ok cannot enter `pilot_keep_candidate`; `coverage_uncertain` or missing capability must enter `pilot_uncertain`; `coverage_mismatch` must enter `pilot_remove`.",
        "",
        "## policy_decision_pilot",
        "",
    ]
    lines.extend(markdown_table(distribution_rows(rows, "policy_decision_pilot"), ["value", "count"], max_rows=20))
    for key in ["pilot_capability_coverage_pred", "pilot_capability_coverage_confidence", "pilot_semantic_alignment_pred", "pilot_semantic_alignment_confidence", "requires_human_review_pilot"]:
        lines.extend(["", f"## {key}", ""])
        lines.extend(markdown_table(distribution_rows(rows, key), ["value", "count"], max_rows=20))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    ensure_dirs()
    if not args.detector_trace.exists() or not args.predictions.exists():
        print("ERROR: missing v0.8 detector trace or semcap predictions.")
        return 1
    _, trace_rows = read_csv(args.detector_trace)
    _, pred_rows = read_csv(args.predictions)
    pred_by_task = {row.get("task_id"): row for row in pred_rows}
    merged: List[Dict[str, object]] = []
    for row in trace_rows:
        pred = pred_by_task.get(row.get("task_id"))
        if pred is None:
            continue
        merged.append(apply_pilot_policy(row, pred))
    with_semcap = args.output_dir / "v0_8_sample_with_semcap_pilot.csv"
    policy_trace = args.output_dir / "v0_8_sample_policy_trace_with_semcap_pilot.csv"
    write_csv(with_semcap, merged)
    write_csv(policy_trace, merged)
    report = args.docs_dir / "v0_8_sample_semcap_pilot_policy_trace_report_v0_9.md"
    write_report(report, merged)
    print(f"Wrote {with_semcap} ({len(merged)} rows)")
    print(f"Wrote {policy_trace}")
    print(f"policy_decision_pilot distribution: {count_by(merged, 'policy_decision_pilot')}")
    print(f"Wrote {report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
