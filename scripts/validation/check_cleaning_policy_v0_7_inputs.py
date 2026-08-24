"""Check required inputs for cleaning policy validation v0.7.

This script only inspects audited/manual-review artifacts. It does not run
full cleaning, create splits, run baselines, or train models.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List

from cleaning_policy_v0_7_utils import (
    DOCS_DIR,
    MANUAL40_PATH,
    MANUAL_COLUMNS,
    OUTPUT_DIR,
    ROUND2_COMPARISON_REPORT,
    ROUND2_PATH,
    ROUND3_PATH,
    ROUND3_REPORT,
    V41_PATH,
    empty_counts,
    ensure_dirs,
    missing_required_inputs,
    now_str,
    read_csv,
    write_json,
    write_missing_inputs,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check v0.7 cleaning-policy validation inputs without modifying them."
    )
    parser.add_argument("--manual40", type=Path, default=MANUAL40_PATH)
    parser.add_argument("--round2", type=Path, default=ROUND2_PATH)
    parser.add_argument("--round2-report", type=Path, default=ROUND2_COMPARISON_REPORT)
    parser.add_argument("--round3", type=Path, default=ROUND3_PATH)
    parser.add_argument("--round3-report", type=Path, default=ROUND3_REPORT)
    parser.add_argument("--v41", type=Path, default=V41_PATH)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    return parser


def summarize_csv(path: Path, label: str) -> Dict[str, object]:
    columns, rows = read_csv(path)
    manual_missing = [col for col in MANUAL_COLUMNS if col not in columns]
    key_columns = [
        "manual_final_decision",
        "query_text",
        "task_id",
        "task_type",
        "risk_category",
        "risk_subtype",
        *[col for col in MANUAL_COLUMNS if col in columns],
    ]
    key_columns = list(dict.fromkeys(key_columns))
    return {
        "label": label,
        "path": str(path),
        "exists": path.exists(),
        "row_count": len(rows),
        "columns": columns,
        "column_count": len(columns),
        "missing_standard_manual_columns": manual_missing,
        "empty_counts": empty_counts([col for col in key_columns if col in columns], rows),
    }


def round3_manual_completion(summary: Dict[str, object]) -> Dict[str, object]:
    columns = set(summary["columns"])
    _, rows = read_csv(Path(str(summary["path"])))
    missing_cols = [col for col in MANUAL_COLUMNS if col not in columns]
    nonempty_by_col = {}
    empty_by_col = {}
    for col in MANUAL_COLUMNS:
        if col not in columns:
            nonempty_by_col[col] = 0
            empty_by_col[col] = len(rows)
        else:
            empty_count = sum(1 for row in rows if not (row.get(col) or "").strip())
            empty_by_col[col] = empty_count
            nonempty_by_col[col] = len(rows) - empty_count
    return {
        "required_manual_columns": MANUAL_COLUMNS,
        "missing_columns": missing_cols,
        "nonempty_by_col": nonempty_by_col,
        "empty_by_col": empty_by_col,
        "all_present": not missing_cols,
        "all_nonempty": not missing_cols and all(value == 0 for value in empty_by_col.values()),
    }


def write_report(
    path: Path,
    summaries: List[Dict[str, object]],
    round3_completion: Dict[str, object],
    optional_v41_exists: bool,
) -> None:
    lines: List[str] = [
        "# Cleaning Policy v0.7 Input Check Report",
        "",
        f"Generated time: {now_str()}",
        f"Project root: `{Path.cwd()}`",
        "",
        "Scope: this report only checks manual/audited inputs. No full cleaning, split, baseline, or model training was run.",
        "",
        "## Input Files",
        "",
        "| label | path | rows | columns | missing standard manual columns |",
        "|---|---|---:|---:|---|",
    ]
    for item in summaries:
        lines.append(
            f"| {item['label']} | `{item['path']}` | {item['row_count']} | {item['column_count']} | "
            f"{', '.join(item['missing_standard_manual_columns']) or 'none'} |"
        )
    lines.extend(
        [
            "",
            "## Optional v4.1 Rule File",
            "",
            f"- `{V41_PATH}` exists: `{str(optional_v41_exists).lower()}`",
        ]
    )
    if not optional_v41_exists:
        lines.append(
            "- `manual_audit_rule_v4_1_candidate.md` not found; v4.2 is derived from v3.3 + Round2 + Round3 evidence."
        )
    lines.extend(
        [
            "",
            "## Round3 Manual Field Completion",
            "",
            f"- all seven manual fields present: `{str(round3_completion['all_present']).lower()}`",
            f"- all seven manual fields non-empty: `{str(round3_completion['all_nonempty']).lower()}`",
            "",
            "| field | empty rows | non-empty rows |",
            "|---|---:|---:|",
        ]
    )
    for col in MANUAL_COLUMNS:
        lines.append(
            f"| {col} | {round3_completion['empty_by_col'][col]} | {round3_completion['nonempty_by_col'][col]} |"
        )
    lines.extend(["", "## Column Empty Counts"])
    for item in summaries:
        lines.extend(
            [
                "",
                f"### {item['label']}",
                "",
                f"Input path: `{item['path']}`",
                f"Sample count: {item['row_count']}",
                "",
                "| column | empty rows |",
                "|---|---:|",
            ]
        )
        empty = item["empty_counts"]
        if not empty:
            lines.append("| - | - |")
        else:
            for col, count in empty.items():
                lines.append(f"| {col} | {count} |")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    ensure_dirs()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    missing = [
        path
        for path in [args.manual40, args.round2, args.round2_report, args.round3, args.round3_report]
        if not path.exists()
    ]
    if missing:
        missing_path = write_missing_inputs(missing)
        print(f"Missing required input(s). See {missing_path}")
        return 1

    summaries = [
        summarize_csv(args.manual40, "manual40 approved"),
        summarize_csv(args.round2, "round2 human final"),
        summarize_csv(args.round3, "round3 reviewed"),
    ]
    r3_completion = round3_manual_completion(summaries[-1])
    payload = {
        "generated_time": now_str(),
        "project_root": str(Path.cwd()),
        "inputs": summaries,
        "round3_manual_completion": r3_completion,
        "round2_comparison_report": str(args.round2_report),
        "round3_analysis_report": str(args.round3_report),
        "v41_rule_file": {
            "path": str(args.v41),
            "exists": args.v41.exists(),
            "fallback_note": (
                "" if args.v41.exists() else "manual_audit_rule_v4_1_candidate.md not found; v4.2 is derived from v3.3 + Round2 + Round3 evidence."
            ),
        },
        "scope_guardrails": {
            "full_cleaning": False,
            "split": False,
            "baseline": False,
            "training": False,
        },
    }
    summary_path = args.output_dir / "input_schema_summary.json"
    report_path = args.output_dir / "input_check_report.md"
    write_json(summary_path, payload)
    write_report(report_path, summaries, r3_completion, args.v41.exists())
    print(f"Wrote {summary_path}")
    print(f"Wrote {report_path}")
    if not r3_completion["all_nonempty"]:
        print("ERROR: Round3 manual fields are not fully completed.")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
