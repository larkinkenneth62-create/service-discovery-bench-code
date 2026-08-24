from __future__ import annotations

import argparse
import csv
from pathlib import Path

from final_qa_v1_5d_common import (
    DOC_DIR,
    OUTPUT_DIR,
    V14B_CANDIDATE_TRACE,
    V14B_CLEAN_BUCKET,
    V14B_CLEAN_HIGH_CONF_BUCKET,
    V14B_DANGEROUS_SUMMARY,
    V14B_DEDUP_SUMMARY,
    V14B_DEDUP_TRACE,
    V14B_GO_NO_GO_REPORT,
    V14B_QA_FRAME,
    V14B_QA_PLAN,
    V14B_REMOVED_BUCKET,
    V14B_SERVICE_LEAK_BUCKET,
    V14B_SUMMARY,
    V14B_TASK_TRACE,
    V14B_UNCERTAIN_BUCKET,
    V15C_FAILURE_PATCH,
    V15C_FAILURE_TAXONOMY_DOC,
    V15C_POLICY_PLAN_DOC,
    V15C_SEMCAP_RULE_DOC,
    V15_PROTOCOL_DOC,
    count_csv_rows,
    csv_schema,
    ensure_dir,
    load_json,
    now_text,
    read_csv,
    write_json,
    write_md,
)


def scan_clean_task_ids(path: Path) -> set[str]:
    ids: set[str] = set()
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            task_id = row.get("task_id", "")
            if task_id:
                ids.add(task_id)
    return ids


