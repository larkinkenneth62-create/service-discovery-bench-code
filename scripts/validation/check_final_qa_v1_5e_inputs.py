from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path

from final_qa_v1_5e_common import (
    DOC_DIR,
    MANUAL_V42_DOC,
    OUTPUT_DIR,
    POLICY_V14C_DOC,
    SEMCAP_V12_DOC,
    SEMCAP_V13_DOC,
    V14C_CANDIDATE_TRACE,
    V14C_GO_NO_GO,
    V14C_SUMMARY,
    V14C_TASK_TRACE,
    V15D_ANALYSIS_REPORT,
    V15D_GO_NO_GO_FOR_V16,
    V15D_MERGED,
    V15D_REVIEW_SET,
    V15D_TAXONOMY,
    csv_schema,
    ensure_dir,
    load_json,
    load_v15d_failed_task_ids,
    now_text,
    write_json,
    write_md,
)


def scan_trace(path: Path, failed_ids: set[str]) -> tuple[set[str], Counter, Counter, Counter, int]:
    clean_ids: set[str] = set()
    source_group = Counter()
    task_type = Counter()
    prediction_level = Counter()
    rows = 0
    if not path.exists():
        return clean_ids, source_group, task_type, prediction_level, rows
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            rows += 1
            if row.get("dryrun_decision_v1_4c") == "dryrun_clean_candidate":
                task_id = row.get("task_id", "")
                if task_id:
                    clean_ids.add(task_id)
                source_group[row.get("source_group", "")] += 1
                task_type[row.get("task_type", "")] += 1
                prediction_level[row.get("prediction_level", "")] += 1
    return clean_ids, source_group, task_type, prediction_level, rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Check v1.5e QA package inputs.")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()
    ensure_dir(args.output_dir)

    required = {
        "v1_4c_task_trace": V14C_TASK_TRACE,
        "v1_4c_summary": V14C_SUMMARY,
        "v1_4c_go_no_go_report": V14C_GO_NO_GO,
        "v1_5d_failure_taxonomy": V15D_TAXONOMY,
        "v1_5d_review_set": V15D_REVIEW_SET,
        "semcap_v1_3_tightening_rules": SEMCAP_V13_DOC,
        "policy_v1_4c_tightening_plan": POLICY_V14C_DOC,
        "semcap_v1_2_tightening_rules": SEMCAP_V12_DOC,
        "manual_audit_rule_v4_2": MANUAL_V42_DOC,
    }
    optional = {
        "v1_4c_candidate_trace": V14C_CANDIDATE_TRACE,
        "v1_5d_merged": V15D_MERGED,
        "v1_5d_analysis_report": V15D_ANALYSIS_REPORT,
        "v1_5d_go_no_go_for_v1_6": V15D_GO_NO_GO_FOR_V16,
    }
    file_status = {name: {"path": str(path), "exists": path.exists()} for name, path in required.items()}
    optional_status = {name: {"path": str(path), "exists": path.exists()} for name, path in optional.items()}
    missing = [info["path"] for info in file_status.values() if not info["exists"]]
    summary = load_json(V14C_SUMMARY)
    failed_ids = load_v15d_failed_task_ids()
    clean_ids, source_group, task_type, prediction_level, trace_rows = scan_trace(V14C_TASK_TRACE, failed_ids)
    still_clean = sorted(failed_ids & clean_ids)

    checks = {
        "v1_4c_task_trace_exists": V14C_TASK_TRACE.exists(),
        "v1_4c_summary_exists": V14C_SUMMARY.exists(),
        "v1_4c_go_no_go_report_exists": V14C_GO_NO_GO.exists(),
        "v1_4c_go_no_go_is_v1_5e": summary.get("go_no_go_decision_v1_4c") == "GO_TO_V1_5E_SMALL_CLEAN_CANDIDATE_QA",
        "v1_4c_clean_candidate_count_around_2168": abs(int(summary.get("v1_4c_clean_candidate_count", -1)) - 2168) <= 50,
        "dangerous_error_count_is_zero": int(summary.get("dangerous_error_count", -1)) == 0,
        "v1_5d_failure_taxonomy_exists": V15D_TAXONOMY.exists(),
        "v1_5d_failed_task_ids_parseable": len(failed_ids) == 32,
        "v1_5d_failed_task_ids_not_clean": len(still_clean) == 0 and len(failed_ids) == 32,
        "candidate_level_join_complete_if_available": summary.get("candidate_level_join_completeness", "not_recorded") in {"complete", "not_recorded", ""},
    }
    fatal = bool(missing) or not checks["v1_4c_go_no_go_is_v1_5e"] or not checks["dangerous_error_count_is_zero"] or bool(still_clean)
    payload = {
        "generated_time": now_text(),
        "required_files": file_status,
        "optional_files": optional_status,
        "missing_required_files": missing,
        "schemas": {
            "v1_4c_task_trace": csv_schema(V14C_TASK_TRACE),
            "v1_4c_candidate_trace": csv_schema(V14C_CANDIDATE_TRACE),
            "v1_5d_review_set": csv_schema(V15D_REVIEW_SET),
        },
        "v1_4c_summary_core": summary,
        "trace_rows": trace_rows,
        "v1_4c_clean_candidate_count_from_trace": len(clean_ids),
        "v1_5d_failed_task_id_count": len(failed_ids),
        "v1_5d_failed_task_ids_still_clean": still_clean,
        "clean_candidate_source_group_distribution": dict(source_group),
        "clean_candidate_task_type_distribution": dict(task_type),
        "clean_candidate_prediction_level_distribution": dict(prediction_level),
        "api_level_final_readiness_authorized": False,
        "checks": checks,
        "fatal_input_error": fatal,
        "no_final_clean_dataset_no_split_no_baseline_no_training": True,
    }
    write_json(args.output_dir / "input_schema_summary_v1_5e.json", payload)
    lines = [
        "# Final QA v1.5e Input Check Report",
        "",
        f"Generated time: {now_text()}",
        f"Output directory: `{args.output_dir}`",
        "",
        "This check prepares a 100-row clean-candidate QA package only. It does not generate final clean data, split, baseline, or training artifacts.",
        "",
        "## Required Inputs",
        "",
        "| name | exists | path |",
        "|---|---:|---|",
        *[f"| {name} | {info['exists']} | `{info['path']}` |" for name, info in file_status.items()],
        "",
        "## Optional Inputs",
        "",
        "| name | exists | path |",
        "|---|---:|---|",
        *[f"| {name} | {info['exists']} | `{info['path']}` |" for name, info in optional_status.items()],
        "",
        "## Core Checks",
        "",
        "| check | passed |",
        "|---|---:|",
        *[f"| {name} | {value} |" for name, value in checks.items()],
        "",
        "## Clean Candidate Pool",
        "",
        f"- v1.4c clean candidate count from summary: {summary.get('v1_4c_clean_candidate_count')}",
        f"- v1.4c clean candidate count from trace: {len(clean_ids)}",
        f"- v1.5d failed task ids still clean: {len(still_clean)}",
        f"- prediction level distribution: {dict(prediction_level)}",
        "",
        "API-level final readiness is not authorized by this package.",
        "",
        f"Fatal input error: {fatal}",
    ]
    write_md(args.output_dir / "input_check_report_v1_5e.md", lines)
    if fatal:
        write_md(
            args.output_dir / "MISSING_INPUTS.md",
            [
                "# Missing Or Invalid v1.5e Inputs",
                "",
                f"Generated time: {now_text()}",
                "",
                "The v1.5e QA package was not generated because at least one required input/check failed.",
                "",
                "## Missing Required Files",
                "",
                *([f"- `{path}`" for path in missing] if missing else ["- None"]),
                "",
                "## v1.5d Failed Task IDs Still Clean",
                "",
                *([f"- `{task_id}`" for task_id in still_clean] if still_clean else ["- None"]),
            ],
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
