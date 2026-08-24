from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from pathlib import Path

from full_clean_v1_4_common import (
    DOC_DIR,
    OUTPUT_DIR,
    POLICY_FIELDS_EXTRA,
    append_csv_row,
    ensure_dir,
    now_text,
    open_csv_writer,
    policy_decide,
    table_lines,
    write_json,
    write_md,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply v4.2/v1.1 conservative dry-run policy on full raw SemCap trace.")
    parser.add_argument("--input", type=Path, default=OUTPUT_DIR / "full_raw_semcap_trace_v1_4.csv")
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR / "full_clean_task_trace_v1_4.csv")
    parser.add_argument("--summary", type=Path, default=OUTPUT_DIR / "full_clean_task_trace_summary_v1_4.json")
    parser.add_argument("--progress-every", type=int, default=10000)
    args = parser.parse_args()

    if not args.input.exists():
        raise FileNotFoundError(f"Missing SemCap trace: {args.input}")
    counters = {
        "dryrun_decision": Counter(),
        "dryrun_bucket": Counter(),
        "clean_confidence_bucket": Counter(),
        "source_group": Counter(),
        "task_type": Counter(),
    }
    cross = defaultdict(Counter)
    row_count = 0
    with args.input.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        for field in POLICY_FIELDS_EXTRA:
            if field not in fieldnames:
                fieldnames.append(field)
        out_f, writer = open_csv_writer(args.output, fieldnames)
        try:
            for row in reader:
                out = policy_decide(row)
                append_csv_row(writer, out, fieldnames)
                row_count += 1
                for key in counters:
                    counters[key][out.get(key, "") or "<blank>"] += 1
                cross[f"{out.get('source_group','')}|{out.get('task_type','')}"][out.get("dryrun_decision", "")] += 1
                if args.progress_every and row_count % args.progress_every == 0:
                    print(f"[v1.4 policy] processed {row_count} tasks")
        finally:
            out_f.close()

    summary = {
        "generated_time": now_text(),
        "input_file": str(args.input),
        "output_file": str(args.output),
        "row_count": row_count,
        "counters": {key: dict(value) for key, value in counters.items()},
        "source_group_task_type_decision": {key: dict(value) for key, value in cross.items()},
        "is_final_clean_dataset": False,
        "no_full_cleaning_no_split_no_baseline_no_training": True,
    }
    write_json(args.summary, summary)
    lines = [
        "# Full Clean Task Trace Report v1.4",
        "",
        f"Generated time: {now_text()}",
        f"Input file: `{args.input}`",
        f"Output file: `{args.output}`",
        f"Sample count: {row_count}",
        "",
        "This is a dry-run task trace, not a final clean dataset.",
        "",
        "## Dry-Run Decision",
        "",
        *table_lines(summary["counters"]["dryrun_decision"]),
        "",
        "## Dry-Run Bucket",
        "",
        *table_lines(summary["counters"]["dryrun_bucket"]),
        "",
        "## Clean Confidence Bucket",
        "",
        *table_lines(summary["counters"]["clean_confidence_bucket"]),
    ]
    write_md(DOC_DIR / "full_clean_task_trace_report_v1_4.md", lines)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
