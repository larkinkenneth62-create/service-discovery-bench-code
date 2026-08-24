from __future__ import annotations

import argparse
import json
from pathlib import Path

from deepseek_semcap_v1_4d_common import (
    DOC_DIR,
    OUTPUT_DIR,
    REGRESSION_DIR,
    V15C_FAILURE_PATCH,
    V15D_REVIEW_SET,
    known_failure_task_ids,
    read_csv,
    write_csv,
    write_json,
    write_md,
)


def json_nonempty(value: str) -> bool:
    try:
        data = json.loads(value or "[]")
        return isinstance(data, list) and len(data) > 0
    except Exception:
        return bool(value)


def is_true(value: str) -> bool:
    return str(value or "").lower() == "true"


def main() -> int:
    parser = argparse.ArgumentParser(description="Regression check for DeepSeek-assisted v1.4d trace.")
    parser.add_argument("--trace", type=Path, default=OUTPUT_DIR / "deepseek_assisted_clean_task_trace_v1_4d.csv")
    parser.add_argument("--summary", type=Path, default=REGRESSION_DIR / "deepseek_regression_summary_v1_4d.json")
    parser.add_argument("--trace-output", type=Path, default=REGRESSION_DIR / "deepseek_regression_trace_v1_4d.csv")
    args = parser.parse_args()
    if not args.trace.exists():
        raise FileNotFoundError(f"Missing DeepSeek-assisted trace: {args.trace}")
    rows = read_csv(args.trace)
    clean = [row for row in rows if row.get("deepseek_assisted_decision_v1_4d") == "deepseek_assisted_clean_candidate"]
    failed_ids = known_failure_task_ids()
    trace_rows = []
    for row in clean:
        issues = []
        if row.get("task_id") in failed_ids:
            issues.append("known_failed_still_clean")
        if row.get("deepseek_capability_coverage_check") == "coverage_mismatch":
            issues.append("deepseek_coverage_mismatch_into_clean")
        if json_nonempty(row.get("deepseek_missing_requirements_json", "")):
            issues.append("deepseek_missing_requirements_into_clean")
        if json_nonempty(row.get("deepseek_extra_unrelated_gold_services_json", "")):
            issues.append("deepseek_extra_gold_into_clean")
        if is_true(row.get("deepseek_wrong_gold_set", "")):
            issues.append("deepseek_wrong_gold_set_into_clean")
        if is_true(row.get("deepseek_generic_search_overtrust", "")):
            issues.append("deepseek_generic_search_overtrust_into_clean")
        if row.get("deepseek_parse_status") != "ok":
            issues.append("deepseek_parse_failed_into_clean")
        if issues:
            trace_rows.append(
                {
                    "task_id": row.get("task_id", ""),
                    "source_group": row.get("source_group", ""),
                    "task_type": row.get("task_type", ""),
                    "issues": ";".join(issues),
                    "query_text": row.get("query_text", ""),
                    "deepseek_reason": row.get("deepseek_reason", ""),
                }
            )
    issue_counts = {
        "known_failed_still_clean": sum("known_failed_still_clean" in row["issues"] for row in trace_rows),
        "deepseek_coverage_mismatch_into_clean": sum("deepseek_coverage_mismatch_into_clean" in row["issues"] for row in trace_rows),
        "deepseek_missing_requirements_into_clean": sum("deepseek_missing_requirements_into_clean" in row["issues"] for row in trace_rows),
        "deepseek_extra_gold_into_clean": sum("deepseek_extra_gold_into_clean" in row["issues"] for row in trace_rows),
        "deepseek_wrong_gold_set_into_clean": sum("deepseek_wrong_gold_set_into_clean" in row["issues"] for row in trace_rows),
        "deepseek_generic_search_overtrust_into_clean": sum("deepseek_generic_search_overtrust_into_clean" in row["issues"] for row in trace_rows),
        "deepseek_parse_failed_into_clean": sum("deepseek_parse_failed_into_clean" in row["issues"] for row in trace_rows),
    }
    passed = all(value == 0 for value in issue_counts.values())
    summary = {
        "input_trace": str(args.trace),
        "deepseek_assisted_clean_candidate_count": len(clean),
        "v1_5c_failure_patch": str(V15C_FAILURE_PATCH),
        "v1_5d_review_set": str(V15D_REVIEW_SET),
        "known_failure_task_id_count": len(failed_ids),
        **issue_counts,
        "regression_passed": passed,
    }
    write_csv(args.trace_output, trace_rows, list(trace_rows[0].keys()) if trace_rows else ["task_id", "source_group", "task_type", "issues", "query_text", "deepseek_reason"])
    write_json(args.summary, summary)
    write_md(
        DOC_DIR / "deepseek_semcap_regression_report_v1_4d.md",
        [
            "# DeepSeek SemCap Regression Report v1.4d",
            "",
            f"Input trace: `{args.trace}`",
            f"DeepSeek-assisted clean candidates: {len(clean)}",
            "",
            "## Issue Counts",
            "",
            *[f"- {key}: {value}" for key, value in issue_counts.items()],
            "",
            f"Regression passed: {passed}",
            "",
            "No final clean data, split, baseline, or training is generated here.",
        ],
    )
    print(f"known_failed_still_clean: {issue_counts['known_failed_still_clean']}")
    print(f"deepseek_parse_failed_into_clean: {issue_counts['deepseek_parse_failed_into_clean']}")
    print(f"regression_passed: {passed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
