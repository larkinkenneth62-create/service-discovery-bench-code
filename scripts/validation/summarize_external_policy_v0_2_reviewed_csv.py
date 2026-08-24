#!/usr/bin/env python
"""Summarize reviewed CSVs for external policy v0.2 QA packs."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def crosstab(rows: list[dict[str, str]], row_col: str, col_col: str) -> list[dict[str, Any]]:
    row_values = sorted({(row.get(row_col, "") or "").strip() for row in rows})
    col_values = sorted({(row.get(col_col, "") or "").strip() for row in rows})
    table: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        table[(row.get(row_col, "") or "").strip()][(row.get(col_col, "") or "").strip()] += 1
    out = []
    for row_value in row_values:
        item: dict[str, Any] = {row_col: row_value}
        for col_value in col_values:
            item[col_value or "blank"] = table[row_value][col_value]
        item["row_total"] = sum(table[row_value].values())
        out.append(item)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize an external policy v0.2 reviewed CSV.")
    parser.add_argument("--input", required=True, help="Reviewed CSV path.")
    parser.add_argument("--source", choices=["metatool", "stabletoolbench"], required=True)
    parser.add_argument(
        "--output-dir",
        default="outputs/external_policy_v0_2_reviewed_csv_analysis",
        help="Output directory for summary artifacts.",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if not input_path.exists():
        raise SystemExit(f"Input CSV does not exist: {input_path}")

    rows = read_csv(input_path)
    policy_col = "metatool_policy_decision" if args.source == "metatool" else "stable_policy_decision"
    source_group_col = "source_group" if args.source == "stabletoolbench" else "task_type"

    distributions = {
        "qa_final_decision": dict(Counter((row.get("qa_final_decision", "") or "").strip() for row in rows)),
        "qa_leakage_check": dict(Counter((row.get("qa_leakage_check", "") or "").strip() for row in rows)),
        "qa_task_type_check": dict(Counter((row.get("qa_task_type_check", "") or "").strip() for row in rows)),
        "qa_severity": dict(Counter((row.get("qa_severity", "") or "").strip() for row in rows)),
        "policy_decision": dict(Counter((row.get(policy_col, "") or "").strip() for row in rows)),
    }
    pending_review_count = distributions["qa_final_decision"].get("", 0)
    final_counts = distributions["qa_final_decision"]
    source_specific_pass = bool(
        pending_review_count == 0
        and final_counts.get("critical", 0) == 0
        and final_counts.get("remove", 0) == 0
    )

    policy_x_human = crosstab(rows, policy_col, "qa_final_decision")
    leakage_x_human = crosstab(rows, "qa_leakage_check", "qa_final_decision")
    group_x_human = crosstab(rows, source_group_col, "qa_final_decision") if source_group_col in (rows[0] if rows else {}) else []

    stem = input_path.stem
    write_csv(out_dir / f"{stem}_policy_x_human_final.csv", policy_x_human, list(policy_x_human[0].keys()) if policy_x_human else [policy_col])
    if leakage_x_human:
        write_csv(
            out_dir / f"{stem}_leakage_x_human_final.csv",
            leakage_x_human,
            list(leakage_x_human[0].keys()),
        )
    if group_x_human:
        write_csv(out_dir / f"{stem}_group_x_human_final.csv", group_x_human, list(group_x_human[0].keys()))

    summary = {
        "generated_at": now_iso(),
        "input": str(input_path),
        "source": args.source,
        "row_count": len(rows),
        "pending_review_count": pending_review_count,
        "distributions": distributions,
        "policy_decision_x_human_final": policy_x_human,
        "leakage_check_x_human_final": leakage_x_human,
        "task_type_or_group_x_human_final": group_x_human,
        "source_specific_pass": source_specific_pass,
        "pass_fail_note": (
            "Pass means no pending reviews, no critical rows, and no remove rows in this reviewed CSV. "
            "It does not authorize final dataset generation by itself."
        ),
    }
    write_json(out_dir / f"{stem}_summary.json", summary)
    report = f"""# External Policy v0.2 Reviewed CSV Summary

Generated at: {summary['generated_at']}

Input: `{input_path}`

Source: `{args.source}`

- row_count: `{len(rows)}`
- pending_review_count: `{pending_review_count}`
- qa_final_decision_distribution: `{distributions['qa_final_decision']}`
- qa_leakage_check_distribution: `{distributions['qa_leakage_check']}`
- qa_task_type_check_distribution: `{distributions['qa_task_type_check']}`
- source_specific_pass: `{str(source_specific_pass).lower()}`

This summary is for reviewed CSV analysis only. It does not merge external sources, generate a final clean dataset, split data, run baselines, or train models.
"""
    (out_dir / f"{stem}_summary_report.md").write_text(report, encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
