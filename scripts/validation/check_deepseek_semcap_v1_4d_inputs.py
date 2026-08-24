from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from deepseek_semcap_v1_4d_common import (
    ALLOWED_MODELS,
    CALIBRATION_180,
    DOC_DIR,
    GO_NO_GO_DOC,
    MANUAL_V42_DOC,
    OUTPUT_DIR,
    POLICY_V14C_DOC,
    SEMCAP_PRED_180,
    SEMCAP_V12_DOC,
    SEMCAP_V13_DOC,
    V14C_GO_NO_GO,
    V14C_SUMMARY,
    V14C_TASK_TRACE,
    V15C_FAILURE_PATCH,
    V15C_FAILURE_TAXONOMY,
    V15D_ANALYSIS_REPORT,
    V15D_FAILURE_TAXONOMY,
    V15D_MERGED,
    V15D_REVIEW_SET,
    clean_candidate_rows,
    distribution,
    ensure_dir,
    env_config,
    known_failure_task_ids,
    load_json,
    now_text,
    read_csv,
    table_lines,
    write_go_no_go_report,
    write_json,
    write_md,
)


def file_status(paths: dict[str, Path]) -> dict[str, dict[str, object]]:
    return {name: {"path": str(path), "exists": path.exists()} for name, path in paths.items()}


