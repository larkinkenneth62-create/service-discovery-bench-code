from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from pathlib import Path

from full_clean_v1_4_common import (
    DOC_DIR,
    OUTPUT_DIR,
    TASK_BUCKET_DIR,
    append_csv_row,
    ensure_dir,
    now_text,
    open_csv_writer,
    table_lines,
    write_json,
    write_md,
)


BUCKET_FILES = {
    "dryrun_clean_candidate_all": "dryrun_clean_candidate_all.csv",
    "dryrun_clean_candidate_high_conf": "dryrun_clean_candidate_high_conf.csv",
    "dryrun_clean_candidate_medium_conf": "dryrun_clean_candidate_medium_conf.csv",
    "dryrun_removed_all": "dryrun_removed_all.csv",
    "removed_api_leak": "removed_api_leak.csv",
    "removed_gold_missing": "removed_gold_missing.csv",
    "removed_choice_space_invalid": "removed_choice_space_invalid.csv",
    "removed_semantic_mismatch": "removed_semantic_mismatch.csv",
    "removed_capability_mismatch": "removed_capability_mismatch.csv",
    "dryrun_service_leak_only": "dryrun_service_leak_only.csv",
    "dryrun_uncertain_all": "dryrun_uncertain_all.csv",
    "uncertain_semcap": "uncertain_semcap.csv",
}


def row_targets(row: dict[str, str]) -> list[str]:
    decision = row.get("dryrun_decision", "")
    bucket = row.get("dryrun_bucket", "")
    targets: list[str] = []
    if decision == "dryrun_clean_candidate":
        targets.append("dryrun_clean_candidate_all")
        if row.get("clean_confidence_bucket") == "clean_candidate_high_conf":
            targets.append("dryrun_clean_candidate_high_conf")
        else:
            targets.append("dryrun_clean_candidate_medium_conf")
    elif decision == "dryrun_removed":
        targets.append("dryrun_removed_all")
        if bucket in BUCKET_FILES:
            targets.append(bucket)
    elif decision == "dryrun_service_leak_only":
        targets.append("dryrun_service_leak_only")
    elif decision == "dryrun_uncertain":
        targets.append("dryrun_uncertain_all")
        if bucket == "uncertain_semcap":
            targets.append("uncertain_semcap")
    return targets


def main() -> int:
    parser = argparse.ArgumentParser(description="Export v1.4 dry-run task buckets.")
    parser.add_argument("--input", type=Path, default=OUTPUT_DIR / "full_clean_task_trace_v1_4.csv")
    parser.add_argument("--bucket-dir", type=Path, default=TASK_BUCKET_DIR)
    parser.add_argument("--summary", type=Path, default=OUTPUT_DIR / "full_clean_dryrun_bucket_summary_v1_4.json")
    args = parser.parse_args()

    if not args.input.exists():
        raise FileNotFoundError(f"Missing task trace: {args.input}")
    ensure_dir(args.bucket_dir)
    counters = Counter()
    group_task = defaultdict(Counter)
    row_count = 0
    with args.input.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        handles = {}
        try:
            for key, filename in BUCKET_FILES.items():
                handles[key] = open_csv_writer(args.bucket_dir / filename, fieldnames)
            for row in reader:
                row_count += 1
                counters[row.get("dryrun_decision", "") or "<blank>"] += 1
                counters[f"bucket::{row.get('dryrun_bucket', '') or '<blank>'}"] += 1
                group_task[f"{row.get('source_group','')}|{row.get('task_type','')}"][row.get("dryrun_decision", "")] += 1
                for target in row_targets(row):
                    append_csv_row(handles[target][1], row, fieldnames)
        finally:
            for file_handle, _writer in handles.values():
                file_handle.close()

    bucket_paths = {key: str(args.bucket_dir / filename) for key, filename in BUCKET_FILES.items()}
    summary = {
        "generated_time": now_text(),
        "input_file": str(args.input),
        "bucket_dir": str(args.bucket_dir),
        "row_count": row_count,
        "bucket_files": bucket_paths,
        "decision_and_bucket_counts": dict(counters),
        "source_group_task_type_decision": {key: dict(value) for key, value in group_task.items()},
        "is_final_clean_dataset": False,
    }
    write_json(args.summary, summary)
    lines = [
        "# Full Clean Dry-Run Bucket Report v1.4",
        "",
        f"Generated time: {now_text()}",
        f"Input file: `{args.input}`",
        f"Bucket directory: `{args.bucket_dir}`",
        f"Sample count: {row_count}",
        "",
        "These bucket files are dry-run diagnostic exports, not final clean datasets.",
        "",
        "## Output Bucket Files",
        "",
        *[f"- `{path}`" for path in bucket_paths.values()],
        "",
        "## Decision And Bucket Counts",
        "",
        *table_lines(dict(counters)),
    ]
    write_md(DOC_DIR / "full_clean_dryrun_bucket_report_v1_4.md", lines)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
