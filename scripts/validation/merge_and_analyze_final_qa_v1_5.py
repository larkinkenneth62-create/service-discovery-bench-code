from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from final_qa_v1_5_common import (
    DOC_DIR,
    OUTPUT_DIR,
    QA_FIELD_OPTIONS,
    QA_HUMAN_FIELDS,
    archive_v1_5,
    now_text,
    read_csv,
    table_lines,
    write_csv,
    write_json,
    write_md,
)


def distribution(rows: list[dict[str, str]], field: str) -> dict[str, int]:
    return dict(Counter((row.get(field, "") or "<blank>").strip() or "<blank>" for row in rows))


def analyze_reviewed(rows: list[dict[str, str]]) -> dict:
    clean = [row for row in rows if row.get("qa_bucket") == "clean_candidate"]
    critical_clean = [
        row
        for row in clean
        if row.get("qa_final_decision") == "fail" and row.get("qa_severity") == "critical"
    ]
    major_critical_clean = [
        row
        for row in clean
        if row.get("qa_final_decision") == "fail" and row.get("qa_severity") in {"major", "critical"}
    ]
    strong_api_leak_clean = [
        row for row in clean if row.get("qa_error_type") == "api_leak" and row.get("qa_severity") in {"major", "critical"}
    ]
    def rate(num: int, den: int) -> float:
        return round(num / den, 4) if den else 0.0

    completed = sum(1 for row in rows if row.get("qa_final_decision"))
    invalid_values: list[dict[str, str]] = []
    for row in rows:
        for field, options in QA_FIELD_OPTIONS.items():
            value = row.get(field, "")
            if value not in options:
                invalid_values.append({"qa_item_id": row.get("qa_item_id", ""), "field": field, "value": value})
    thresholds_pass = (
        completed == len(rows)
        and rate(len(critical_clean), len(clean)) <= 0.02
        and rate(len(major_critical_clean), len(clean)) <= 0.05
        and len(strong_api_leak_clean) == 0
    )
    return {
        "row_count": len(rows),
        "completed_review_count": completed,
        "all_reviewed": completed == len(rows),
        "invalid_values": invalid_values,
        "qa_final_decision_distribution": distribution(rows, "qa_final_decision"),
        "qa_error_type_distribution": distribution(rows, "qa_error_type"),
        "qa_severity_distribution": distribution(rows, "qa_severity"),
        "qa_bucket_distribution": distribution(rows, "qa_bucket"),
        "clean_candidate_count": len(clean),
        "clean_candidate_fail_count": sum(1 for row in clean if row.get("qa_final_decision") == "fail"),
        "clean_candidate_critical_error_count": len(critical_clean),
        "clean_candidate_critical_error_rate": rate(len(critical_clean), len(clean)),
        "clean_candidate_major_plus_critical_error_count": len(major_critical_clean),
        "clean_candidate_major_plus_critical_error_rate": rate(len(major_critical_clean), len(clean)),
        "strong_api_leak_in_clean_candidate_count": len(strong_api_leak_clean),
        "critical_error_examples": [
            {
                "qa_item_id": row.get("qa_item_id", ""),
                "task_id": row.get("task_id", ""),
                "qa_error_type": row.get("qa_error_type", ""),
                "qa_notes": row.get("qa_notes", ""),
            }
            for row in rows
            if row.get("qa_severity") == "critical"
        ][:10],
        "major_error_examples": [
            {
                "qa_item_id": row.get("qa_item_id", ""),
                "task_id": row.get("task_id", ""),
                "qa_error_type": row.get("qa_error_type", ""),
                "qa_notes": row.get("qa_notes", ""),
            }
            for row in rows
            if row.get("qa_severity") == "major"
        ][:10],
        "v1_6_recommendation": "READY_TO_REQUEST_FINAL_CLEAN_GENERATION" if thresholds_pass else "NOT_READY_OR_WAITING_FOR_FIX",
    }


def write_go_no_go(review_rows: list[dict[str, str]], reviewed_exists: bool, package_ok: bool, analysis: dict | None) -> str:
    decision = "WAITING_FOR_FINAL_QA_REVIEW"
    if reviewed_exists and analysis:
        decision = analysis.get("v1_6_recommendation", "NOT_READY_OR_WAITING_FOR_FIX")
    lines = [
        "# Final QA v1.5 Go / No-Go Report",
        "",
        f"Generated time: {now_text()}",
        f"Final QA review item count: {len(review_rows)}",
        "",
        f"Go / No-Go Decision v1.5: {decision}",
        "",
        f"- can_accept_final_qa_sampling_package: {str(package_ok).lower()}",
        "- can_generate_final_clean_dataset_now: false",
        "- can_create_split_now: false",
        "- can_run_paper_baseline_now: false",
        "- can_train_model_now: false",
        "",
        "Recommended next step: complete final QA review using `outputs/final_qa_v1_5/final_qa_review_app_v1_5.html`.",
        "",
        "This report does not authorize final clean dataset generation. v1.6 can only begin after reviewed QA results are merged and pass release thresholds.",
    ]
    write_md(DOC_DIR / "final_qa_v1_5_go_no_go_report.md", lines)
    return decision