def main() -> int:
    parser = argparse.ArgumentParser(description="Check v1.5d impacted clean-candidate QA inputs.")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()
    ensure_dir(args.output_dir)

    required_files = {
        "v1_4b_summary_json": V14B_SUMMARY,
        "v1_4b_full_task_trace": V14B_TASK_TRACE,
        "v1_4b_full_candidate_trace": V14B_CANDIDATE_TRACE,
        "v1_4b_dangerous_summary": V14B_DANGEROUS_SUMMARY,
        "v1_4b_go_no_go_report": V14B_GO_NO_GO_REPORT,
        "v1_4b_clean_candidate_bucket": V14B_CLEAN_BUCKET,
        "v1_4b_clean_candidate_high_conf_bucket": V14B_CLEAN_HIGH_CONF_BUCKET,
        "v1_4b_removed_bucket": V14B_REMOVED_BUCKET,
        "v1_4b_uncertain_bucket": V14B_UNCERTAIN_BUCKET,
        "v1_4b_service_leak_only_bucket": V14B_SERVICE_LEAK_BUCKET,
        "v1_4b_dedup_trace": V14B_DEDUP_TRACE,
        "v1_4b_dedup_summary": V14B_DEDUP_SUMMARY,
        "v1_4b_impacted_qa_frame": V14B_QA_FRAME,
        "v1_4b_impacted_qa_plan": V14B_QA_PLAN,
        "v1_5c_failure_patch": V15C_FAILURE_PATCH,
        "v1_5c_failure_taxonomy_doc": V15C_FAILURE_TAXONOMY_DOC,
        "v1_5c_semcap_rule_doc": V15C_SEMCAP_RULE_DOC,
        "v1_5c_policy_plan_doc": V15C_POLICY_PLAN_DOC,
        "v1_5_original_protocol": V15_PROTOCOL_DOC,
    }
    file_status = {name: {"path": str(path), "exists": path.exists()} for name, path in required_files.items()}
    missing_files = [info["path"] for info in file_status.values() if not info["exists"]]

    summary = load_json(V14B_SUMMARY)
    dangerous = load_json(V14B_DANGEROUS_SUMMARY)
    dedup_summary = load_json(V14B_DEDUP_SUMMARY)
    failure_rows = read_csv(V15C_FAILURE_PATCH) if V15C_FAILURE_PATCH.exists() else []
    failure_ids = {row.get("task_id", "") for row in failure_rows if row.get("task_id")}
    clean_ids = scan_clean_task_ids(V14B_CLEAN_BUCKET) if V14B_CLEAN_BUCKET.exists() else set()
    failures_still_clean = sorted(failure_ids & clean_ids)

    checks = {
        "v1_4b_summary_exists": V14B_SUMMARY.exists(),
        "v1_4b_task_trace_exists": V14B_TASK_TRACE.exists(),
        "v1_4b_clean_candidate_bucket_exists": V14B_CLEAN_BUCKET.exists(),
        "v1_4b_dangerous_summary_exists": V14B_DANGEROUS_SUMMARY.exists(),
        "v1_5c_failure_patch_exists": V15C_FAILURE_PATCH.exists(),
        "dedup_precheck_exists": V14B_DEDUP_TRACE.exists() and V14B_DEDUP_SUMMARY.exists(),
        "dangerous_error_count_is_zero": int(summary.get("dangerous_error_count", dangerous.get("dangerous_error_count", -1))) == 0,
        "clean_candidate_count_around_4862": abs(int(summary.get("v1_4b_clean_candidate_count", -1)) - 4862) <= 50,
        "v1_5c_failure_patch_has_32_rows": len(failure_rows) == 32,
        "all_32_previous_failures_not_clean": len(failures_still_clean) == 0 and len(failure_ids) == 32,
        "candidate_level_join_complete": summary.get("candidate_level_join_completeness") == "complete",
        "api_level_not_certified_by_current_qa": True,
    }
    fatal = bool(missing_files) or not checks["dangerous_error_count_is_zero"] or not checks["all_32_previous_failures_not_clean"]

    payload = {
        "generated_time": now_text(),
        "required_files": file_status,
        "missing_files": missing_files,
        "schemas": {
            "v1_4b_task_trace": csv_schema(V14B_TASK_TRACE),
            "v1_4b_clean_candidate_bucket": csv_schema(V14B_CLEAN_BUCKET),
            "v1_4b_dedup_trace": csv_schema(V14B_DEDUP_TRACE),
            "v1_4b_qa_frame": csv_schema(V14B_QA_FRAME),
            "v1_5c_failure_patch": csv_schema(V15C_FAILURE_PATCH),
        },
        "v1_4b_summary_core": {
            "full_task_rows": summary.get("full_task_rows"),
            "full_candidate_rows": summary.get("full_candidate_rows"),
            "v1_4b_clean_candidate_count": summary.get("v1_4b_clean_candidate_count"),
            "dangerous_error_count": summary.get("dangerous_error_count"),
            "candidate_level_join_completeness": summary.get("candidate_level_join_completeness"),
            "go_no_go_decision_v1_4b": summary.get("go_no_go_decision_v1_4b"),
            "can_generate_final_clean_dataset_now": summary.get("can_generate_final_clean_dataset_now"),
            "can_create_split_now": summary.get("can_create_split_now"),
            "can_run_baseline_now": summary.get("can_run_baseline_now"),
            "can_train_model_now": summary.get("can_train_model_now"),
        },
        "dedup_summary": dedup_summary,
        "failure_patch_row_count": len(failure_rows),
        "v1_5c_failure_ids_still_clean": failures_still_clean,
        "checks": checks,
        "fatal_input_error": fatal,
        "api_level_current_qa_covered": False,
        "no_final_clean_dataset_no_split_no_baseline_no_training": True,
    }
    write_json(args.output_dir / "input_schema_summary.json", payload)

    lines = [
        "# Final QA v1.5d Input Check Report",
        "",
        f"Generated time: {now_text()}",
        f"Output directory: `{args.output_dir}`",
        "",
        "This check prepares impacted clean-candidate QA only. It does not generate a final clean dataset, split, baseline, or model training run.",
        "",
        "## Required Inputs",
        "",
        "| name | exists | path |",
        "|---|---:|---|",
        *[f"| {name} | {info['exists']} | `{info['path']}` |" for name, info in file_status.items()],
        "",
        "## Core Checks",
        "",
        "| check | passed |",
        "|---|---:|",
        *[f"| {name} | {value} |" for name, value in checks.items()],
        "",
        "## Important Counts",
        "",
        f"- v1.4b clean candidate count from summary: {summary.get('v1_4b_clean_candidate_count')}",
        f"- v1.4b clean bucket rows: {count_csv_rows(V14B_CLEAN_BUCKET)}",
        f"- v1.5c failure patch rows: {len(failure_rows)}",
        f"- v1.5c failure ids still clean in v1.4b: {len(failures_still_clean)}",
        f"- dedup group count: {dedup_summary.get('duplicate_group_count')}",
        "",
        f"Fatal input error: {fatal}",
        "",
        "API-level final readiness is not covered by this v1.5d package.",
    ]
    write_md(args.output_dir / "input_check_report.md", lines)

    if fatal:
        missing_lines = [f"- `{path}`" for path in missing_files] if missing_files else ["- None"]
        still_clean_lines = [f"- `{task_id}`" for task_id in failures_still_clean] if failures_still_clean else ["- None"]
        write_md(
            args.output_dir / "MISSING_INPUTS.md",
            [
                "# Missing Or Invalid v1.5d Inputs",
                "",
                f"Generated time: {now_text()}",
                "",
                "The v1.5d QA package was not generated because at least one required input/check failed.",
                "",
                "## Missing Files",
                "",
                *missing_lines,
                "",
                "## Previous Failure IDs Still Clean",
                "",
                *still_clean_lines,
            ],
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
