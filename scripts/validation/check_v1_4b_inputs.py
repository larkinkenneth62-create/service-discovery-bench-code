from __future__ import annotations

import argparse
import csv
from pathlib import Path

from full_clean_v1_4b_common import (
    CALIBRATION_180,
    DOC_DIR,
    OUTPUT_DIR,
    PREDICTIONS_180,
    RAW_CANDIDATE,
    RAW_TASK,
    SEMCAP_V1_EVAL,
    SEMCAP_V1_RULE,
    SEMCAP_V1_SCRIPT,
    V12_RULE_DOC,
    V14_DETECTOR_TRACE,
    V14_SUMMARY,
    V14_TASK_TRACE,
    V15C_GO_NO_GO,
    V15C_PATCH,
    V15C_PLAN,
    V15C_SUMMARY,
    V15C_TAXONOMY,
    V42_POLICY,
    count_csv_rows,
    ensure_dir,
    load_json,
    now_text,
    write_json,
    write_md,
)


def csv_columns(path: Path) -> list[str]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f).fieldnames or [])


def main() -> int:
    parser = argparse.ArgumentParser(description="Check v1.4b inputs.")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()
    ensure_dir(args.output_dir)

    required = {
        "v1_5c_taxonomy": V15C_TAXONOMY,
        "v1_5c_failure_patch": V15C_PATCH,
        "semcap_v1_2_rules": V12_RULE_DOC,
        "policy_tightening_plan": V15C_PLAN,
        "v1_5c_summary": V15C_SUMMARY,
        "v1_5c_go_no_go": V15C_GO_NO_GO,
        "v1_4_task_trace": V14_TASK_TRACE,
        "v1_4_summary": V14_SUMMARY,
        "v1_3_raw_task": RAW_TASK,
        "v1_3_raw_candidate": RAW_CANDIDATE,
        "v4_2_policy": V42_POLICY,
        "semcap_v1_1_script": SEMCAP_V1_SCRIPT,
        "semcap_v1_1_rule_doc": SEMCAP_V1_RULE,
        "semcap_v1_1_eval_report": SEMCAP_V1_EVAL,
        "combined_calibration_180": CALIBRATION_180,
    }
    optional = {
        "v1_4_detector_trace": V14_DETECTOR_TRACE,
        "semcap_predictions_combined_180_v1": PREDICTIONS_180,
    }
    status = {name: {"path": str(path), "exists": path.exists()} for name, path in required.items()}
    optional_status = {name: {"path": str(path), "exists": path.exists()} for name, path in optional.items()}
    missing = [str(path) for path in required.values() if not path.exists()]
    patch_rows = count_csv_rows(V15C_PATCH)
    patch_columns = csv_columns(V15C_PATCH)
    patch_nonempty = True
    if V15C_PATCH.exists():
        with V15C_PATCH.open("r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                if not row.get("qa_item_id") or not row.get("task_id"):
                    patch_nonempty = False
                    break
    v14_summary = load_json(V14_SUMMARY)
    checks = {
        "failure_patch_has_32_rows": patch_rows == 32,
        "failure_patch_qa_item_id_task_id_nonempty": patch_nonempty,
        "v1_4_dangerous_error_count_is_0": int(v14_summary.get("dangerous_error_count", -1)) == 0,
        "v1_4_candidate_join_complete": v14_summary.get("candidate_level_join_completeness") == "complete",
        "v1_4_detector_trace_exists": V14_DETECTOR_TRACE.exists(),
    }
    fatal = bool(missing or not patch_nonempty)
    payload = {
        "generated_time": now_text(),
        "required_inputs": status,
        "optional_inputs": optional_status,
        "missing_required_inputs": missing,
        "failure_patch_rows": patch_rows,
        "failure_patch_columns": patch_columns,
        "checks": checks,
        "fatal_input_error": fatal,
        "detector_trace_needs_rerun": not V14_DETECTOR_TRACE.exists(),
        "no_final_clean_dataset_no_split_no_baseline_no_training": True,
    }
    write_json(args.output_dir / "input_schema_summary.json", payload)
    lines = [
        "# v1.4b Input Check Report",
        "",
        f"Generated time: {now_text()}",
        "",
        "This check prepares SemCap v1.2 tightening + full dry-run rerun. It does not generate a final clean dataset.",
        "",
        "## Required Inputs",
        "",
        "| input | exists | path |",
        "|---|---:|---|",
        *[f"| {name} | {info['exists']} | `{info['path']}` |" for name, info in status.items()],
        "",
        "## Optional Inputs",
        "",
        "| input | exists | path |",
        "|---|---:|---|",
        *[f"| {name} | {info['exists']} | `{info['path']}` |" for name, info in optional_status.items()],
        "",
        "## Checks",
        "",
        "| check | passed |",
        "|---|---:|",
        *[f"| {name} | {passed} |" for name, passed in checks.items()],
        "",
        f"Fatal input error: {fatal}",
    ]
    write_md(args.output_dir / "input_check_report.md", lines)
    if fatal:
        write_md(args.output_dir / "MISSING_INPUTS.md", ["# Missing Inputs", "", *[f"- `{path}`" for path in missing]])
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