def main() -> int:
    parser = argparse.ArgumentParser(description="Merge and analyze final QA v1.5 reviewed CSV if available.")
    parser.add_argument("--review-set", type=Path, default=OUTPUT_DIR / "final_qa_review_items_v1_5.csv")
    parser.add_argument("--reviewed", type=Path, default=OUTPUT_DIR / "final_qa_review_items_v1_5_user_reviewed.csv")
    parser.add_argument("--merged", type=Path, default=OUTPUT_DIR / "final_qa_review_items_v1_5_merged.csv")
    parser.add_argument("--summary", type=Path, default=OUTPUT_DIR / "final_qa_analysis_summary_v1_5.json")
    args = parser.parse_args()

    if not args.review_set.exists():
        raise FileNotFoundError(f"Missing final QA review set: {args.review_set}")
    review_rows = read_csv(args.review_set)
    package_ok = bool(review_rows) and (OUTPUT_DIR / "final_qa_review_app_v1_5.html").exists()
    analysis: dict | None = None
    generated = [
        str(args.review_set),
        str(OUTPUT_DIR / "final_qa_review_app_v1_5.html"),
        str(DOC_DIR / "final_qa_review_protocol_v1_5.md"),
    ]

    if not args.reviewed.exists():
        write_md(
            OUTPUT_DIR / "WAITING_FOR_FINAL_QA_REVIEW.md",
            [
                "# Waiting For Final QA Review",
                "",
                f"Generated time: {now_text()}",
                "",
                f"Expected reviewed CSV: `{args.reviewed}`",
                "",
                "Open `outputs/final_qa_v1_5/final_qa_review_app_v1_5.html`, complete the QA fields, export CSV, and place it at the expected path.",
                "",
                "No final clean dataset, split, baseline, or training was generated.",
            ],
        )
        generated.append(str(OUTPUT_DIR / "WAITING_FOR_FINAL_QA_REVIEW.md"))
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
        report_lines = [
            "# Final QA Analysis Report v1.5",
            "",
            f"Generated time: {now_text()}",
            f"Review set: `{args.review_set}`",
            f"Reviewed CSV: `{args.reviewed}`",
            f"Merged output: `{args.merged}`",
            f"Sample count: {len(merged_rows)}",
            "",
            "## QA Final Decision Distribution",
            "",
            *table_lines(analysis["qa_final_decision_distribution"]),
            "",
            "## QA Error Type Distribution",
            "",
            *table_lines(analysis["qa_error_type_distribution"]),
            "",
            "## QA Severity Distribution",
            "",
            *table_lines(analysis["qa_severity_distribution"]),
            "",
            "## Clean Candidate Error Rates",
            "",
            f"- clean_candidate_count: {analysis['clean_candidate_count']}",
            f"- critical error rate: {analysis['clean_candidate_critical_error_rate']}",
            f"- major+critical error rate: {analysis['clean_candidate_major_plus_critical_error_rate']}",
            f"- strong API leak in clean candidate count: {analysis['strong_api_leak_in_clean_candidate_count']}",
            "",
            f"## v1.6 Recommendation: {analysis['v1_6_recommendation']}",
        ]
        write_md(DOC_DIR / "final_qa_analysis_report_v1_5.md", report_lines)
        generated.extend([str(args.merged), str(args.summary), str(DOC_DIR / "final_qa_analysis_report_v1_5.md")])

    go_no_go = write_go_no_go(review_rows, args.reviewed.exists(), package_ok, analysis)
    generated.append(str(DOC_DIR / "final_qa_v1_5_go_no_go_report.md"))
    bucket_counts = distribution(review_rows, "qa_bucket")
    subbucket_counts = distribution(review_rows, "qa_subbucket")
    summary_payload = {
        "generated_time": now_text(),
        "review_set": str(args.review_set),
        "reviewed_csv_exists": args.reviewed.exists(),
        "final_qa_review_item_count": len(review_rows),
        "qa_bucket_distribution": bucket_counts,
        "qa_subbucket_distribution": subbucket_counts,
        "go_no_go_decision_v1_5": go_no_go,
        "can_accept_final_qa_sampling_package": package_ok,
        "can_generate_final_clean_dataset_now": False,
        "can_create_split_now": False,
        "can_run_paper_baseline_now": False,
        "can_train_model_now": False,
    }
    write_json(OUTPUT_DIR / "final_qa_v1_5_package_summary.json", summary_payload)
    generated.append(str(OUTPUT_DIR / "final_qa_v1_5_package_summary.json"))

    archive_files = archive_v1_5(Path.cwd())
    generated.append(str(Path("outputs/run_archives") / f"{now_text()[:10]}_final_qa_v1_5" / "ARCHIVE_MANIFEST.md"))
    summary_payload["archive_file_count"] = len(archive_files)
    write_json(OUTPUT_DIR / "final_qa_v1_5_package_summary.json", summary_payload)

    print("Generated files:")
    for path in generated:
        print(f"- {path}")
    print(f"final QA review item count: {len(review_rows)}")
    print(f"QA bucket distribution: {bucket_counts}")
    print(f"clean candidate QA sample count: {bucket_counts.get('clean_candidate', 0)}")
    print(f"removed QA sample count: {bucket_counts.get('removed', 0)}")
    print(f"uncertain QA sample count: {bucket_counts.get('uncertain', 0)}")
    print(f"service leak only QA sample count: {bucket_counts.get('service_leak_only', 0)}")
    print(f"duplicate group QA sample count: {bucket_counts.get('duplicate_clean_candidate', 0)}")
    print(f"Go / No-Go Decision v1.5: {go_no_go}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
