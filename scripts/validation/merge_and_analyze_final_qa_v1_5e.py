from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from final_qa_v1_5e_common import (
    ANALYSIS_DIR,
    DOC_DIR,
    OUTPUT_DIR,
    QA_FIELD_OPTIONS,
    QA_HUMAN_FIELDS,
    V14C_SUMMARY,
    V14C_TASK_TRACE,
    archive_v1_5e,
    distribution,
    load_json,
    load_v15d_failed_task_ids,
    now_text,
    read_csv,
    table_lines,
    write_csv,
    write_json,
    write_md,
)


def invalid_values(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    invalid: list[dict[str, str]] = []
    for row in rows:
        for field, options in QA_FIELD_OPTIONS.items():
            value = row.get(field, "")
            if value not in options:
                invalid.append({"qa_item_id": row.get("qa_item_id", ""), "field": field, "value": value})
    return invalid


def cross_tab(rows: list[dict[str, str]], a: str, b: str) -> dict[str, dict[str, int]]:
    out: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        out[row.get(a, "") or "<blank>"][row.get(b, "") or "<blank>"] += 1
    return {key: dict(counter) for key, counter in out.items()}


def scan_v14c_failed_still_clean() -> tuple[int, list[str]]:
    failed_ids = load_v15d_failed_task_ids()
    if not failed_ids or not V14C_TASK_TRACE.exists():
        return len(failed_ids), []
    still_clean: list[str] = []
    with V14C_TASK_TRACE.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            task_id = row.get("task_id", "")
            if task_id in failed_ids and row.get("dryrun_decision_v1_4c") == "dryrun_clean_candidate":
                still_clean.append(task_id)
    return len(failed_ids), sorted(still_clean)


def is_fail(row: dict[str, str]) -> bool:
    return row.get("qa_final_decision") == "fail"


def count_fail_by_error_and_severity(rows: list[dict[str, str]], error_type: str, severity: str) -> int:
    return sum(
        1
        for row in rows
        if is_fail(row) and row.get("qa_error_type") == error_type and row.get("qa_severity") == severity
    )


def analyze_reviewed(rows: list[dict[str, str]]) -> dict[str, Any]:
    current = [row for row in rows if row.get("qa_bucket") == "current_clean_candidate_audit"]
    completed = sum(1 for row in rows if row.get("qa_final_decision"))
    invalid = invalid_values(rows)
    failed_total, failed_still_clean = scan_v14c_failed_still_clean()

    current_critical = [row for row in current if is_fail(row) and row.get("qa_severity") == "critical"]
    current_major = [row for row in current if is_fail(row) and row.get("qa_severity") == "major"]
    current_major_critical = current_major + current_critical

    wrong_gold_set_critical = count_fail_by_error_and_severity(current, "wrong_gold_set", "critical")
    capability_mismatch_critical = count_fail_by_error_and_severity(current, "capability_mismatch", "critical")
    duplicate_critical = count_fail_by_error_and_severity(current, "duplicate_issue", "critical")

    def rate(num: int, den: int) -> float:
        return round(num / den, 4) if den else 0.0

    major_plus_critical_rate = rate(len(current_major_critical), len(current))
    thresholds_pass = (
        completed == len(rows)
        and not invalid
        and len(failed_still_clean) == 0
        and len(current_critical) == 0
        and len(current_major_critical) <= 5
        and major_plus_critical_rate <= 0.05
        and wrong_gold_set_critical == 0
        and capability_mismatch_critical == 0
        and duplicate_critical == 0
    )

    return {
        "generated_time": now_text(),
        "row_count": len(rows),
        "completed_review_count": completed,
        "all_reviewed": completed == len(rows),
        "invalid_values": invalid,
        "overall_qa_final_decision_distribution": distribution(rows, "qa_final_decision"),
        "qa_subbucket_x_final_decision": cross_tab(rows, "qa_subbucket", "qa_final_decision"),
        "qa_error_type_distribution": distribution(rows, "qa_error_type"),
        "qa_severity_distribution": distribution(rows, "qa_severity"),
        "v1_5d_previous_failed_regression": {
            "failed_task_id_count": failed_total,
            "still_clean_count": len(failed_still_clean),
            "still_clean_task_ids": failed_still_clean,
        },
        "current_clean_candidate": {
            "total": len(current),
            "pass": sum(1 for row in current if row.get("qa_final_decision") == "pass"),
            "fail": sum(1 for row in current if row.get("qa_final_decision") == "fail"),
            "uncertain": sum(1 for row in current if row.get("qa_final_decision") == "uncertain"),
            "critical_error_count": len(current_critical),
            "major_error_count": len(current_major),
            "major_plus_critical_error_count": len(current_major_critical),
            "major_plus_critical_rate": major_plus_critical_rate,
            "wrong_gold_set_critical_count": wrong_gold_set_critical,
            "capability_mismatch_critical_count": capability_mismatch_critical,
            "duplicate_critical_count": duplicate_critical,
            "wrong_gold_set_count": sum(1 for row in current if row.get("qa_error_type") == "wrong_gold_set"),
            "capability_mismatch_count": sum(1 for row in current if row.get("qa_error_type") == "capability_mismatch"),
            "generic_search_overtrust_count": sum(1 for row in current if row.get("qa_error_type") == "generic_search_overtrust"),
            "domain_specific_gap_count": sum(1 for row in current if row.get("qa_error_type") == "domain_specific_gap"),
            "duplicate_issue_count": sum(1 for row in current if row.get("qa_error_type") == "duplicate_issue"),
        },
        "can_accept_v1_5e_final_qa": thresholds_pass,
        "can_generate_service_level_final_clean_dataset_v1_6": thresholds_pass,
        "can_generate_api_level_final_clean_dataset_v1_6": False,
        "can_create_split_now": False,
        "can_run_baseline_now": False,
        "can_train_model_now": False,
        "go_no_go_decision_v1_5e": "GO_TO_V1_6_SERVICE_LEVEL_FINAL_CLEAN_DATASET" if thresholds_pass else "NO_GO_REVIEW_OR_FIX_REQUIRED",
    }


def write_analysis_report(
    review_set: Path,
    reviewed: Path,
    merged: Path,
    rows: list[dict[str, str]],
    analysis: dict[str, Any],
) -> None:
    current = analysis["current_clean_candidate"]
    regression = analysis["v1_5d_previous_failed_regression"]
    lines = [
        "# Final QA Analysis Report v1.5e",
        "",
        f"Generated time: {now_text()}",
        f"Input review set: `{review_set}`",
        f"Input reviewed CSV: `{reviewed}`",
        f"Merged output: `{merged}`",
        f"Sample count: {len(rows)}",
        "",
        "This analysis uses only user-provided QA fields. Detector predictions are not treated as human final labels.",
        "",
        "## QA Final Decision Distribution",
        "",
        *table_lines(analysis["overall_qa_final_decision_distribution"]),
        "",
        "## QA Error Type Distribution",
        "",
        *table_lines(analysis["qa_error_type_distribution"]),
        "",
        "## QA Severity Distribution",
        "",
        *table_lines(analysis["qa_severity_distribution"]),
        "",
        "## v1.5d Previous Failed Regression Check",
        "",
        f"- failed_task_id_count: {regression['failed_task_id_count']}",
        f"- still_clean_count: {regression['still_clean_count']}",
        "",
        "## Current Clean Candidate QA",
        "",
        f"- total: {current['total']}",
        f"- pass: {current['pass']}",
        f"- fail: {current['fail']}",
        f"- uncertain: {current['uncertain']}",
        f"- critical_error_count: {current['critical_error_count']}",
        f"- major_error_count: {current['major_error_count']}",
        f"- major_plus_critical_error_count: {current['major_plus_critical_error_count']}",
        f"- major_plus_critical_rate: {current['major_plus_critical_rate']}",
        f"- wrong_gold_set_critical_count: {current['wrong_gold_set_critical_count']}",
        f"- capability_mismatch_critical_count: {current['capability_mismatch_critical_count']}",
        f"- duplicate_critical_count: {current['duplicate_critical_count']}",
        "",
        f"## Go/No-Go: {analysis['go_no_go_decision_v1_5e']}",
        "",
        "- can_generate_api_level_final_clean_dataset_v1_6: false",
        "- can_create_split_now: false",
        "- can_run_baseline_now: false",
        "- can_train_model_now: false",
    ]
    write_md(DOC_DIR / "final_qa_analysis_report_v1_5e.md", lines)


def write_go_no_go(
    review_rows: list[dict[str, str]],
    package_ok: bool,
    reviewed_exists: bool,
    analysis: dict[str, Any] | None,
) -> str:
    failed_total, failed_still_clean = scan_v14c_failed_still_clean()
    if reviewed_exists and analysis:
        decision = analysis.get("go_no_go_decision_v1_5e", "NO_GO_REVIEW_OR_FIX_REQUIRED")
        can_accept = bool(analysis.get("can_accept_v1_5e_final_qa", False))
        can_v16 = bool(analysis.get("can_generate_service_level_final_clean_dataset_v1_6", False))
        next_step = (
            "enter v1.6 service-level final clean dataset generation in a separate confirmed step"
            if can_v16
            else "inspect v1.5e QA failures/uncertain items before v1.6"
        )
    else:
        decision = "WAITING_FOR_FINAL_QA_REVIEW"
        can_accept = False
        can_v16 = False
        next_step = "complete 100-row final QA using final_qa_review_app_v1_5e.html and export the reviewed CSV"

    lines = [
        "# Final QA v1.5e Go / No-Go Report",
        "",
        f"Generated time: {now_text()}",
        f"Input review set: `outputs/final_qa_v1_5e/final_qa_review_items_v1_5e.csv`",
        f"Sample count: {len(review_rows)}",
        "",
        f"Go / No-Go Decision v1.5e: {decision}",
        "",
        f"- can_accept_v1_5e_qa_package: {str(package_ok).lower()}",
        f"- can_accept_v1_5e_final_qa: {str(can_accept).lower()}",
        f"- can_generate_service_level_final_clean_dataset_v1_6: {str(can_v16).lower()}",
        "- can_generate_api_level_final_clean_dataset_v1_6: false",
        "- can_generate_final_clean_dataset_now: false",
        "- can_create_split_now: false",
        "- can_run_baseline_now: false",
        "- can_train_model_now: false",
        "",
        "## Regression Gate",
        "",
        f"- v1.5d previous failed task ids: {failed_total}",
        f"- v1.5d previous failed still clean in v1.4c: {len(failed_still_clean)}",
        "",
        f"Recommended next step: {next_step}.",
        "",
        "This report does not generate final clean data, split data, baseline results, or model training artifacts.",
    ]
    write_md(DOC_DIR / "final_qa_v1_5e_go_no_go_report.md", lines)
    return decision


def merge_reviewed_fields(review_rows: list[dict[str, str]], reviewed_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    reviewed_by_id = {row.get("qa_item_id", ""): row for row in reviewed_rows}
    merged_rows: list[dict[str, str]] = []
    for row in review_rows:
        out = dict(row)
        reviewed = reviewed_by_id.get(row.get("qa_item_id", ""), {})
        for field in QA_HUMAN_FIELDS:
            out[field] = reviewed.get(field, row.get(field, ""))
        merged_rows.append(out)
    return merged_rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Merge and analyze v1.5e final QA user-reviewed CSV if available.")
    parser.add_argument("--review-set", type=Path, default=OUTPUT_DIR / "final_qa_review_items_v1_5e.csv")
    parser.add_argument("--reviewed", type=Path, default=OUTPUT_DIR / "final_qa_review_items_v1_5e_user_reviewed.csv")
    parser.add_argument("--merged", type=Path, default=ANALYSIS_DIR / "final_qa_review_items_v1_5e_merged.csv")
    parser.add_argument("--summary", type=Path, default=ANALYSIS_DIR / "final_qa_analysis_summary_v1_5e.json")
    args = parser.parse_args()

    if not args.review_set.exists():
        raise FileNotFoundError(f"Missing v1.5e review set: {args.review_set}")

    review_rows = read_csv(args.review_set)
    package_ok = bool(review_rows) and len(review_rows) == 100 and (OUTPUT_DIR / "final_qa_review_app_v1_5e.html").exists()
    generated = [
        str(args.review_set),
        str(OUTPUT_DIR / "final_qa_review_app_v1_5e.html"),
        str(DOC_DIR / "final_qa_review_protocol_v1_5e.md"),
    ]
    analysis: dict[str, Any] | None = None

    if not args.reviewed.exists():
        write_md(
            OUTPUT_DIR / "WAITING_FOR_FINAL_QA_REVIEW_V1_5E.md",
            [
                "# Waiting For Final QA Review v1.5e",
                "",
                f"Generated time: {now_text()}",
                f"Input review set: `{args.review_set}`",
                f"Expected reviewed CSV: `{args.reviewed}`",
                f"Sample count: {len(review_rows)}",
                "",
                "Open `outputs/final_qa_v1_5e/final_qa_review_app_v1_5e.html`, complete the QA fields, export CSV, and place it at the expected path.",
                "",
                "No final clean dataset, split, baseline, training, or automatic human labels were generated.",
            ],
        )
        generated.append(str(OUTPUT_DIR / "WAITING_FOR_FINAL_QA_REVIEW_V1_5E.md"))
    else:
        reviewed_rows = read_csv(args.reviewed)
        merged_rows = merge_reviewed_fields(review_rows, reviewed_rows)
        write_csv(args.merged, merged_rows, list(merged_rows[0].keys()) if merged_rows else [])
        analysis = analyze_reviewed(merged_rows)
        write_json(args.summary, analysis)
        write_analysis_report(args.review_set, args.reviewed, args.merged, merged_rows, analysis)
        generated.extend(
            [
                str(args.merged),
                str(args.summary),
                str(DOC_DIR / "final_qa_analysis_report_v1_5e.md"),
            ]
        )

    go_no_go = write_go_no_go(review_rows, package_ok, args.reviewed.exists(), analysis)
    generated.append(str(DOC_DIR / "final_qa_v1_5e_go_no_go_report.md"))

    failed_total, failed_still_clean = scan_v14c_failed_still_clean()
    source_group_counts = distribution(review_rows, "source_group")
    task_type_counts = distribution(review_rows, "task_type")
    prediction_counts = distribution(review_rows, "prediction_level")
    subbucket_counts = distribution(review_rows, "qa_subbucket")
    duplicate_count = sum(1 for row in review_rows if row.get("dedup_group_id"))
    v14c_summary = load_json(V14C_SUMMARY)
    package_summary = {
        "generated_time": now_text(),
        "review_set": str(args.review_set),
        "reviewed_csv": str(args.reviewed),
        "reviewed_csv_exists": args.reviewed.exists(),
        "v1_5e_review_item_count": len(review_rows),
        "current_clean_candidate_audit_count": len(review_rows),
        "v1_4c_clean_candidate_count": v14c_summary.get("v1_4c_clean_candidate_count"),
        "v1_5d_previous_failed_task_id_count": failed_total,
        "v1_5d_previous_failed_still_clean_count": len(failed_still_clean),
        "duplicate_samples_included_count": duplicate_count,
        "source_group_distribution": source_group_counts,
        "task_type_distribution": task_type_counts,
        "prediction_level_distribution": prediction_counts,
        "qa_subbucket_distribution": subbucket_counts,
        "go_no_go_decision_v1_5e": go_no_go,
        "can_accept_v1_5e_qa_package": package_ok,
        "can_accept_v1_5e_final_qa": bool(analysis and analysis.get("can_accept_v1_5e_final_qa")),
        "can_generate_service_level_final_clean_dataset_v1_6": bool(
            analysis and analysis.get("can_generate_service_level_final_clean_dataset_v1_6")
        ),
        "can_generate_api_level_final_clean_dataset_v1_6": False,
        "can_generate_final_clean_dataset_now": False,
        "can_create_split_now": False,
        "can_run_baseline_now": False,
        "can_train_model_now": False,
    }
    write_json(OUTPUT_DIR / "final_qa_v1_5e_package_summary.json", package_summary)
    generated.append(str(OUTPUT_DIR / "final_qa_v1_5e_package_summary.json"))

    archive_files = archive_v1_5e(Path.cwd())
    package_summary["archive_file_count"] = len(archive_files)
    write_json(OUTPUT_DIR / "final_qa_v1_5e_package_summary.json", package_summary)
    generated.append(str(Path("outputs/run_archives") / f"{now_text()[:10]}_final_qa_v1_5e" / "ARCHIVE_MANIFEST.md"))

    print("Generated files:")
    for path in generated:
        print(f"- {path}")
    print(f"v1.5e QA review item count: {len(review_rows)}")
    print(f"current_clean_candidate_audit count: {len(review_rows)}")
    print(f"v1.4c clean candidate count: {v14c_summary.get('v1_4c_clean_candidate_count')}")
    print(f"source_group distribution: {source_group_counts}")
    print(f"task_type distribution: {task_type_counts}")
    print(f"prediction_level distribution: {prediction_counts}")
    print(f"qa_subbucket distribution: {subbucket_counts}")
    print(f"duplicate samples included count: {duplicate_count}")
    print(f"all v1.5d 32 failed clean candidates are not clean in v1.4c: {len(failed_still_clean) == 0 and failed_total == 32}")
    print(f"Go / No-Go Decision v1.5e: {go_no_go}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
