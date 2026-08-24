from __future__ import annotations

import argparse
from pathlib import Path

from small_cleaning_v1_2_common import (
    DOC_DIR,
    OUTPUT_DIR,
    archive_v1_2,
    clean_confidence_bucket,
    ensure_dir,
    now_text,
    read_csv,
    value_counter,
    warning_flags,
    why_kept,
    write_csv,
    write_md,
)


READABLE_FIELDS = [
    "task_id",
    "task_type",
    "prediction_level",
    "dryrun_bucket_v1_2",
    "dryrun_subbucket_v1_2",
    "query_text",
    "gold_services_json",
    "gold_apis_json",
    "candidate_service_count",
    "gold_service_count",
    "candidate_api_count",
    "gold_api_count",
    "api_leak_detector_status",
    "service_leak_detector_status",
    "semantic_alignment_pred",
    "capability_coverage_pred",
    "semantic_alignment_confidence",
    "capability_coverage_confidence",
    "policy_bucket_v1",
    "keep_confidence_bucket",
    "why_kept",
    "warning_flags",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export readable keep candidate table v1.2 and archive run.")
    parser.add_argument("--project-root", type=Path, default=Path.cwd(), help="Project root. Default: current directory.")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR, help="Output directory.")
    parser.add_argument("--skip-archive", action="store_true", help="Do not archive this run.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.project_root.resolve()
    output_dir = root / args.output_dir
    ensure_dir(output_dir)

    trace_path = output_dir / "small_cleaning_dryrun_trace_v1_2.csv"
    rows = [row for row in read_csv(trace_path) if row.get("policy_decision_v1") == "policy_keep_candidate"]
    readable = []
    for row in rows:
        out = {field: row.get(field, "") for field in READABLE_FIELDS}
        out["keep_confidence_bucket"] = row.get("keep_confidence_bucket") or clean_confidence_bucket(row)
        out["why_kept"] = why_kept(row)
        out["warning_flags"] = row.get("warning_flags_v1_2") or ";".join(warning_flags(row))
        readable.append(out)

    write_csv(output_dir / "keep_candidate_readable_46_v1_2.csv", readable, READABLE_FIELDS)

    lines = [
        "# Keep Candidate Readable 46 v1.2",
        "",
        f"Generated time: {now_text()}",
        f"Input file: `{trace_path.relative_to(root) if trace_path.is_relative_to(root) else trace_path}`",
        f"Sample count: {len(readable)}",
        "",
        "Purpose: quick researcher browsing of dry-run keep candidates. This is not new human review and not final clean data.",
        "",
        "## Confidence distribution",
        "",
    ]
    for key, count in value_counter(readable, "keep_confidence_bucket").items():
        lines.append(f"- {key}: {count}")
    lines.extend(
        [
            "",
            "## Preview",
            "",
            "| task_id | task_type | level | v1.2 bucket | confidence | warnings |",
            "|---|---|---|---|---|---|",
        ]
    )
    for row in readable[:20]:
        lines.append(
            f"| {row.get('task_id')} | {row.get('task_type')} | {row.get('prediction_level')} | "
            f"{row.get('dryrun_bucket_v1_2')} | {row.get('keep_confidence_bucket')} | {row.get('warning_flags')} |"
        )
    write_md(root / DOC_DIR / "keep_candidate_readable_46_v1_2.md", lines)

    copied = []
    if not args.skip_archive:
        copied = archive_v1_2(root)

    print("Wrote readable keep candidate table.")
    print("keep candidates:", len(readable))
    print("confidence distribution:", value_counter(readable, "keep_confidence_bucket"))
    if copied:
        print("archived files:", len(copied))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
