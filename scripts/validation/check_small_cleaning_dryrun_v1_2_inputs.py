from __future__ import annotations

import argparse
import sys
from pathlib import Path

from small_cleaning_v1_2_common import (
    OUTPUT_DIR,
    REQUIRED_CANONICAL_FIELDS,
    V1_1_EVAL_SUMMARY,
    V1_1_PREDICTIONS,
    V1_1_REPORTS,
    V4_2_POLICY,
    build_column_mapping,
    ensure_dir,
    now_text,
    read_csv_with_fields,
    resolve_policy_trace,
    table_lines,
    value_counter,
    write_json,
    write_md,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check inputs for small cleaning dry-run v1.2.")
    parser.add_argument("--project-root", type=Path, default=Path.cwd(), help="Project root. Default: current directory.")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR, help="Output directory.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.project_root.resolve()
    output_dir = root / args.output_dir
    ensure_dir(output_dir)

    policy_trace = resolve_policy_trace(root)
    if policy_trace is None:
        missing_path = output_dir / "MISSING_V1_1_POLICY_TRACE.md"
        write_md(
            missing_path,
            [
                "# Missing v1.1 Policy Trace",
                "",
                f"Generated time: {now_text()}",
                "",
                "Could not find v0.8 sample policy trace with SemCap v1.1.",
                "Stopped without guessing.",
            ],
        )
        print(f"Missing v1.1 policy trace. Wrote {missing_path}", file=sys.stderr)
        return 1

    rows, fields = read_csv_with_fields(policy_trace)
    mapping = build_column_mapping(fields)
    missing_canonical = [field for field in REQUIRED_CANONICAL_FIELDS if field not in mapping and field not in fields]
    policy_distribution = value_counter(rows, "policy_decision_v1")

    required_other_inputs = [V1_1_PREDICTIONS, V1_1_EVAL_SUMMARY, V4_2_POLICY, *V1_1_REPORTS]
    missing_other = [str(path) for path in required_other_inputs if not (root / path).exists()]

    summary = {
        "generated_time": now_text(),
        "policy_trace_path": str(policy_trace.relative_to(root) if policy_trace.is_relative_to(root) else policy_trace),
        "row_count": len(rows),
        "column_count": len(fields),
        "columns": fields,
        "column_mapping": mapping,
        "missing_canonical_fields": missing_canonical,
        "missing_other_inputs": missing_other,
        "policy_decision_v1_distribution": policy_distribution,
        "policy_keep_candidate_count": policy_distribution.get("policy_keep_candidate", 0),
        "expected_policy_keep_candidate_count": 46,
        "policy_keep_candidate_count_matches_expected": policy_distribution.get("policy_keep_candidate", 0) == 46,
    }
    write_json(output_dir / "input_schema_summary.json", summary)

    lines = [
        "# Small Cleaning Dry-Run v1.2 Input Check Report",
        "",
        f"Generated time: {summary['generated_time']}",
        f"Project root: `{root}`",
        "",
        "Scope: input validation only. No full cleaning, split, baseline, model training, final clean dataset, or new human review was run.",
        "",
        "## Input paths",
        "",
        f"- policy trace: `{summary['policy_trace_path']}`",
        f"- SemCap predictions: `{V1_1_PREDICTIONS}`",
        f"- eval summary: `{V1_1_EVAL_SUMMARY}`",
        f"- v4.2 policy: `{V4_2_POLICY}`",
        *[f"- report: `{path}`" for path in V1_1_REPORTS],
        "",
        "## Basic checks",
        "",
        f"- row_count: {len(rows)}",
        f"- expected row_count: 300",
        f"- policy_keep_candidate_count: {policy_distribution.get('policy_keep_candidate', 0)}",
        f"- expected policy_keep_candidate_count: 46",
        f"- missing_canonical_fields: {missing_canonical if missing_canonical else 'none'}",
        f"- missing_other_inputs: {missing_other if missing_other else 'none'}",
        "",
        "## Column mapping",
        "",
        "| canonical field | actual field |",
        "|---|---|",
    ]
    for field in REQUIRED_CANONICAL_FIELDS:
        actual = mapping.get(field, field if field in fields else "")
        lines.append(f"| {field} | {actual or '<missing>'} |")
    lines.extend(["", "## policy_decision_v1 distribution", "", *table_lines(policy_distribution)])
    if policy_distribution.get("policy_keep_candidate", 0) != 46:
        lines.extend(["", "## Note", "", "The keep candidate count is not 46. The run continues, but this must be interpreted carefully."])
    write_md(output_dir / "input_check_report.md", lines)

    if missing_canonical or missing_other or len(rows) != 300:
        print("Input check completed with warnings. See input_check_report.md.")
    else:
        print("Input check passed.")
    print(f"policy_keep_candidate_count: {policy_distribution.get('policy_keep_candidate', 0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
