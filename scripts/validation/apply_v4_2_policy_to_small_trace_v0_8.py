"""Apply v4.2 conservative policy to v0.8 small detector trace."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List

from small_full_pipeline_v0_8_utils import (
    DOCS_DIR,
    OUTPUT_DIR,
    POLICY_COLUMNS,
    apply_v42_trace_policy,
    count_by,
    ensure_dirs,
    markdown_table,
    now_str,
    read_csv,
    status_distribution,
    write_csv,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Apply v4.2 conservative policy trace to v0.8 small detector output.")
    parser.add_argument("--input", type=Path, default=OUTPUT_DIR / "small_full_pipeline_detector_trace.csv")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--docs-dir", type=Path, default=DOCS_DIR)
    return parser


def write_report(path: Path, input_path: Path, rows: List[Dict[str, object]]) -> None:
    sections = [
        ("policy_decision", "Policy Decision"),
        ("policy_bucket", "Policy Bucket"),
        ("requires_human_or_llm_review", "Requires Human/LLM Review"),
        ("can_be_clean_ready_without_semantic_capability_detector", "Can Be Clean-Ready Without Semantic/Capability Detector"),
        ("api_leak_detector_status", "API Leak Detector Status"),
        ("candidate_space_status", "Candidate Space Status"),
        ("gold_in_candidate_services", "Gold In Candidate Services"),
        ("gold_in_candidate_apis", "Gold In Candidate APIs"),
    ]
    missing_sem_cap_keep = [
        row
        for row in rows
        if row.get("policy_decision") == "keep_for_cleaning_candidate"
        and (
            row.get("semantic_alignment_check") == "missing_or_unavailable"
            or row.get("capability_coverage_check") == "missing_or_unavailable"
        )
    ]
    lines = [
        "# Small Full-Pipeline Policy Trace Report v0.8",
        "",
        f"Generated time: {now_str()}",
        f"Input file: `{input_path}`",
        f"Sample count: {len(rows)}",
        "",
        "Scope: conservative policy trace only. No full cleaning, final clean dataset, split, baseline, or model training was run.",
        "",
        "Because v0.8 has no reliable semantic/capability detector, rows with `missing_or_unavailable` should fail closed into remove/uncertain rather than policy keep.",
    ]
    for key, title in sections:
        lines.extend(["", f"## {title}", ""])
        lines.extend(markdown_table(status_distribution(rows, key), ["value", "count"], max_rows=80))
    lines.extend(
        [
            "",
            "## Missing Semantic/Capability Into Keep Check",
            "",
            f"- Count: {len(missing_sem_cap_keep)}",
        ]
    )
    if missing_sem_cap_keep:
        lines.extend(
            markdown_table(
                missing_sem_cap_keep,
                ["v0_8_sample_id", "task_id", "task_type", "policy_decision", "semantic_alignment_check", "capability_coverage_check", "query_text"],
                max_rows=20,
            )
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "A large uncertain bucket is expected here. v0.8 tests whether the pipeline can safely produce trace diagnostics on raw/dry-run samples, not whether it can produce a clean dataset.",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    ensure_dirs()
    if not args.input.exists():
        print(f"ERROR: missing detector trace input: {args.input}")
        return 1
    _, rows = read_csv(args.input)
    policy_rows = [apply_v42_trace_policy(row) for row in rows]
    out_csv = args.output_dir / "small_full_pipeline_policy_trace.csv"
    write_csv(out_csv, policy_rows, POLICY_COLUMNS)
    report = args.docs_dir / "small_full_pipeline_policy_trace_report_v0_8.md"
    write_report(report, args.input, policy_rows)
    print(f"Policy trace rows: {len(policy_rows)}")
    print(f"policy_decision distribution: {count_by(policy_rows, 'policy_decision')}")
    print(f"policy_bucket distribution: {count_by(policy_rows, 'policy_bucket')}")
    print(f"Wrote {out_csv}")
    print(f"Wrote {report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
