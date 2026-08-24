from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from small_cleaning_v1_2_common import (
    DOC_DIR,
    OUTPUT_DIR,
    dangerous_flags_for_clean,
    ensure_dir,
    load_json,
    now_text,
    read_csv,
    table_lines,
    value_counter,
    write_csv,
    write_json,
    write_md,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check dangerous errors in small cleaning dry-run v1.2.")
    parser.add_argument("--project-root", type=Path, default=Path.cwd(), help="Project root. Default: current directory.")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR, help="Output directory.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.project_root.resolve()
    output_dir = root / args.output_dir
    ensure_dir(output_dir)

    trace_path = output_dir / "small_cleaning_dryrun_trace_v1_2.csv"
    rows = read_csv(trace_path)
    clean_rows = [row for row in rows if row.get("dryrun_bucket_v1_2") == "dryrun_clean_candidate"]
    error_rows = []
    flag_counter: Counter[str] = Counter()
    for row in clean_rows:
        flags = dangerous_flags_for_clean(row)
        for flag in flags:
            flag_counter[flag] += 1
        if flags:
            out = dict(row)
            out["dangerous_error_flags"] = ";".join(flags)
            error_rows.append(out)

    fieldnames = list(error_rows[0].keys()) if error_rows else list(rows[0].keys()) + ["dangerous_error_flags"]
    write_csv(output_dir / "dangerous_error_trace_v1_2.csv", error_rows, fieldnames)

    dangerous_error_count = len(error_rows)
    summary = {
        "generated_time": now_text(),
        "trace_rows": len(rows),
        "dryrun_clean_candidate_count": len(clean_rows),
        "policy_keep_candidate_count": sum(1 for row in rows if row.get("policy_decision_v1") == "policy_keep_candidate"),
        "dangerous_error_count": dangerous_error_count,
        "dangerous_error_flag_distribution": dict(flag_counter),
        "strong_api_leak_into_clean": flag_counter.get("strong_or_blocking_api_leak_into_clean", 0),
        "api_leak_blocking_into_clean": flag_counter.get("strong_or_blocking_api_leak_into_clean", 0),
        "gold_missing_into_clean": flag_counter.get("gold_missing_from_candidate_into_clean", 0),
        "service_level_no_choice_into_clean": flag_counter.get("service_level_no_choice_space_into_clean", 0),
        "api_level_no_api_choice_into_clean": flag_counter.get("api_level_no_api_choice_space_into_clean", 0),
        "semantic_mismatch_into_clean": flag_counter.get("semantic_mismatch_into_clean", 0),
        "capability_mismatch_into_clean": flag_counter.get("capability_mismatch_into_clean", 0),
        "semantic_uncertain_into_clean": flag_counter.get("semantic_uncertain_into_clean", 0),
        "coverage_uncertain_into_clean": flag_counter.get("coverage_uncertain_into_clean", 0),
        "service_level_service_leak_into_clean": flag_counter.get("service_level_service_leak_only_into_clean", 0),
        "task_type_invalid_into_clean": flag_counter.get("task_type_invalid_into_clean", 0),
        "missing_semantic_capability_into_clean": flag_counter.get("missing_semantic_or_capability_into_clean", 0),
        "can_prepare_full_raw_streaming_conversion": dangerous_error_count == 0,
    }
    write_json(output_dir / "dangerous_error_summary_v1_2.json", summary)

    lines = [
        "# Small Cleaning Dry-Run Dangerous Error Report v1.2",
        "",
        f"Generated time: {summary['generated_time']}",
        f"Input file: `{trace_path.relative_to(root) if trace_path.is_relative_to(root) else trace_path}`",
        f"Sample count: {len(rows)}",
        "",
        "Scope: dangerous-error check only. This is not final clean data. No full cleaning, split, baseline, model training, or new human review was run.",
        "",
        "## Summary",
        "",
        f"- dryrun_clean_candidate_count: {len(clean_rows)}",
        f"- dangerous_error_count: {dangerous_error_count}",
        f"- can_prepare_full_raw_streaming_conversion: `{str(dangerous_error_count == 0).lower()}`",
        "",
        "## Dangerous flag distribution",
        "",
        *table_lines(dict(flag_counter) if flag_counter else {"none": 0}),
        "",
        "## Problem rows",
        "",
        "| task_id | dryrun_bucket | flags | query |",
        "|---|---|---|---|",
    ]
    for row in error_rows:
        lines.append(
            f"| {row.get('task_id')} | {row.get('dryrun_bucket_v1_2')} | {row.get('dangerous_error_flags')} | "
            f"{row.get('query_text', '')[:180]} |"
        )
    write_md(root / DOC_DIR / "small_cleaning_dryrun_dangerous_error_report_v1_2.md", lines)

    bucket_dist = value_counter(rows, "dryrun_bucket_v1_2")
    policy_dist = value_counter(rows, "policy_decision_v1")
    conf_dist = value_counter(clean_rows, "keep_confidence_bucket")
    policy_keep_rows = [row for row in rows if row.get("policy_decision_v1") == "policy_keep_candidate"]
    policy_keep_high = sum(
        1
        for row in policy_keep_rows
        if row.get("semantic_alignment_confidence") == "high" and row.get("capability_coverage_confidence") == "high"
    )
    policy_keep_medium = len(policy_keep_rows) - policy_keep_high
    go = {
        "can_accept_small_cleaning_dryrun": dangerous_error_count == 0,
        "can_prepare_full_raw_streaming_conversion": dangerous_error_count == 0,
        "can_run_full_cleaning_now": False,
        "can_create_split_now": False,
        "can_run_paper_baseline_now": False,
        "recommended_next_step": "v1.3 ToolBench full streaming raw conversion"
        if dangerous_error_count == 0
        else "fix policy/detector bugs and rerun v1.2",
    }
    go_lines = [
        "# Small Cleaning Dry-Run v1.2 Go / No-Go Report",
        "",
        f"Generated time: {now_text()}",
        f"Input file: `{trace_path.relative_to(root) if trace_path.is_relative_to(root) else trace_path}`",
        f"Sample count: {len(rows)}",
        "",
        "Scope: Go/No-Go for next engineering step only. Even if v1.2 passes, this does not authorize full cleaning, split, baseline, model training, or final clean dataset generation.",
        "",
        "## Required questions",
        "",
        f"1. v1.2 small cleaning dry-run 是否跑通？ {'yes' if rows else 'no'}",
        f"2. 输入是否为 300 条 v0.8 sample？ {'yes' if len(rows) == 300 else 'no'} ({len(rows)})",
        f"3. policy_keep_candidate 是否为 46 条？ {'yes' if policy_dist.get('policy_keep_candidate', 0) == 46 else 'no'} ({policy_dist.get('policy_keep_candidate', 0)}); v1.2 dryrun_clean_candidate = {bucket_dist.get('dryrun_clean_candidate', 0)}",
        f"4. 46 条 policy_keep_candidate 中 high-confidence / medium-confidence: {policy_keep_high} / {policy_keep_medium}; v1.2 clean 中 high/medium = {conf_dist.get('clean_candidate_high_conf', 0)} / {conf_dist.get('clean_candidate_medium_conf', 0)}",
        f"5. strong API leak 进入 clean candidate？ {summary['strong_api_leak_into_clean']}",
        f"6. service-level no-choice 进入 clean candidate？ {summary['service_level_no_choice_into_clean']}",
        f"7. capability mismatch 进入 clean candidate？ {summary['capability_mismatch_into_clean']}",
        f"8. semantic mismatch 进入 clean candidate？ {summary['semantic_mismatch_into_clean']}",
        f"9. service-level service leak 进入 clean candidate？ {summary['service_level_service_leak_into_clean']}",
        "10. 当前是否可以 full cleaning？ false",
        "11. 当前是否可以 split？ false",
        "12. 当前是否可以 baseline？ false",
        f"13. 下一步应该是什么？ {go['recommended_next_step']}",
        "",
        "## Go / No-Go Decision v1.2",
        "",
        f"can_accept_small_cleaning_dryrun: {str(go['can_accept_small_cleaning_dryrun']).lower()}",
        f"can_prepare_full_raw_streaming_conversion: {str(go['can_prepare_full_raw_streaming_conversion']).lower()}",
        "can_run_full_cleaning_now: false",
        "can_create_split_now: false",
        "can_run_paper_baseline_now: false",
        "",
        "recommended_next_step:",
        go["recommended_next_step"],
        "",
        "## dry-run bucket distribution",
        "",
        *table_lines(bucket_dist),
        "",
        "## v1.1 policy_decision_v1 distribution",
        "",
        *table_lines(policy_dist),
    ]
    write_md(root / DOC_DIR / "small_cleaning_dryrun_v1_2_go_no_go_report.md", go_lines)

    print("Wrote dangerous error report and Go/No-Go report.")
    print("dangerous_error_count:", dangerous_error_count)
    print("go_no_go:", go)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