def main() -> int:
    parser = argparse.ArgumentParser(description="Check v1.4d DeepSeek SemCap Judge inputs.")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()
    ensure_dir(args.output_dir)

    required = {
        "v1_4c_task_trace": V14C_TASK_TRACE,
        "v1_4c_summary": V14C_SUMMARY,
        "v1_4c_go_no_go_report": V14C_GO_NO_GO,
        "v1_5c_failure_patch": V15C_FAILURE_PATCH,
        "v1_5c_failure_taxonomy": V15C_FAILURE_TAXONOMY,
        "v1_5d_failure_taxonomy": V15D_FAILURE_TAXONOMY,
        "v1_5d_review_set": V15D_REVIEW_SET,
        "combined_calibration_180": CALIBRATION_180,
        "combined_semcap_predictions_180": SEMCAP_PRED_180,
        "manual_audit_rule_v4_2": MANUAL_V42_DOC,
        "semcap_v1_3_tightening_rules": SEMCAP_V13_DOC,
        "policy_v1_4c_tightening_plan": POLICY_V14C_DOC,
        "semcap_v1_2_tightening_rules": SEMCAP_V12_DOC,
    }
    optional = {
        "v1_5d_merged": V15D_MERGED,
        "v1_5d_analysis_report": V15D_ANALYSIS_REPORT,
    }
    required_status = file_status(required)
    optional_status = file_status(optional)
    missing_required = [str(path) for path in required.values() if not path.exists()]

    summary = load_json(V14C_SUMMARY)
    clean_rows = clean_candidate_rows() if V14C_TASK_TRACE.exists() else []
    clean_ids = {row.get("task_id", "") for row in clean_rows if row.get("task_id")}
    known_failed = known_failure_task_ids()
    known_failed_still_clean = sorted(clean_ids & known_failed)
    config = env_config()

    checks = {
        "v1_4c_task_trace_exists": V14C_TASK_TRACE.exists(),
        "v1_4c_summary_exists": V14C_SUMMARY.exists(),
        "v1_4c_go_no_go_report_exists": V14C_GO_NO_GO.exists(),
        "v1_4c_go_no_go_is_v1_5e": summary.get("go_no_go_decision_v1_4c") == "GO_TO_V1_5E_SMALL_CLEAN_CANDIDATE_QA",
        "v1_4c_clean_candidate_count_around_2168": abs(len(clean_rows) - 2168) <= 50,
        "dangerous_error_count_is_zero": int(summary.get("dangerous_error_count", -1)) == 0,
        "v1_5c_failure_patch_exists": V15C_FAILURE_PATCH.exists(),
        "v1_5d_failure_taxonomy_exists": V15D_FAILURE_TAXONOMY.exists(),
        "combined_calibration_180_exists": CALIBRATION_180.exists(),
        "deepseek_api_key_exists": config["api_key_exists"],
        "deepseek_model_allowed": config["model"] in ALLOWED_MODELS,
        "known_failure_task_ids_not_clean": len(known_failed_still_clean) == 0,
    }
    fatal = bool(missing_required) or not checks["v1_4c_go_no_go_is_v1_5e"] or not checks["dangerous_error_count_is_zero"] or bool(known_failed_still_clean) or not checks["deepseek_model_allowed"]

    payload = {
        "generated_time": now_text(),
        "required_files": required_status,
        "optional_files": optional_status,
        "missing_required_files": missing_required,
        "environment": {
            "deepseek_api_key_exists": config["api_key_exists"],
            "deepseek_api_base_url": config["base_url"],
            "deepseek_api_model": config["model"],
            "deepseek_model_allowed": config["model_allowed"],
            "deepseek_structured_mode": config["structured_mode"],
            "deepseek_thinking": config["thinking"],
            "allow_deepseek_full_run": config["allow_full_run"],
        },
        "v1_4c_summary_core": summary,
        "v1_4c_clean_candidate_count_from_trace": len(clean_rows),
        "v1_4c_clean_candidate_source_group_distribution": distribution(clean_rows, "source_group"),
        "v1_4c_clean_candidate_task_type_distribution": distribution(clean_rows, "task_type"),
        "v1_4c_clean_candidate_prediction_level_distribution": distribution(clean_rows, "prediction_level"),
        "known_failure_task_id_count": len(known_failed),
        "known_failure_task_ids_still_clean_in_v1_4c": known_failed_still_clean,
        "checks": checks,
        "fatal_input_error": fatal,
        "api_call_allowed_now": bool(config["api_key_exists"] and not fatal),
        "no_final_clean_dataset_no_split_no_baseline_no_training": True,
    }
    write_json(args.output_dir / "input_schema_summary.json", payload)

    lines = [
        "# DeepSeek SemCap v1.4d Input Check Report",
        "",
        f"Generated time: {now_text()}",
        f"Output directory: `{args.output_dir}`",
        "This check prepares DeepSeek SemCap judging only. It does not generate final clean data, split, baseline, or training.",
        "",
        "## Required Inputs",
        "",
        "| name | exists | path |",
        "|---|---:|---|",
        *[f"| {name} | {info['exists']} | `{info['path']}` |" for name, info in required_status.items()],
        "",
        "## Optional Inputs",
        "",
        "| name | exists | path |",
        "|---|---:|---|",
        *[f"| {name} | {info['exists']} | `{info['path']}` |" for name, info in optional_status.items()],
        "",
        "## Environment",
        "",
        f"- DEEPSEEK_API_KEY exists: {config['api_key_exists']}",
        f"- DEEPSEEK_API_BASE_URL: `{config['base_url']}`",
        f"- DEEPSEEK_API_MODEL: `{config['model']}`",
        f"- model allowed: {config['model_allowed']}",
        f"- ALLOW_DEEPSEEK_FULL_RUN: {config['allow_full_run']}",
        "",
        "The API key value is never printed.",
        "",
        "## Core Checks",
        "",
        "| check | passed |",
        "|---|---:|",
        *[f"| {name} | {value} |" for name, value in checks.items()],
        "",
        "## v1.4c Clean Candidate Pool",
        "",
        f"- clean candidate count from trace: {len(clean_rows)}",
        f"- known failure task ids: {len(known_failed)}",
        f"- known failure task ids still clean: {len(known_failed_still_clean)}",
        "",
        "### Source Group Distribution",
        "",
        *table_lines(Counter(distribution(clean_rows, "source_group"))),
        "",
        f"Fatal input error: {fatal}",
    ]
    write_md(args.output_dir / "input_check_report.md", lines)

    if missing_required or fatal:
        write_md(
            args.output_dir / "MISSING_INPUTS.md",
            [
                "# Missing Or Invalid v1.4d Inputs",
                "",
                f"Generated time: {now_text()}",
                "",
                "The v1.4d DeepSeek preparation cannot safely continue until these issues are resolved.",
                "",
                "## Missing Required Files",
                "",
                *([f"- `{path}`" for path in missing_required] if missing_required else ["- None"]),
                "",
                "## Known Failure Task IDs Still Clean",
                "",
                *([f"- `{task_id}`" for task_id in known_failed_still_clean] if known_failed_still_clean else ["- None"]),
            ],
        )
    if not config["api_key_exists"]:
        write_md(
            args.output_dir / "WAITING_FOR_DEEPSEEK_API_KEY.md",
            [
                "# Waiting For DeepSeek API Key",
                "",
                f"Generated time: {now_text()}",
                "",
                "`DEEPSEEK_API_KEY` is not set in the current environment.",
                "Request JSONL and prompt/schema artifacts can still be generated, but no DeepSeek API call will be made.",
                "",
                "Do not paste the key into source files or reports. Set it as an environment variable before running the API runner.",
            ],
        )

    decision = "READY_FOR_SAMPLE20_API_CALL" if config["api_key_exists"] and not fatal else "WAITING_FOR_DEEPSEEK_API_KEY_OR_INPUT_FIX"
    next_step = "run sample20 DeepSeek judge" if decision == "READY_FOR_SAMPLE20_API_CALL" else "set DEEPSEEK_API_KEY, or fix missing/invalid inputs, then run sample20"
    write_go_no_go_report(
        {
            "go_no_go_decision": decision,
            "can_accept_deepseek_sample20": False,
            "can_accept_deepseek_calibration": False,
            "can_accept_deepseek_full_predictions": False,
            "can_prepare_deepseek_assisted_final_qa": False,
            "recommended_next_step": next_step,
        }
    )
    print(f"v1.4c clean candidate count: {len(clean_rows)}")
    print(f"known failure still clean count: {len(known_failed_still_clean)}")
    print(f"DEEPSEEK_API_KEY exists: {config['api_key_exists']}")
    print(f"model allowed: {config['model_allowed']} ({config['model']})")
    print(f"fatal input error: {fatal}")
    print(f"go/no-go report: {GO_NO_GO_DOC}")
    return 1 if fatal else 0


if __name__ == "__main__":
    raise SystemExit(main())
