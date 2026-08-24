from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path

from full_clean_v1_4_common import (
    DEDUP_DIR,
    DOC_DIR,
    OUTPUT_DIR,
    ensure_dir,
    now_text,
    open_csv_writer,
    table_lines,
    write_json,
    write_md,
)


QA_EXTRA_FIELDS = [
    "final_qa_pool",
    "qa_priority_reason",
    "dedup_group_id",
    "dedup_group_size",
    "is_dedup_representative",
    "dedup_reason",
]


def load_dedup(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    mapping: dict[str, dict[str, str]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            mapping[row.get("task_id", "")] = {
                "dedup_group_id": row.get("dedup_group_id", ""),
                "dedup_group_size": row.get("dedup_group_size", "1"),
                "is_dedup_representative": row.get("is_dedup_representative", ""),
                "dedup_reason": row.get("dedup_reason", ""),
            }
    return mapping


def qa_pool(row: dict[str, str], dedup: dict[str, str]) -> tuple[str, str]:
    if dedup.get("dedup_group_id"):
        return "duplicate_clean_candidate", "clean candidate belongs to a duplicate group"
    decision = row.get("dryrun_decision", "")
    bucket = row.get("dryrun_bucket", "")
    conf = row.get("clean_confidence_bucket", "")
    if decision == "dryrun_clean_candidate":
        if conf == "clean_candidate_high_conf":
            return "clean_candidate_high_conf", "high-confidence dry-run clean candidate"
        return "clean_candidate_medium_conf", "medium-confidence dry-run clean candidate"
    if decision == "dryrun_service_leak_only":
        return "service_leak_only", "service leak separated from clean service discovery"
    if decision == "dryrun_uncertain":
        return bucket if bucket else "dryrun_uncertain", "uncertain bucket requires final QA review"
    if decision == "dryrun_removed":
        return bucket if bucket else "dryrun_removed", "removed bucket spot-check"
    return "unknown", "unexpected or missing dry-run decision"


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare v1.4 final QA sampling frame without sampling final data.")
    parser.add_argument("--task-trace", type=Path, default=OUTPUT_DIR / "full_clean_task_trace_v1_4.csv")
    parser.add_argument("--dedup-trace", type=Path, default=DEDUP_DIR / "dryrun_clean_candidate_dedup_trace_v1_4.csv")
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR / "final_qa_sampling_frame_v1_4.csv")
    parser.add_argument("--summary", type=Path, default=OUTPUT_DIR / "final_qa_sampling_frame_summary_v1_4.json")
    args = parser.parse_args()

    if not args.task_trace.exists():
        raise FileNotFoundError(f"Missing task trace CSV: {args.task_trace}")
    dedup_map = load_dedup(args.dedup_trace)
    counters = Counter()
    row_count = 0
    with args.task_trace.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        for field in QA_EXTRA_FIELDS:
            if field not in fieldnames:
                fieldnames.append(field)
        out_f, writer = open_csv_writer(args.output, fieldnames)
        try:
            for row in reader:
                dedup = dedup_map.get(row.get("task_id", ""), {})
                pool, reason = qa_pool(row, dedup)
                out = dict(row)
                out.update(
                    {
                        "final_qa_pool": pool,
                        "qa_priority_reason": reason,
                        "dedup_group_id": dedup.get("dedup_group_id", ""),
                        "dedup_group_size": dedup.get("dedup_group_size", "1"),
                        "is_dedup_representative": dedup.get("is_dedup_representative", ""),
                        "dedup_reason": dedup.get("dedup_reason", ""),
                    }
                )
                writer.writerow({field: out.get(field, "") for field in fieldnames})
                counters[pool] += 1
                row_count += 1
        finally:
            out_f.close()

    summary = {
        "generated_time": now_text(),
        "task_trace_file": str(args.task_trace),
        "dedup_trace_file": str(args.dedup_trace),
        "output_file": str(args.output),
        "row_count": row_count,
        "qa_pool_distribution": dict(counters),
        "recommended_manual_qa_plan": {
            "clean_candidate_high_conf": 100,
            "clean_candidate_medium_conf": 100,
            "removed_buckets_total": 80,
            "service_leak_only": 50,
            "uncertain_buckets_total": 80,
            "duplicate_clean_candidate": 50,
        },
        "is_final_clean_dataset": False,
    }
    write_json(args.summary, summary)
    plan_lines = [
        "# Final QA Recommended Sample Plan v1.4",
        "",
        f"Generated time: {now_text()}",
        f"Sampling frame: `{args.output}`",
        "",
        "This is a recommended QA plan only. It does not create a final clean dataset.",
        "",
        "| QA pool | recommended count | purpose |",
        "|---|---:|---|",
        "| clean_candidate_high_conf | 100 | verify high-confidence keep precision |",
        "| clean_candidate_medium_conf | 100 | verify borderline keep precision |",
        "| removed_buckets_total | 80 | verify no overly aggressive removals |",
        "| service_leak_only | 50 | verify service leak separation |",
        "| uncertain_buckets_total | 80 | decide whether rules need expansion |",
        "| duplicate_clean_candidate | 50 | verify duplicate grouping quality |",
    ]
    write_md(OUTPUT_DIR / "final_qa_recommended_sample_plan_v1_4.md", plan_lines)
    report_lines = [
        "# Final QA Sampling Frame Report v1.4",
        "",
        f"Generated time: {now_text()}",
        f"Task trace input: `{args.task_trace}`",
        f"Dedup trace input: `{args.dedup_trace}`",
        f"Output file: `{args.output}`",
        f"Sample count: {row_count}",
        "",
        "No full cleaning, split, baseline, or training was run.",
        "",
        "## QA Pool Distribution",
        "",
        *table_lines(dict(counters)),
    ]
    write_md(DOC_DIR / "final_qa_sampling_frame_report_v1_4.md", report_lines)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
