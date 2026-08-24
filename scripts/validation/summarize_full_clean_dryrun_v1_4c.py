from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path

from full_clean_v1_4c_common import (
    DOC_DIR,
    OUTPUT_DIR,
    REGRESSION_DIR,
    V14B_SUMMARY,
    archive_v14c,
    dangerous_flags_v14c,
    load_json,
    now_text,
    open_csv_writer,
    table_lines,
    write_json,
    write_md,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize v1.4c full clean dry-run.")
    parser.add_argument("--trace", type=Path, default=OUTPUT_DIR / "full_clean_task_trace_v1_4c.csv")
    parser.add_argument("--skip-archive", action="store_true")
    args = parser.parse_args()
    if not args.trace.exists():
        raise FileNotFoundError(f"Missing v1.4c task trace: {args.trace}")

    decisions = Counter()
    buckets = Counter()
    changes = Counter()
    dangerous_rows: list[dict[str, str]] = []
    rows = 0
    with args.trace.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows += 1
            decisions[row.get("dryrun_decision_v1_4c", "")] += 1
            buckets[row.get("dryrun_bucket_v1_4c", "")] += 1
            changes[row.get("v1_4c_change_from_v1_4b", "")] += 1
            flags = dangerous_flags_v14c(row)
            if flags:
                dangerous_rows.append(
                    {
                        "task_id": row.get("task_id", ""),
                        "source_group": row.get("source_group", ""),
                        "task_type": row.get("task_type", ""),
                        "dangerous_flags": ";".join(flags),
                        "dryrun_decision_v1_4c": row.get("dryrun_decision_v1_4c", ""),
                        "dryrun_bucket_v1_4c": row.get("dryrun_bucket_v1_4c", ""),
                        "query_text": row.get("query_text", ""),
                    }
                )

    danger_csv = OUTPUT_DIR / "dangerous_error_trace_v1_4c.csv"
    out_f, writer = open_csv_writer(
        danger_csv,
        ["task_id", "source_group", "task_type", "dangerous_flags", "dryrun_decision_v1_4c", "dryrun_bucket_v1_4c", "query_text"],
    )
    try:
        for row in dangerous_rows:
            writer.writerow(row)
    finally:
        out_f.close()

    v14b = load_json(V14B_SUMMARY)
    regression = load_json(REGRESSION_DIR / "v1_5d_failure_regression_summary_v1_4c.json")
    still_clean = int(regression.get("v1_5d_failed_still_clean_count", -1))
    moved_out = int(regression.get("v1_5d_failed_moved_out_count", -1))
    dangerous_count = len(dangerous_rows)
    can_accept = rows > 0 and dangerous_count == 0 and still_clean == 0 and moved_out == 32
    decision = "GO_TO_V1_5E_SMALL_CLEAN_CANDIDATE_QA" if can_accept else "NO_GO_INSPECT_V1_4C_REGRESSION_FAILURES"
    summary = {
        "generated_time": now_text(),
        "full_task_rows": rows,
        "v1_4c_clean_candidate_count": decisions.get("dryrun_clean_candidate", 0),
        "v1_4b_clean_candidate_count": v14b.get("v1_4b_clean_candidate_count"),
        "clean_candidate_delta_from_v1_4b": decisions.get("dryrun_clean_candidate", 0) - int(v14b.get("v1_4b_clean_candidate_count", 0)),
        "v1_5d_failed_clean_candidates_total": 32,
        "v1_5d_failed_clean_candidates_still_clean_count": still_clean,
        "v1_5d_failed_clean_candidates_moved_out_count": moved_out,
        "dangerous_error_count": dangerous_count,
        "decision_distribution": dict(decisions),
        "bucket_distribution": dict(buckets),
        "change_distribution": dict(changes),
        "can_accept_v1_4c_full_clean_dryrun": can_accept,
        "can_prepare_v1_5e_small_clean_candidate_qa": can_accept,
        "can_generate_final_clean_dataset_now": False,
        "can_generate_service_level_final_clean_dataset_v1_6": False,
        "can_generate_api_level_final_clean_dataset_v1_6": False,
        "can_create_split_now": False,
        "can_run_baseline_now": False,
        "can_train_model_now": False,
        "go_no_go_decision_v1_4c": decision,
        "recommended_next_step": "v1.5e small clean-candidate QA only, max 100 rows" if can_accept else "inspect v1.4c failed regression and tighten rules before more QA",
    }
    write_json(OUTPUT_DIR / "full_clean_dryrun_summary_v1_4c.json", summary)
    write_json(OUTPUT_DIR / "dangerous_error_summary_v1_4c.json", {"generated_time": now_text(), "dangerous_error_count": dangerous_count, "trace": str(danger_csv)})

    summary_lines = [
        "# Full Clean Dry-Run Summary Report v1.4c",
        "",
        f"Generated time: {now_text()}",
        f"Input trace: `{args.trace}`",
        f"Full task rows: {rows}",
        f"v1.4b clean candidate count: {summary['v1_4b_clean_candidate_count']}",
        f"v1.4c clean candidate count: {summary['v1_4c_clean_candidate_count']}",
        f"clean candidate delta from v1.4b: {summary['clean_candidate_delta_from_v1_4b']}",
        f"v1.5d failed clean candidates still clean: {still_clean}",
        f"v1.5d failed clean candidates moved out: {moved_out}",
        f"dangerous_error_count: {dangerous_count}",
        "",
        "This is a dry-run summary. It is not a final clean dataset.",
        "",
        "## Decision Distribution",
        "",
        *table_lines(decisions),
        "",
        "## Bucket Distribution",
        "",
        *table_lines(buckets),
        "",
        "## v1.4b -> v1.4c Change Distribution",
        "",
        *table_lines(changes),
    ]
    write_md(DOC_DIR / "full_clean_dryrun_summary_report_v1_4c.md", summary_lines)

    go_lines = [
        "# Full Clean Dry-Run v1.4c Go/No-Go Report",
        "",
        f"Generated time: {now_text()}",
        f"Input trace: `{args.trace}`",
        "",
        f"Go / No-Go Decision v1.4c: {decision}",
        "",
        f"- can_accept_v1_4c_full_clean_dryrun: {str(can_accept).lower()}",
        f"- can_prepare_v1_5e_small_clean_candidate_qa: {str(can_accept).lower()}",
        "- can_generate_final_clean_dataset_now: false",
        "- can_generate_service_level_final_clean_dataset_v1_6: false",
        "- can_generate_api_level_final_clean_dataset_v1_6: false",
        "- can_create_split_now: false",
        "- can_run_baseline_now: false",
        "- can_train_model_now: false",
        "",
        f"recommended_next_step: {summary['recommended_next_step']}",
        "",
        "v1.4c only checks that the v1.5d false keeps are removed from dry-run clean candidates. It does not authorize v1.6.",
    ]
    write_md(DOC_DIR / "full_clean_dryrun_v1_4c_go_no_go_report.md", go_lines)

    archived = []
    if not args.skip_archive:
        archived = archive_v14c(Path.cwd())
    summary["archive_file_count"] = len(archived)
    write_json(OUTPUT_DIR / "full_clean_dryrun_summary_v1_4c.json", summary)

    print("Generated files:")
    print(f"- {OUTPUT_DIR / 'full_clean_task_trace_v1_4c.csv'}")
    print(f"- {OUTPUT_DIR / 'full_clean_dryrun_summary_v1_4c.json'}")
    print(f"- {DOC_DIR / 'full_clean_dryrun_v1_4c_go_no_go_report.md'}")
    print(f"v1.4b clean candidate count: {summary['v1_4b_clean_candidate_count']}")
    print(f"v1.4c clean candidate count: {summary['v1_4c_clean_candidate_count']}")
    print(f"clean candidate delta from v1.4b: {summary['clean_candidate_delta_from_v1_4b']}")
    print(f"v1.5d failed still clean count: {still_clean}")
    print(f"v1.5d failed moved out count: {moved_out}")
    print(f"dangerous_error_count: {dangerous_count}")
    print(f"Go / No-Go Decision v1.4c: {decision}")
    return 0 if can_accept else 2


if __name__ == "__main__":
    raise SystemExit(main())
