from __future__ import annotations

import argparse
import csv
from pathlib import Path

from final_qa_v1_5_common import (
    OUTPUT_DIR,
    V1_4_DEDUP_SUMMARY,
    V1_4_DEDUP_TRACE,
    V1_4_GO_NO_GO_REPORT,
    V1_4_QA_FRAME,
    V1_4_SUMMARY,
    V1_4_SUMMARY_REPORT,
    V1_4_TASK_TRACE,
    count_csv_rows,
    ensure_dir,
    load_json,
    now_text,
    resolve_bucket_aliases,
    write_json,
    write_md,
)


def csv_schema(path: Path) -> dict:
    if not path.exists():
        return {"exists": False, "path": str(path), "row_count": 0, "columns": []}
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        columns = list(reader.fieldnames or [])
    return {"exists": True, "path": str(path), "row_count": count_csv_rows(path), "columns": columns}


def main() -> int:
    parser = argparse.ArgumentParser(description="Check v1.5 final QA package inputs.")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()

    ensure_dir(args.output_dir)
    summary = load_json(V1_4_SUMMARY)
    required_files = {
        "v1_4_summary_json": V1_4_SUMMARY,
        "v1_4_summary_report": V1_4_SUMMARY_REPORT,
        "v1_4_go_no_go_report": V1_4_GO_NO_GO_REPORT,
        "v1_4_task_trace": V1_4_TASK_TRACE,
        "v1_4_final_qa_sampling_frame": V1_4_QA_FRAME,
        "v1_4_dedup_trace": V1_4_DEDUP_TRACE,
        "v1_4_dedup_summary": V1_4_DEDUP_SUMMARY,
    }
    file_status = {name: {"path": str(path), "exists": path.exists()} for name, path in required_files.items()}
    bucket_status = resolve_bucket_aliases()
    missing_files = [info["path"] for info in file_status.values() if not info["exists"]]
    missing_buckets = [name for name, info in bucket_status.items() if info["exists"] != "true"]

    task_schema = csv_schema(V1_4_TASK_TRACE)
    qa_frame_schema = csv_schema(V1_4_QA_FRAME)
    checks = {
        "task_trace_rows_approx_201774": abs(int(task_schema.get("row_count", 0)) - 201774) <= 1,
        "dryrun_clean_candidate_approx_10550": int(summary.get("dryrun_clean_candidate_task_count", -1)) == 10550,
        "dangerous_error_count_is_zero": int(summary.get("dangerous_error_count", -1)) == 0,
        "candidate_level_join_complete": summary.get("candidate_level_join_completeness") == "complete",
        "can_prepare_final_qa_true": bool(summary.get("can_prepare_final_qa")) is True,
        "bucket_files_resolved": not missing_buckets,
    }
    fatal = bool(missing_files or missing_buckets or not all(checks.values()))
    payload = {
        "generated_time": now_text(),
        "required_files": file_status,
        "bucket_file_resolution": bucket_status,
        "missing_files": missing_files,
        "missing_buckets": missing_buckets,
        "task_trace_schema": task_schema,
        "qa_sampling_frame_schema": qa_frame_schema,
        "v1_4_summary_core": {
            "full_raw_task_rows": summary.get("full_raw_task_rows"),
            "full_raw_candidate_rows": summary.get("full_raw_candidate_rows"),
            "dryrun_clean_candidate_task_count": summary.get("dryrun_clean_candidate_task_count"),
            "dangerous_error_count": summary.get("dangerous_error_count"),
            "candidate_level_join_completeness": summary.get("candidate_level_join_completeness"),
            "can_prepare_final_qa": summary.get("can_prepare_final_qa"),
        },
        "checks": checks,
        "fatal_input_error": fatal,
        "no_final_clean_dataset_no_split_no_baseline_no_training": True,
    }
    write_json(args.output_dir / "input_schema_summary.json", payload)

    lines = [
        "# Final QA v1.5 Input Check Report",
        "",
        f"Generated time: {now_text()}",
        "",
        "This report checks readiness for final QA sampling only. It does not generate a final clean dataset, split, baseline, or training run.",
        "",
        "## Required File Status",
        "",
        "| name | exists | path |",
        "|---|---:|---|",
        *[f"| {name} | {info['exists']} | `{info['path']}` |" for name, info in file_status.items()],
        "",
        "## Bucket File Resolution",
        "",
        "The v1.5 prompt names some bucket files with `_task_level_v1_4.csv`; v1.4 actually produced equivalent bucket files without that suffix. Resolved aliases are recorded below.",
        "",
        "| expected key | exists | used alias | resolved path |",
        "|---|---:|---:|---|",
        *[f"| {name} | {info['exists']} | {info['used_alias']} | `{info['resolved']}` |" for name, info in bucket_status.items()],
        "",
        "## Core Checks",
        "",
        "| check | passed |",
        "|---|---:|",
        *[f"| {name} | {value} |" for name, value in checks.items()],
        "",
        f"Fatal input error: {fatal}",
    ]
    write_md(args.output_dir / "input_check_report.md", lines)

    if fatal:
        missing_lines = [f"- `{path}`" for path in missing_files] if missing_files else ["- None"]
        missing_bucket_lines = [f"- `{name}`" for name in missing_buckets] if missing_buckets else ["- None"]
        write_md(
            args.output_dir / "MISSING_INPUTS.md",
            [
                "# Missing Or Invalid v1.5 Inputs",
                "",
                f"Generated time: {now_text()}",
                "",
                "The v1.5 QA package was not generated because at least one required input/check failed.",
                "",
                "## Missing Files",
                "",
                *missing_lines,
                "",
                "## Missing Bucket Keys",
                "",
                *missing_bucket_lines,
            ],
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
