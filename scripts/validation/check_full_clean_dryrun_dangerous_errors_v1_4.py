from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path

from full_clean_v1_4_common import (
    DOC_DIR,
    OUTPUT_DIR,
    dangerous_flags,
    ensure_dir,
    now_text,
    open_csv_writer,
    table_lines,
    write_json,
    write_md,
)


ERROR_FIELDS = [
    "task_id",
    "source_group",
    "task_type",
    "query_text",
    "dryrun_decision",
    "dryrun_bucket",
    "dangerous_error_type",
    "blocking_reasons",
    "warning_reasons",
    "api_leak_detector_status",
    "api_leak_strength",
    "service_leak_detector_status",
    "candidate_space_status",
    "gold_in_candidate_services",
    "gold_in_candidate_apis",
    "v1_semantic_alignment_pred",
    "v1_capability_coverage_pred",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Check dangerous false-keep errors in v1.4 dry-run clean candidates.")
    parser.add_argument("--task-trace", type=Path, default=OUTPUT_DIR / "full_clean_task_trace_v1_4.csv")
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR / "dangerous_error_trace_v1_4.csv")
    parser.add_argument("--summary", type=Path, default=OUTPUT_DIR / "dangerous_error_summary_v1_4.json")
    args = parser.parse_args()

    if not args.task_trace.exists():
        raise FileNotFoundError(f"Missing task trace CSV: {args.task_trace}")
    row_count = 0
    clean_count = 0
    dangerous_error_count = 0
    dangerous_task_ids: set[str] = set()
    flag_counter = Counter()
    out_f, writer = open_csv_writer(args.output, ERROR_FIELDS)
    try:
        with args.task_trace.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                row_count += 1
                if row.get("dryrun_decision") == "dryrun_clean_candidate":
                    clean_count += 1
                flags = dangerous_flags(row)
                for flag in flags:
                    dangerous_error_count += 1
                    dangerous_task_ids.add(row.get("task_id", ""))
                    flag_counter[flag] += 1
                    writer.writerow({field: row.get(field, "") for field in ERROR_FIELDS} | {"dangerous_error_type": flag})
    finally:
        out_f.close()

    summary = {
        "generated_time": now_text(),
        "task_trace_file": str(args.task_trace),
        "output_file": str(args.output),
        "row_count": row_count,
        "dryrun_clean_candidate_count": clean_count,
        "dangerous_error_count": dangerous_error_count,
        "dangerous_task_count": len(dangerous_task_ids),
        "dangerous_error_type_distribution": dict(flag_counter),
        "dangerous_error_free": dangerous_error_count == 0,
    }
    write_json(args.summary, summary)
    lines = [
        "# Full Clean Dry-Run Dangerous Error Report v1.4",
        "",
        f"Generated time: {now_text()}",
        f"Input file: `{args.task_trace}`",
        f"Output file: `{args.output}`",
        f"Sample count: {row_count}",
        f"Dry-run clean candidates: {clean_count}",
        f"Dangerous error count: {dangerous_error_count}",
        f"Dangerous task count: {len(dangerous_task_ids)}",
        "",
        "A dangerous error means a sample entered `dryrun_clean_candidate` while still violating a blocking rule.",
        "",
        "## Dangerous Error Type Distribution",
        "",
        *table_lines(dict(flag_counter)),
    ]
    write_md(DOC_DIR / "full_clean_dryrun_dangerous_error_report_v1_4.md", lines)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
