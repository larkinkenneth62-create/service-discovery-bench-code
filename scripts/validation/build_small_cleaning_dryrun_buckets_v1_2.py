from __future__ import annotations

import argparse
from pathlib import Path

from small_cleaning_v1_2_common import (
    DOC_DIR,
    OUTPUT_DIR,
    assign_dryrun_bucket,
    ensure_dir,
    now_text,
    read_csv,
    resolve_policy_trace,
    table_lines,
    value_counter,
    write_csv,
    write_md,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build small cleaning dry-run buckets v1.2.")
    parser.add_argument("--project-root", type=Path, default=Path.cwd(), help="Project root. Default: current directory.")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR, help="Output directory.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.project_root.resolve()
    output_dir = root / args.output_dir
    ensure_dir(output_dir)

    policy_trace = resolve_policy_trace(root)
    if policy_trace is None:
        raise FileNotFoundError("Could not find v1.1 policy trace. Run check_small_cleaning_dryrun_v1_2_inputs.py first.")

    source_rows = read_csv(policy_trace)
    trace_rows = [assign_dryrun_bucket(row) for row in source_rows]
    fieldnames = list(trace_rows[0].keys()) if trace_rows else []

    clean = [row for row in trace_rows if row.get("dryrun_bucket_v1_2") == "dryrun_clean_candidate"]
    removed = [row for row in trace_rows if row.get("dryrun_bucket_v1_2") == "dryrun_removed"]
    uncertain = [row for row in trace_rows if row.get("dryrun_bucket_v1_2") == "dryrun_uncertain"]
    service_leak = [row for row in trace_rows if row.get("dryrun_bucket_v1_2") == "dryrun_service_leak_only"]

    write_csv(output_dir / "small_cleaning_dryrun_trace_v1_2.csv", trace_rows, fieldnames)
    write_csv(output_dir / "dryrun_clean_candidate_v1_2.csv", clean, fieldnames)
    write_csv(output_dir / "dryrun_removed_v1_2.csv", removed, fieldnames)
    write_csv(output_dir / "dryrun_uncertain_v1_2.csv", uncertain, fieldnames)
    write_csv(output_dir / "dryrun_service_leak_only_v1_2.csv", service_leak, fieldnames)

    lines = [
        "# Small Cleaning Dry-Run Bucket Report v1.2",
        "",
        f"Generated time: {now_text()}",
        f"Input policy trace: `{policy_trace.relative_to(root) if policy_trace.is_relative_to(root) else policy_trace}`",
        f"Sample count: {len(trace_rows)}",
        "",
        "Scope: small cleaning dry-run only. This is not a final clean dataset. No full cleaning, split, baseline, model training, or new human review was run.",
        "",
        "## dry-run bucket distribution",
        "",
        *table_lines(value_counter(trace_rows, "dryrun_bucket_v1_2")),
        "",
        "## clean candidate confidence distribution",
        "",
        *table_lines(value_counter(clean, "keep_confidence_bucket")),
        "",
        "## v1.1 policy_decision_v1 distribution",
        "",
        *table_lines(value_counter(trace_rows, "policy_decision_v1")),
        "",
        "## Notes",
        "",
        "- `dryrun_clean_candidate` is still a trial output, not final clean data.",
        "- `clean_candidate_medium_conf` may be used as a dry-run candidate, but not as final release without more QA.",
        "- service-level `service_leak_only` is separated from clean candidates.",
    ]
    write_md(root / DOC_DIR / "small_cleaning_dryrun_bucket_report_v1_2.md", lines)

    print("Wrote small cleaning dry-run buckets.")
    print("bucket distribution:", value_counter(trace_rows, "dryrun_bucket_v1_2"))
    print("clean candidates:", len(clean))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
