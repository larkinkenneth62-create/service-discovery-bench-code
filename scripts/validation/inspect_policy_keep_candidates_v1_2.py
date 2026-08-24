from __future__ import annotations

import argparse
from pathlib import Path

from small_cleaning_v1_2_common import (
    DOC_DIR,
    OUTPUT_DIR,
    blocking_flags,
    clean_confidence_bucket,
    ensure_dir,
    now_text,
    prediction_level,
    read_csv,
    table_lines,
    value_counter,
    warning_flags,
    why_kept,
    why_risky,
    write_csv,
    write_md,
)


INSPECTION_FIELDS = [
    "keep_candidate_id",
    "task_id",
    "task_type",
    "query_text",
    "prediction_level",
    "dryrun_bucket_v1_2",
    "dryrun_subbucket_v1_2",
    "keep_confidence_bucket",
    "inspection_status",
    "danger_flags",
    "warning_flags",
    "blocking_flags",
    "why_kept",
    "why_risky",
    "candidate_service_count",
    "gold_service_count",
    "candidate_api_count",
    "gold_api_count",
    "api_leak_detector_status",
    "api_leak_strength",
    "service_leak_detector_status",
    "candidate_space_status",
    "task_type_eligibility_status",
    "gold_in_candidate_services",
    "gold_in_candidate_apis",
    "semantic_alignment_pred",
    "capability_coverage_pred",
    "semantic_alignment_confidence",
    "capability_coverage_confidence",
    "policy_decision_v1",
    "policy_bucket_v1",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect dry-run keep candidates v1.2.")
    parser.add_argument("--project-root", type=Path, default=Path.cwd(), help="Project root. Default: current directory.")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR, help="Output directory.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.project_root.resolve()
    output_dir = root / args.output_dir
    ensure_dir(output_dir)

    trace_path = output_dir / "small_cleaning_dryrun_trace_v1_2.csv"
    rows = [row for row in read_csv(trace_path) if row.get("policy_decision_v1") == "policy_keep_candidate"]
    inspected = []
    for idx, row in enumerate(rows, start=1):
        bflags = blocking_flags(row)
        wflags = warning_flags(row)
        status = "danger" if row.get("dryrun_bucket_v1_2") == "dryrun_clean_candidate" and bflags else ("warning" if wflags or row.get("dryrun_bucket_v1_2") != "dryrun_clean_candidate" else "pass")
        out = dict(row)
        out.update(
            {
                "keep_candidate_id": f"KC-V12-{idx:03d}",
                "prediction_level": prediction_level(row),
                "keep_confidence_bucket": row.get("keep_confidence_bucket") or clean_confidence_bucket(row),
                "inspection_status": status,
                "danger_flags": ";".join(bflags),
                "warning_flags": ";".join(wflags),
                "blocking_flags": ";".join(bflags),
                "why_kept": why_kept(row),
                "why_risky": why_risky(row),
            }
        )
        inspected.append(out)

    write_csv(output_dir / "policy_keep_candidate_inspection_v1_2.csv", inspected, INSPECTION_FIELDS)

    service_level = sum(1 for row in inspected if row.get("prediction_level") == "service")
    api_level = sum(1 for row in inspected if row.get("prediction_level") == "api")
    weak_leak_warnings = sum(1 for row in inspected if "weak_or_generic_api_leak_unresolved" in row.get("warning_flags", ""))
    service_leak_warnings = sum(1 for row in inspected if "service_leak" in row.get("warning_flags", ""))

    lines = [
        "# Policy Keep Candidate Inspection Report v1.2",
        "",
        f"Generated time: {now_text()}",
        f"Input file: `{trace_path.relative_to(root) if trace_path.is_relative_to(root) else trace_path}`",
        f"Sample count: {len(inspected)}",
        "",
        "Scope: inspection of v1.1 policy_keep_candidate rows only. This is not new human review and not final clean data.",
        "",
        "## Summary",
        "",
        f"- total keep candidates: {len(inspected)}",
        f"- high confidence keep candidates: {sum(1 for row in inspected if row.get('keep_confidence_bucket') == 'clean_candidate_high_conf')}",
        f"- medium confidence keep candidates: {sum(1 for row in inspected if row.get('keep_confidence_bucket') == 'clean_candidate_medium_conf')}",
        f"- service-level keep candidates: {service_level}",
        f"- API-level keep candidates: {api_level}",
        f"- weak/generic leak warnings: {weak_leak_warnings}",
        f"- service leak warnings: {service_leak_warnings}",
        "",
        "## inspection_status distribution",
        "",
        *table_lines(value_counter(inspected, "inspection_status")),
        "",
        "## keep_confidence_bucket distribution",
        "",
        *table_lines(value_counter(inspected, "keep_confidence_bucket")),
        "",
        "## Representative rows",
        "",
        "| keep_candidate_id | task_id | level | confidence | status | warning_flags |",
        "|---|---|---|---|---|---|",
    ]
    for row in inspected[:20]:
        lines.append(
            f"| {row.get('keep_candidate_id')} | {row.get('task_id')} | {row.get('prediction_level')} | "
            f"{row.get('keep_confidence_bucket')} | {row.get('inspection_status')} | {row.get('warning_flags')} |"
        )
    write_md(root / DOC_DIR / "policy_keep_candidate_inspection_report_v1_2.md", lines)

    print("Wrote policy keep candidate inspection.")
    print("total keep candidates:", len(inspected))
    print("confidence distribution:", value_counter(inspected, "keep_confidence_bucket"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
