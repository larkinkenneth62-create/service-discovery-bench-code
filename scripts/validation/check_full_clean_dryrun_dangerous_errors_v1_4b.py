from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path

from full_clean_v1_4b_common import DOC_DIR, OUTPUT_DIR, dangerous_flags_v14b, now_text, open_csv_writer, table_lines, write_json, write_md


FIELDS = ["task_id", "source_group", "task_type", "query_text", "dryrun_decision_v1_4b", "dryrun_bucket_v1_4b", "dangerous_error_type", "blocking_reasons_v1_4b", "warning_reasons_v1_4b", "v12_capability_coverage_pred", "v12_gold_set_integrity_status", "v12_generic_search_overtrust_flag"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Check v1.4b dangerous errors.")
    parser.add_argument("--input", type=Path, default=OUTPUT_DIR / "full_clean_task_trace_v1_4b.csv")
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR / "dangerous_error_trace_v1_4b.csv")
    args = parser.parse_args()
    if not args.input.exists():
        raise FileNotFoundError(f"Missing task trace: {args.input}")
    row_count = clean_count = err_count = 0
    task_ids = set()
    counter = Counter()
    out_f, writer = open_csv_writer(args.output, FIELDS)
    try:
        with args.input.open("r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                row_count += 1
                if row.get("dryrun_decision_v1_4b") == "dryrun_clean_candidate":
                    clean_count += 1
                for flag in dangerous_flags_v14b(row):
                    out = {field: row.get(field, "") for field in FIELDS}
                    out["dangerous_error_type"] = flag
                    writer.writerow(out)
                    err_count += 1
                    task_ids.add(row.get("task_id", ""))
                    counter[flag] += 1
    finally:
        out_f.close()
    summary = {"generated_time": now_text(), "row_count": row_count, "dryrun_clean_candidate_count": clean_count, "dangerous_error_count": err_count, "dangerous_task_count": len(task_ids), "dangerous_error_type_distribution": dict(counter), "dangerous_error_free": err_count == 0}
    write_json(OUTPUT_DIR / "dangerous_error_summary_v1_4b.json", summary)
    write_md(DOC_DIR / "full_clean_dryrun_dangerous_error_report_v1_4b.md", ["# Full Clean Dry-Run Dangerous Error Report v1.4b", "", f"Generated time: {now_text()}", f"Dangerous error count: {err_count}", "", *table_lines(counter)])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
