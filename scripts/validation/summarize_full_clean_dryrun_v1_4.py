from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path

from full_clean_v1_4_common import (
    DEDUP_DIR,
    DOC_DIR,
    OUTPUT_DIR,
    TASK_BUCKET_DIR,
    archive_v1_4,
    load_json,
    now_text,
    table_lines,
    write_json,
    write_md,
)


def count_task_trace(path: Path) -> dict:
    counters = {
        "dryrun_decision": Counter(),
        "dryrun_bucket": Counter(),
        "clean_confidence_bucket": Counter(),
        "source_group": Counter(),
        "task_type": Counter(),
    }
    row_count = 0
    if not path.exists():
        return {"row_count": 0, "missing": True, "counters": {key: {} for key in counters}}
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row_count += 1
            for key in counters:
                counters[key][row.get(key, "") or "<blank>"] += 1
    return {"row_count": row_count, "missing": False, "counters": {key: dict(value) for key, value in counters.items()}}


def line_count_csv(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        next(reader, None)
        return sum(1 for _ in reader)


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize v1.4 full clean dry-run and archive outputs.")
    parser.add_argument("--task-trace", type=Path, default=OUTPUT_DIR / "full_clean_task_trace_v1_4.csv")
    parser.add_argument("--candidate-summary", type=Path, default=OUTPUT_DIR / "full_clean_candidate_trace_summary_v1_4.json")
    parser.add_argument("--danger-summary", type=Path, default=OUTPUT_DIR / "dangerous_error_summary_v1_4.json")
    parser.add_argument("--dedup-summary", type=Path, default=DEDUP_DIR / "dedup_precheck_summary_v1_4.json")
    parser.add_argument("--qa-summary", type=Path, default=OUTPUT_DIR / "final_qa_sampling_frame_summary_v1_4.json")
    parser.add_argument("--summary-json", type=Path, default=OUTPUT_DIR / "full_clean_dryrun_summary_v1_4.json")
    parser.add_argument("--skip-archive", action="store_true", help="Debug only. Do not use for final requested run.")
    args = parser.parse_args()

    task_info = count_task_trace(args.task_trace)
    candidate_summary = load_json(args.candidate_summary)
    danger_summary = load_json(args.danger_summary)
    dedup_summary = load_json(args.dedup_summary)
    qa_summary = load_json(args.qa_summary)

    decision_counts = task_info["counters"]["dryrun_decision"]
    clean_task_count = int(decision_counts.get("dryrun_clean_candidate", 0))
    removed_task_count = int(decision_counts.get("dryrun_removed", 0))
    service_leak_task_count = int(decision_counts.get("dryrun_service_leak_only", 0))
    uncertain_task_count = int(decision_counts.get("dryrun_uncertain", 0))
    candidate_decision_counts = candidate_summary.get("counters", {}).get("dryrun_decision", {})
    clean_candidate_rows = int(candidate_decision_counts.get("dryrun_clean_candidate", 0))
    dangerous_error_count = int(danger_summary.get("dangerous_error_count", -1))
    missing_join_count = int(candidate_summary.get("missing_task_join_count", -1))
    duplicate_group_count = int(dedup_summary.get("duplicate_group_count", -1))
    bucket_files = {
        path.name: line_count_csv(path)
        for path in sorted(TASK_BUCKET_DIR.glob("*.csv"))
    } if TASK_BUCKET_DIR.exists() else {}

    can_accept = (
        task_info["row_count"] > 0
        and not task_info.get("missing")
        and dangerous_error_count == 0
        and missing_join_count == 0
        and duplicate_group_count >= 0
        and bool(bucket_files)
    )
    go_no_go = "GO_TO_FINAL_QA_PREPARATION" if can_accept else "NO_GO_FIX_DRYRUN_PIPELINE"
    final_clean_allowed = False
    split_allowed = False
    baseline_allowed = False
    training_allowed = False

    summary = {
        "generated_time": now_text(),
        "input_task_trace": str(args.task_trace),
        "full_raw_task_rows": task_info["row_count"],
        "full_raw_candidate_rows": int(candidate_summary.get("candidate_row_count", 0)),
        "dryrun_clean_candidate_task_count": clean_task_count,
        "dryrun_clean_candidate_candidate_rows": clean_candidate_rows,
        "dryrun_removed_task_count": removed_task_count,
        "dryrun_service_leak_only_task_count": service_leak_task_count,
        "dryrun_uncertain_task_count": uncertain_task_count,
        "dangerous_error_count": dangerous_error_count,
        "candidate_level_missing_join_count": missing_join_count,
        "candidate_level_join_completeness": candidate_summary.get("join_completeness", "unknown"),
        "duplicate_group_count": duplicate_group_count,
        "task_decision_distribution": decision_counts,
        "task_bucket_distribution": task_info["counters"]["dryrun_bucket"],
        "candidate_decision_distribution": candidate_decision_counts,
        "bucket_file_row_counts": bucket_files,
        "qa_pool_distribution": qa_summary.get("qa_pool_distribution", {}),
        "go_no_go_decision_v1_4": go_no_go,
        "can_accept_full_clean_dryrun": can_accept,
        "can_prepare_final_qa": can_accept,
        "can_generate_final_clean_dataset": final_clean_allowed,
        "can_split": split_allowed,
        "can_run_baseline": baseline_allowed,
        "can_train_model": training_allowed,
        "explicit_boundaries": {
            "final_clean_dataset_generated": False,
            "split_generated": False,
            "baseline_run": False,
            "model_training_run": False,
            "new_human_review_added": False,
        },
    }
    write_json(args.summary_json, summary)

    report_lines = [
        "# Full Clean Dry-Run Summary Report v1.4",
        "",
        f"Generated time: {now_text()}",
        f"Input task trace: `{args.task_trace}`",
        f"Sample count: {task_info['row_count']}",
        "",
        "This is still a full raw dry-run. It does not generate a final clean dataset.",
        "",
        "## Core Counts",
        "",
        f"- Full raw task rows: {task_info['row_count']}",
        f"- Full raw candidate rows: {summary['full_raw_candidate_rows']}",
        f"- dryrun_clean_candidate task count: {clean_task_count}",
        f"- dryrun_clean_candidate candidate rows: {clean_candidate_rows}",
        f"- dryrun_removed task count: {removed_task_count}",
        f"- dryrun_service_leak_only task count: {service_leak_task_count}",
        f"- dryrun_uncertain task count: {uncertain_task_count}",
        f"- dangerous_error_count: {dangerous_error_count}",
        f"- candidate-level missing joins: {missing_join_count}",
        f"- duplicate group count: {duplicate_group_count}",
        "",
        "## Task Decision Distribution",
        "",
        *table_lines(decision_counts),
        "",
        "## Task Bucket Distribution",
        "",
        *table_lines(task_info["counters"]["dryrun_bucket"]),
        "",
        "## QA Pool Distribution",
        "",
        *table_lines(qa_summary.get("qa_pool_distribution", {})),
    ]
    write_md(DOC_DIR / "full_clean_dryrun_summary_report_v1_4.md", report_lines)

    go_lines = [
        "# Full Clean Dry-Run v1.4 Go/No-Go Report",
        "",
        f"Generated time: {now_text()}",
        f"Input task trace: `{args.task_trace}`",
        f"Sample count: {task_info['row_count']}",
        "",
        f"## Go / No-Go Decision: {go_no_go}",
        "",
        f"- can_accept_full_clean_dryrun: {can_accept}",
        f"- can_prepare_final_qa: {can_accept}",
        f"- can_generate_final_clean_dataset: {final_clean_allowed}",
        f"- can_split: {split_allowed}",
        f"- can_run_baseline: {baseline_allowed}",
        f"- can_train_model: {training_allowed}",
        "",
        "## Blocking Checks",
        "",
        f"- dangerous_error_count == 0: {dangerous_error_count == 0}",
        f"- candidate-level join complete: {missing_join_count == 0}",
        f"- dedup precheck completed: {duplicate_group_count >= 0}",
        f"- task bucket files generated: {bool(bucket_files)}",
        "",
        "Even if this dry-run passes, it only supports final QA preparation. It does not authorize final clean dataset generation, split, baseline, or model training.",
    ]
    write_md(DOC_DIR / "full_clean_dryrun_v1_4_go_no_go_report.md", go_lines)

    archive_files: list[str] = []
    if not args.skip_archive:
        archive_files = archive_v1_4(Path.cwd())
    summary["archive_file_count"] = len(archive_files)
    summary["archive_files"] = archive_files
    write_json(args.summary_json, summary)

    print("Generated v1.4 files:")
    for path in [
        args.summary_json,
        DOC_DIR / "full_clean_dryrun_summary_report_v1_4.md",
        DOC_DIR / "full_clean_dryrun_v1_4_go_no_go_report.md",
    ]:
        print(f"- {path}")
    print(f"Full raw task rows: {task_info['row_count']}")
    print(f"Full raw candidate rows: {summary['full_raw_candidate_rows']}")
    print(f"dryrun_clean_candidate task count: {clean_task_count}")
    print(f"dryrun_clean_candidate candidate rows: {clean_candidate_rows}")
    print(f"removed task count: {removed_task_count}")
    print(f"service_leak_only task count: {service_leak_task_count}")
    print(f"uncertain task count: {uncertain_task_count}")
    print(f"dangerous_error_count: {dangerous_error_count}")
    print(f"candidate-level join completeness: {candidate_summary.get('join_completeness', 'unknown')}")
    print(f"duplicate group count: {duplicate_group_count}")
    print(f"Go/No-Go Decision v1.4: {go_no_go}")
    return 0 if can_accept else 2


if __name__ == "__main__":
    raise SystemExit(main())
