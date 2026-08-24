from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path

from full_clean_v1_4b_common import DIFF_DIR, DOC_DIR, OUTPUT_DIR, REGRESSION_DIR, archive_v14b, load_json, now_text, table_lines, write_json, write_md


def count_decisions(path: Path) -> tuple[int, Counter, Counter]:
    rows = 0
    decisions = Counter()
    buckets = Counter()
    if not path.exists():
        return rows, decisions, buckets
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            rows += 1
            decisions[row.get("dryrun_decision_v1_4b", "")] += 1
            buckets[row.get("dryrun_bucket_v1_4b", "")] += 1
    return rows, decisions, buckets


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize v1.4b full clean dry-run.")
    parser.add_argument("--skip-archive", action="store_true")
    args = parser.parse_args()
    task_trace = OUTPUT_DIR / "full_clean_task_trace_v1_4b.csv"
    rows, decisions, buckets = count_decisions(task_trace)
    candidate_summary = load_json(OUTPUT_DIR / "full_clean_candidate_trace_summary_v1_4b.json")
    danger = load_json(OUTPUT_DIR / "dangerous_error_summary_v1_4b.json")
    failure = load_json(REGRESSION_DIR / "v1_5c_failure_regression_summary_v1_4b.json")
    calib = load_json(REGRESSION_DIR / "semcap_v1_2_calibration_eval_summary.json")
    dedup = load_json(OUTPUT_DIR / "dedup_precheck/dedup_precheck_summary_v1_4b.json")
    diff = load_json(DIFF_DIR / "v1_4_vs_v1_4b_summary.json")
    dangerous_ok = int(danger.get("dangerous_error_count", -1)) == 0
    join_ok = candidate_summary.get("join_completeness") == "complete"
    fail_ok = int(failure.get("failure_ids_still_clean_candidate", -1)) == 0 and int(failure.get("failure_ids_found_in_full_trace", 0)) == int(failure.get("failure_ids_total", -1))
    calib_ok = int(calib.get("dangerous_false_keep", -1)) == 0
    dedup_ok = int(dedup.get("duplicate_group_count", -1)) >= 0
    can_accept = rows > 0 and dangerous_ok and join_ok and fail_ok and calib_ok and dedup_ok
    decision = "GO_TO_V1_5D_IMPACTED_CLEAN_CANDIDATE_QA" if can_accept else "NO_GO_INSPECT_V1_4B_REGRESSION_FAILURES"
    summary = {
        "generated_time": now_text(),
        "full_task_rows": rows,
        "full_candidate_rows": candidate_summary.get("candidate_row_count", 0),
        "v1_4b_clean_candidate_count": decisions.get("dryrun_clean_candidate", 0),
        "v1_4_clean_candidate_count": diff.get("v1_4_clean_candidate_count", 0),
        "clean_candidate_delta": diff.get("clean_candidate_delta", 0),
        "v1_5c_failure_ids_still_clean_count": failure.get("failure_ids_still_clean_candidate"),
        "v1_5c_failure_ids_moved_out_count": failure.get("failure_ids_downgraded_to_uncertain_or_removed"),
        "dangerous_error_count": danger.get("dangerous_error_count"),
        "candidate_level_join_completeness": candidate_summary.get("join_completeness"),
        "semcap_v1_2_calibration_dangerous_false_keep": calib.get("dangerous_false_keep"),
        "semcap_v1_2_coverage_mismatch_capture": calib.get("coverage_mismatch_capture"),
        "dedup_group_count": dedup.get("duplicate_group_count"),
        "decision_distribution": dict(decisions),
        "bucket_distribution": dict(buckets),
        "can_accept_v1_4b_full_clean_dryrun": can_accept,
        "can_prepare_impacted_clean_candidate_qa_v1_5d": can_accept,
        "can_generate_final_clean_dataset_now": False,
        "can_create_split_now": False,
        "can_run_baseline_now": False,
        "can_train_model_now": False,
        "go_no_go_decision_v1_4b": decision,
        "recommended_next_step": "v1.5d impacted clean-candidate QA only" if can_accept else "inspect v1.4b regression failures and tighten rules again, without final dataset generation",
    }
    write_json(OUTPUT_DIR / "full_clean_dryrun_summary_v1_4b.json", summary)
    lines = [
        "# Full Clean Dry-Run Summary Report v1.4b",
        "",
        f"Generated time: {now_text()}",
        f"Full task rows: {rows}",
        f"v1.4b clean candidate count: {summary['v1_4b_clean_candidate_count']}",
        f"v1.4 clean candidate count: {summary['v1_4_clean_candidate_count']}",
        f"clean candidate delta: {summary['clean_candidate_delta']}",
        f"v1.5c failure ids still clean count: {summary['v1_5c_failure_ids_still_clean_count']}",
        f"dangerous_error_count: {summary['dangerous_error_count']}",
        f"candidate-level join completeness: {summary['candidate_level_join_completeness']}",
        f"SemCap v1.2 calibration dangerous_false_keep: {summary['semcap_v1_2_calibration_dangerous_false_keep']}",
        f"dedup group count: {summary['dedup_group_count']}",
        "",
        "## Decision Distribution",
        "",
        *table_lines(decisions),
        "",
        "## Bucket Distribution",
        "",
        *table_lines(buckets),
    ]
    write_md(DOC_DIR / "full_clean_dryrun_summary_report_v1_4b.md", lines)
    go_lines = [
        "# Full Clean Dry-Run v1.4b Go/No-Go Report",
        "",
        f"Generated time: {now_text()}",
        "",
        f"Go / No-Go Decision v1.4b: {decision}",
        "",
        f"- can_accept_v1_4b_full_clean_dryrun: {can_accept}",
        f"- can_prepare_impacted_clean_candidate_qa_v1_5d: {can_accept}",
        "- can_generate_final_clean_dataset_now: false",
        "- can_create_split_now: false",
        "- can_run_baseline_now: false",
        "- can_train_model_now: false",
        "",
        f"recommended_next_step: {summary['recommended_next_step']}",
    ]
    write_md(DOC_DIR / "full_clean_dryrun_v1_4b_go_no_go_report.md", go_lines)
    archived = []
    if not args.skip_archive:
        archived = archive_v14b(Path.cwd())
    summary["archive_file_count"] = len(archived)
    write_json(OUTPUT_DIR / "full_clean_dryrun_summary_v1_4b.json", summary)
    print("Generated files:")
    print(f"- {OUTPUT_DIR / 'full_clean_dryrun_summary_v1_4b.json'}")
    print(f"- {DOC_DIR / 'full_clean_dryrun_summary_report_v1_4b.md'}")
    print(f"- {DOC_DIR / 'full_clean_dryrun_v1_4b_go_no_go_report.md'}")
    print(f"v1.4b clean candidate count: {summary['v1_4b_clean_candidate_count']}")
    print(f"v1.4 clean candidate count: {summary['v1_4_clean_candidate_count']}")
    print(f"clean candidate delta: {summary['clean_candidate_delta']}")
    print(f"v1.5c 32 failure ids still clean count: {summary['v1_5c_failure_ids_still_clean_count']}")
    print(f"v1.5c 32 failure ids moved out count: {summary['v1_5c_failure_ids_moved_out_count']}")
    print(f"dangerous_error_count: {summary['dangerous_error_count']}")
    print(f"candidate-level join completeness: {summary['candidate_level_join_completeness']}")
    print(f"SemCap v1.2 calibration dangerous_false_keep: {summary['semcap_v1_2_calibration_dangerous_false_keep']}")
    print(f"SemCap v1.2 coverage_mismatch_capture: {summary['semcap_v1_2_coverage_mismatch_capture']}")
    print(f"dedup group count: {summary['dedup_group_count']}")
    print(f"Go / No-Go Decision v1.4b: {decision}")
    return 0 if can_accept else 2


if __name__ == "__main__":
    raise SystemExit(main())
