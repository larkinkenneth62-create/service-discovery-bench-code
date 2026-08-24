from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path

from final_qa_v1_5d_common import (
    ANALYSIS_DIR,
    DOC_DIR,
    OUTPUT_DIR,
    QA_FIELD_OPTIONS,
    QA_HUMAN_FIELDS,
    archive_v1_5d,
    distribution,
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
    out: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        out[row.get(a, "") or "<blank>"][row.get(b, "") or "<blank>"] += 1
    return {key: dict(counter) for key, counter in out.items()}


def is_fail(row: dict[str, str]) -> bool:
    return row.get("qa_final_decision") == "fail"


def analyze_reviewed(rows: list[dict[str, str]]) -> dict:
    previous = [row for row in rows if row.get("qa_bucket") == "previous_failed_regression"]
    current = [row for row in rows if row.get("qa_bucket") == "current_clean_candidate_audit"]
    completed = sum(1 for row in rows if row.get("qa_final_decision"))
    previous_still_clean = [row for row in previous if row.get("v1_4b_dryrun_decision") == "dryrun_clean_candidate"]
    regression_not_fixed = [row for row in previous if row.get("qa_error_type") == "regression_not_fixed"]
    current_critical = [row for row in current if is_fail(row) and row.get("qa_severity") == "critical"]
    current_major = [row for row in current if is_fail(row) and row.get("qa_severity") == "major"]
    current_major_critical = current_major + current_critical
    capability_mismatch = [row for row in current if row.get("qa_error_type") == "capability_mismatch"]
    wrong_gold_set = [row for row in current if row.get("qa_error_type") == "wrong_gold_set"]
    generic_overtrust = [row for row in current if row.get("qa_error_type") == "generic_search_overtrust"]
    domain_gap = [row for row in current if row.get("qa_error_type") == "domain_specific_gap"]
    duplicate_critical = [row for row in current if row.get("qa_error_type") == "duplicate_issue" and row.get("qa_severity") == "critical"]
    strong_api_leak = [
        row for row in current
        if row.get("qa_leakage_check") == "api_leak_blocking" or row.get("qa_error_type") == "api_leak"
    ]
    service_no_choice = [
        row for row in current
        if row.get("qa_candidate_validity_check") == "insufficient_choice_space" or row.get("qa_error_type") == "choice_space_invalid"
    ]

    def rate(num: int, den: int) -> float:
        return round(num / den, 4) if den else 0.0

    major_plus_critical_rate = rate(len(current_major_critical), len(current))
    thresholds_pass = (
        completed == len(rows)
        and not invalid_values(rows)
        and len(previous_still_clean) == 0
        and len(current_critical) == 0
        and major_plus_critical_rate <= 0.05
        and len(strong_api_leak) == 0
        and len(service_no_choice) == 0
        and len(capability_mismatch) == 0
        and len(wrong_gold_set) == 0
        and len([row for row in generic_overtrust if row.get("qa_severity") == "critical"]) == 0
        and len(duplicate_critical) == 0
    )
    return {
        "generated_time": now_text(),
        "row_count": len(rows),
        "completed_review_count": completed,
        "all_reviewed": completed == len(rows),
        "invalid_values": invalid_values(rows),
        "overall_qa_final_decision_distribution": distribution(rows, "qa_final_decision"),
        "qa_bucket_x_final_decision": cross_tab(rows, "qa_bucket", "qa_final_decision"),
        "qa_error_type_distribution": distribution(rows, "qa_error_type"),
        "qa_severity_distribution": distribution(rows, "qa_severity"),
        "previous_failed_regression": {
            "total": len(previous),
            "pass": sum(1 for row in previous if row.get("qa_final_decision") == "pass"),
            "fail": sum(1 for row in previous if row.get("qa_final_decision") == "fail"),
            "uncertain": sum(1 for row in previous if row.get("qa_final_decision") == "uncertain"),
            "still_clean_count": len(previous_still_clean),
            "regression_not_fixed_count": len(regression_not_fixed),
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
            "capability_mismatch_count": len(capability_mismatch),
            "wrong_gold_set_count": len(wrong_gold_set),
            "generic_search_overtrust_count": len(generic_overtrust),
            "domain_specific_gap_count": len(domain_gap),
            "duplicate_critical_count": len(duplicate_critical),
            "strong_api_leak_count": len(strong_api_leak),
            "service_level_no_choice_count": len(service_no_choice),
        },
        "can_accept_v1_5d_final_qa": thresholds_pass,
        "can_generate_service_level_final_clean_dataset_v1_6": thresholds_pass,
        "can_generate_api_level_final_clean_dataset_v1_6": False,
        "can_create_split_now": False,
        "can_run_baseline_now": False,
        "can_train_model_now": False,
        "go_no_go_decision_v1_5d": "GO_TO_V1_6_SERVICE_LEVEL_FINAL_CLEAN_DATASET" if thresholds_pass else "NO_GO_REVIEW_OR_FIX_REQUIRED",
    }


def write_go_no_go(review_rows: list[dict[str, str]], package_ok: bool, reviewed_exists: bool, analysis: dict | None) -> str:
    if reviewed_exists and analysis:
        decision = analysis.get("go_no_go_decision_v1_5d", "NO_GO_REVIEW_OR_FIX_REQUIRED")
        can_accept = analysis.get("can_accept_v1_5d_final_qa", False)
        can_v16 = analysis.get("can_generate_service_level_final_clean_dataset_v1_6", False)
        next_step = "enter v1.6 service-level final clean dataset generation only" if can_v16 else "inspect v1.5d failures/uncertain items before v1.6"
    else:
        decision = "WAITING_FOR_FINAL_QA_REVIEW"
        can_accept = False
        can_v16 = False
        next_step = "complete impacted clean-candidate QA using final_qa_review_app_v1_5d.html"
    lines = [
        "# Final QA v1.5d Go / No-Go Report",
        "",
        f"Generated time: {now_text()}",
        f"Input review set: `outputs/final_qa_v1_5d/final_qa_review_items_v1_5d.csv`",
        f"Sample count: {len(review_rows)}",
        "",
        f"Go / No-Go Decision v1.5d: {decision}",
        "",
        f"- can_accept_v1_5d_qa_package: {str(package_ok).lower()}",
        f"- can_accept_v1_5d_final_qa: {str(can_accept).lower()}",
        f"- can_generate_service_level_final_clean_dataset_v1_6: {str(can_v16).lower()}",
        "- can_generate_api_level_final_clean_dataset_v1_6: false",
        "- can_generate_final_clean_dataset_now: false",
        "- can_create_split_now: false",
        "- can_run_baseline_now: false",
        "- can_train_model_now: false",
        "",
        f"Recommended next step: {next_step}.",
        "",
        "This report does not itself generate or authorize any split, baseline, or model training.",
    ]
    write_md(DOC_DIR / "final_qa_v1_5d_go_no_go_report.md", lines)
    return decision


def main() -> int:
    parser = argparse.ArgumentParser(description="Merge and analyze v1.5d final QA user-reviewed CSV if available.")
    parser.add_argument("--review-set", type=Path, default=OUTPUT_DIR / "final_qa_review_items_v1_5d.csv")
    parser.add_argument("--reviewed", type=Path, default=OUTPUT_DIR / "final_qa_review_items_v1_5d_user_reviewed.csv")
    parser.add_argument("--merged", type=Path, default=ANALYSIS_DIR / "final_qa_review_items_v1_5d_merged.csv")
    parser.add_argument("--summary", type=Path, default=ANALYSIS_DIR / "final_qa_analysis_summary_v1_5d.json")
    args = parser.parse_args()

    if not args.review_set.exists():
        raise FileNotFoundError(f"Missing v1.5d review set: {args.review_set}")
    review_rows = read_csv(args.review_set)
    package_ok = bool(review_rows) and (OUTPUT_DIR / "final_qa_review_app_v1_5d.html").exists()
    generated = [
        str(args.review_set),
        str(OUTPUT_DIR / "final_qa_review_app_v1_5d.html"),
        str(DOC_DIR / "final_qa_review_protocol_v1_5d.md"),
    ]
    analysis: dict | None = None

    if not args.reviewed.exists():
        write_md(
            OUTPUT_DIR / "WAITING_FOR_FINAL_QA_REVIEW_V1_5D.md",
            [
                "# Waiting For Final QA Review v1.5d",
                "",
                f"Generated time: {now_text()}",
                f"Expected reviewed CSV: `{args.reviewed}`",
                "",
                "Open `outputs/final_qa_v1_5d/final_qa_review_app_v1_5d.html`, complete the QA fields, export CSV, and place it at the expected path.",
                "",
                "No final clean dataset, split, baseline, training, or automatic human labels were generated.",
            ],
        )
        generated.append(str(OUTPUT_DIR / "WAITING_FOR_FINAL_QA_REVIEW_V1_5D.md"))
    else:
        reviewed_rows = read_csv(args.reviewed)
        reviewed_by_id = {row.get("qa_item_id", ""): row for row in reviewed_rows}
        merged_rows = []
        for row in review_rows:
            out = dict(row)
            reviewed = reviewed_by_id.get(row.get("qa_item_id", ""), {})
            for field in QA_HUMAN_FIELDS:
                out[field] = reviewed.get(field, row.get(field, ""))
            merged_rows.append(out)
        write_csv(args.merged, merged_rows, list(merged_rows[0].keys()) if merged_rows else [])
        analysis = analyze_reviewed(merged_rows)
        write_json(args.summary, analysis)
        current = analysis["current_clean_candidate"]
        previous = analysis["previous_failed_regression"]
        report_lines = [
            "# Final QA Analysis Report v1.5d",
            "",
            f"Generated time: {now_text()}",
            f"Review set: `{args.review_set}`",
            f"Reviewed CSV: `{args.reviewed}`",
            f"Merged output: `{args.merged}`",
            f"Sample count: {len(merged_rows)}",
            "",
            "## Overall QA Final Decision Distribution",
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
            "## previous_failed_regression",
            "",
            f"- total: {previous['total']}",
            f"- pass: {previous['pass']}",
            f"- fail: {previous['fail']}",
            f"- uncertain: {previous['uncertain']}",
            f"- still_clean_count: {previous['still_clean_count']}",
            f"- regression_not_fixed_count: {previous['regression_not_fixed_count']}",
            "",
            "## current_clean_candidate",
            "",
            f"- total: {current['total']}",
            f"- pass: {current['pass']}",
            f"- fail: {current['fail']}",
            f"- uncertain: {current['uncertain']}",
            f"- critical_error_count: {current['critical_error_count']}",
            f"- major_error_count: {current['major_error_count']}",
            f"- major_plus_critical_rate: {current['major_plus_critical_rate']}",
            f"- capability_mismatch_count: {current['capability_mismatch_count']}",
            f"- wrong_gold_set_count: {current['wrong_gold_set_count']}",
            f"- generic_search_overtrust_count: {current['generic_search_overtrust_count']}",
            f"- domain_specific_gap_count: {current['domain_specific_gap_count']}",
            f"- duplicate_critical_count: {current['duplicate_critical_count']}",
            "",
            f"## Go/No-Go: {analysis['go_no_go_decision_v1_5d']}",
        ]
        write_md(DOC_DIR / "final_qa_analysis_report_v1_5d.md", report_lines)
        generated.extend([str(args.merged), str(args.summary), str(DOC_DIR / "final_qa_analysis_report_v1_5d.md")])

    go_no_go = write_go_no_go(review_rows, package_ok, args.reviewed.exists(), analysis)
    generated.append(str(DOC_DIR / "final_qa_v1_5d_go_no_go_report.md"))
    bucket_counts = distribution(review_rows, "qa_bucket")
    subbucket_counts = distribution(review_rows, "qa_subbucket")
    source_group_counts = distribution(review_rows, "source_group")
    task_type_counts = distribution(review_rows, "task_type")
    prediction_counts = distribution(review_rows, "prediction_level")
    previous_not_clean = all(
        row.get("v1_4b_dryrun_decision") != "dryrun_clean_candidate"
        for row in review_rows
        if row.get("qa_bucket") == "previous_failed_regression"
    )
    duplicate_count = sum(
        1 for row in review_rows
        if row.get("qa_bucket") == "current_clean_candidate_audit" and row.get("dedup_group_id")
    )
    summary_payload = {
        "generated_time": now_text(),
        "review_set": str(args.review_set),
        "reviewed_csv_exists": args.reviewed.exists(),
        "v1_5d_qa_review_item_count": len(review_rows),
        "previous_failed_regression_count": bucket_counts.get("previous_failed_regression", 0),
        "current_clean_candidate_audit_count": bucket_counts.get("current_clean_candidate_audit", 0),
        "duplicate_samples_included_count": duplicate_count,
        "qa_bucket_distribution": bucket_counts,
        "qa_subbucket_distribution": subbucket_counts,
        "source_group_distribution": source_group_counts,
        "task_type_distribution": task_type_counts,
        "prediction_level_distribution": prediction_counts,
        "all_32_previous_failures_not_clean_in_v1_4b": previous_not_clean,
        "go_no_go_decision_v1_5d": go_no_go,
        "can_accept_v1_5d_qa_package": package_ok,
        "can_generate_final_clean_dataset_now": False,
        "can_create_split_now": False,
        "can_run_baseline_now": False,
        "can_train_model_now": False,
    }
    write_json(OUTPUT_DIR / "final_qa_v1_5d_package_summary.json", summary_payload)
    generated.append(str(OUTPUT_DIR / "final_qa_v1_5d_package_summary.json"))

    archive_files = archive_v1_5d(Path.cwd())
    summary_payload["archive_file_count"] = len(archive_files)
    write_json(OUTPUT_DIR / "final_qa_v1_5d_package_summary.json", summary_payload)
    generated.append(str(Path("outputs/run_archives") / f"{now_text()[:10]}_final_qa_v1_5d" / "ARCHIVE_MANIFEST.md"))

    print("Generated files:")
    for path in generated:
        print(f"- {path}")
    print(f"v1.5d QA review item count: {len(review_rows)}")
    print(f"previous_failed_regression count: {bucket_counts.get('previous_failed_regression', 0)}")
    print(f"current_clean_candidate_audit count: {bucket_counts.get('current_clean_candidate_audit', 0)}")
    print(f"duplicate samples included count: {duplicate_count}")
    print(f"source_group distribution: {source_group_counts}")
    print(f"task_type distribution: {task_type_counts}")
    print(f"prediction_level distribution: {prediction_counts}")
    print(f"all 32 previous failures are not clean in v1.4b: {previous_not_clean}")
    print(f"Go / No-Go Decision v1.5d: {go_no_go}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
