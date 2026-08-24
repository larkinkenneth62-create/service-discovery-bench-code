from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from pathlib import Path

from full_clean_v1_4b_common import DOC_DIR, OUTPUT_DIR, V14B_POLICY_FIELDS, append_csv_row, now_text, open_csv_writer, policy_decide_v14b, table_lines, write_json, write_md


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply tightened v1.4b dry-run policy.")
    parser.add_argument("--input", type=Path, default=OUTPUT_DIR / "full_raw_semcap_v1_2_trace_v1_4b.csv")
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR / "full_clean_task_trace_v1_4b.csv")
    parser.add_argument("--progress-every", type=int, default=10000)
    args = parser.parse_args()
    if not args.input.exists():
        raise FileNotFoundError(f"Missing SemCap v1.2 trace: {args.input}")
    counters = {"decision": Counter(), "bucket": Counter(), "change": Counter(), "source_group": Counter(), "task_type": Counter()}
    cross = defaultdict(Counter)
    row_count = 0
    with args.input.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        for field in V14B_POLICY_FIELDS:
            if field not in fieldnames:
                fieldnames.append(field)
        out_f, writer = open_csv_writer(args.output, fieldnames)
        try:
            for row in reader:
                out = policy_decide_v14b(row)
                append_csv_row(writer, out, fieldnames)
                row_count += 1
                counters["decision"][out.get("dryrun_decision_v1_4b", "")] += 1
                counters["bucket"][out.get("dryrun_bucket_v1_4b", "")] += 1
                counters["change"][out.get("v1_4b_change_from_v1_4", "")] += 1
                counters["source_group"][out.get("source_group", "")] += 1
                counters["task_type"][out.get("task_type", "")] += 1
                cross[f"{out.get('source_group','')}|{out.get('task_type','')}"][out.get("dryrun_decision_v1_4b", "")] += 1
                if args.progress_every and row_count % args.progress_every == 0:
                    print(f"[v1.4b policy] processed {row_count} rows")
        finally:
            out_f.close()
    summary = {
        "generated_time": now_text(),
        "input_file": str(args.input),
        "output_file": str(args.output),
        "row_count": row_count,
        "counters": {k: dict(v) for k, v in counters.items()},
        "source_group_task_type_decision": {k: dict(v) for k, v in cross.items()},
    }
    write_json(OUTPUT_DIR / "full_clean_task_trace_summary_v1_4b.json", summary)
    write_md(
        DOC_DIR / "full_clean_task_trace_report_v1_4b.md",
        [
            "# Full Clean Task Trace Report v1.4b",
            "",
            f"Generated time: {now_text()}",
            f"Input: `{args.input}`",
            f"Output: `{args.output}`",
            f"Rows: {row_count}",
            "",
            "This is a dry-run trace, not a final clean dataset.",
            "",
            "## Decision Distribution",
            "",
            *table_lines(counters["decision"]),
            "",
            "## Bucket Distribution",
            "",
            *table_lines(counters["bucket"]),
        ],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
