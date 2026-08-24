#!/usr/bin/env python
"""Summarize external QA CSV files.

Pending CSVs remain pending; this script does not infer Go/No-Go from empty
human fields.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def split_error_types(rows: list[dict[str, str]]) -> Counter[str]:
    c: Counter[str] = Counter()
    for row in rows:
        raw = row.get("qa_error_type", "")
        if not raw:
            c[""] += 1
            continue
        for part in raw.split(";"):
            c[part.strip()] += 1
    return c


def summarize(path: Path) -> dict[str, Any]:
    rows = read_rows(path)
    source = next((row.get("source_dataset", "") for row in rows if row.get("source_dataset", "")), "unknown")
    pending = sum(1 for row in rows if not row.get("qa_final_decision", "").strip())
    reviewed = len(rows) - pending
    final_counter = Counter(row.get("qa_final_decision", "") for row in rows)
    severity_counter = Counter(row.get("qa_severity", "") for row in rows)
    leakage_counter = Counter(row.get("qa_leakage_check", "") for row in rows)
    candidate_counter = Counter(row.get("qa_candidate_validity_check", "") for row in rows)
    critical = severity_counter.get("critical", 0)
    remove = final_counter.get("remove", 0)
    uncertain = final_counter.get("uncertain", 0)
    keep = final_counter.get("keep_for_cleaning_candidate", 0)

    if pending:
        status = "pending"
        pass_value: bool | str = "pending"
    elif critical > 0:
        status = "failed_critical"
        pass_value = False
    elif remove + uncertain > len(rows) * 0.5:
        status = "needs_policy_revision"
        pass_value = False
    elif leakage_counter.get("service_leak_blocking", 0) + leakage_counter.get("api_leak_blocking", 0) > 0:
        status = "needs_rewrite_or_leakage_policy"
        pass_value = False
    else:
        status = "pass_for_source_specific_policy_design"
        pass_value = True

    summary: dict[str, Any] = {
        "generated_time": now(),
        "input_csv": str(path),
        "source_dataset": source,
        "rows": len(rows),
        "pending_review_count": pending,
        "reviewed_count": reviewed,
        "qa_final_decision_distribution": dict(final_counter),
        "qa_severity_distribution": dict(severity_counter),
        "qa_error_type_distribution": dict(split_error_types(rows)),
        "leakage_check_distribution": dict(leakage_counter),
        "candidate_validity_distribution": dict(candidate_counter),
        "critical_count": critical,
        "remove_count": remove,
        "uncertain_count": uncertain,
        "keep_count": keep,
        "can_accept_external_source_qa_as_pass": pass_value,
        "status": status,
        "fixed_no_go": {
            "can_generate_final_dataset_now": False,
            "can_merge_external_sources_now": False,
            "can_create_split_now": False,
            "can_run_baseline_now": False,
            "can_train_model_now": False,
        },
    }
    if source == "MetaTool":
        summary["service_catalog_check_distribution"] = dict(Counter(row.get("qa_service_catalog_check", "") for row in rows))
    if source == "StableToolBench":
        summary["task_type_check_distribution"] = dict(Counter(row.get("qa_task_type_check", "") for row in rows))
        summary["g3_not_strong_composable_count"] = sum(
            1
            for row in rows
            if row.get("stable_group") == "G3" and row.get("qa_task_type_check") == "composable_not_strong_dependency"
        )
    return summary


def output_for(path: Path, source: str) -> Path:
    name = "metatool_external_qa_summary.json" if source == "MetaTool" else "stabletoolbench_external_qa_summary.json"
    return path.parent / name


def write_md(path: Path, summaries: list[dict[str, Any]]) -> None:
    lines = ["# External QA CSV Summary Report v0.1", "", f"Generated time: {now()}", ""]
    for s in summaries:
        lines.extend(
            [
                f"## {s['source_dataset']}",
                "",
                f"- input_csv: `{s['input_csv']}`",
                f"- rows: {s['rows']}",
                f"- pending_review_count: {s['pending_review_count']}",
                f"- reviewed_count: {s['reviewed_count']}",
                f"- qa_final_decision_distribution: `{s['qa_final_decision_distribution']}`",
                f"- qa_severity_distribution: `{s['qa_severity_distribution']}`",
                f"- leakage_check_distribution: `{s['leakage_check_distribution']}`",
                f"- critical_count: {s['critical_count']}",
                f"- remove_count: {s['remove_count']}",
                f"- uncertain_count: {s['uncertain_count']}",
                f"- keep_count: {s['keep_count']}",
                f"- can_accept_external_source_qa_as_pass: `{s['can_accept_external_source_qa_as_pass']}`",
                f"- status: `{s['status']}`",
                "",
                "No matter the status, this summary does not authorize final dataset generation, source merge, split, baseline, or training.",
                "",
            ]
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize external QA CSV files.")
    parser.add_argument("csv_files", nargs="+", help="One or more external QA CSV files.")
    parser.add_argument("--markdown-report", default="docs/phase1/external_qa_csv_summary_report_v0_1.md")
    args = parser.parse_args()

    summaries = []
    for csv_file in args.csv_files:
        path = Path(csv_file)
        if not path.exists():
            raise SystemExit(f"Input CSV does not exist: {path}")
        summary = summarize(path)
        out = output_for(path, summary["source_dataset"])
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        summaries.append(summary)
        print(json.dumps({"input_csv": str(path), "output_json": str(out), "status": summary["status"]}, ensure_ascii=False))
    write_md(Path(args.markdown_report), summaries)


if __name__ == "__main__":
    main()
