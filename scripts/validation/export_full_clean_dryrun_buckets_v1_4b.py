from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path

from full_clean_v1_4b_common import DOC_DIR, OUTPUT_DIR, TASK_BUCKET_DIR, append_csv_row, ensure_dir, now_text, open_csv_writer, table_lines, write_json, write_md


BUCKET_FILES = {
    "clean_all": "dryrun_clean_candidate_task_level_v1_4b.csv",
    "clean_high": "dryrun_clean_candidate_high_conf_task_level_v1_4b.csv",
    "removed_all": "dryrun_removed_task_level_v1_4b.csv",
    "uncertain_all": "dryrun_uncertain_task_level_v1_4b.csv",
    "service_leak": "dryrun_service_leak_only_task_level_v1_4b.csv",
    "removed_api_leak": "removed_api_leak_task_level_v1_4b.csv",
    "removed_choice_space_invalid": "removed_choice_space_invalid_task_level_v1_4b.csv",
    "removed_semantic_mismatch": "removed_semantic_mismatch_task_level_v1_4b.csv",
    "removed_capability_mismatch": "removed_capability_mismatch_task_level_v1_4b.csv",
    "removed_wrong_gold_set": "removed_wrong_gold_set_task_level_v1_4b.csv",
    "uncertain_semcap": "uncertain_semcap_task_level_v1_4b.csv",
    "uncertain_generic_search_overtrusted": "uncertain_generic_search_overtrusted_task_level_v1_4b.csv",
    "uncertain_wrong_gold_set": "uncertain_wrong_gold_set_task_level_v1_4b.csv",
}


def targets(row: dict[str, str]) -> list[str]:
    decision = row.get("dryrun_decision_v1_4b", "")
    bucket = row.get("dryrun_bucket_v1_4b", "")
    out = []
    if decision == "dryrun_clean_candidate":
        out += ["clean_all", "clean_high"]
    elif decision == "dryrun_removed":
        out.append("removed_all")
        if bucket in BUCKET_FILES:
            out.append(bucket)
    elif decision == "dryrun_uncertain":
        out.append("uncertain_all")
        if bucket in BUCKET_FILES:
            out.append(bucket)
    elif decision == "dryrun_service_leak_only":
        out.append("service_leak")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Export v1.4b task buckets.")
    parser.add_argument("--input", type=Path, default=OUTPUT_DIR / "full_clean_task_trace_v1_4b.csv")
    args = parser.parse_args()
    if not args.input.exists():
        raise FileNotFoundError(f"Missing task trace: {args.input}")
    ensure_dir(TASK_BUCKET_DIR)
    counters = Counter()
    handles = {}
    with args.input.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        try:
            for key, filename in BUCKET_FILES.items():
                handles[key] = open_csv_writer(TASK_BUCKET_DIR / filename, fieldnames)
            for row in reader:
                counters[row.get("dryrun_decision_v1_4b", "")] += 1
                counters[f"bucket::{row.get('dryrun_bucket_v1_4b','')}"] += 1
                for key in targets(row):
                    append_csv_row(handles[key][1], row, fieldnames)
        finally:
            for fobj, _writer in handles.values():
                fobj.close()
    bucket_counts = {filename: sum(1 for _ in csv.reader(open(TASK_BUCKET_DIR / filename, encoding="utf-8-sig", newline=""))) - 1 for filename in BUCKET_FILES.values()}
    write_json(OUTPUT_DIR / "full_clean_dryrun_bucket_summary_v1_4b.json", {"generated_time": now_text(), "decision_and_bucket_counts": dict(counters), "bucket_file_row_counts": bucket_counts})
    write_md(DOC_DIR / "full_clean_dryrun_bucket_report_v1_4b.md", ["# Full Clean Dry-Run Bucket Report v1.4b", "", f"Generated time: {now_text()}", "", *table_lines(counters)])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
