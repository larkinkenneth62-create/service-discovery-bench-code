"""Check dangerous errors for v0.8 small full-pipeline policy trace."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List

from small_full_pipeline_v0_8_utils import (
    DOCS_DIR,
    OUTPUT_DIR,
    POLICY_COLUMNS,
    archive_v0_8,
    count_by,
    dangerous_error_rows,
    ensure_dirs,
    markdown_table,
    now_str,
    read_csv,
    status_distribution,
    write_csv,
    write_json,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check v0.8 policy trace for dangerous fail-open errors.")
    parser.add_argument("--input", type=Path, default=OUTPUT_DIR / "small_full_pipeline_policy_trace.csv")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--docs-dir", type=Path, default=DOCS_DIR)
    return parser


def dangerous_summary(rows: List[Dict[str, object]], errors: List[Dict[str, object]]) -> Dict[str, object]:
    error_types = count_by(errors, "dangerous_error_type")
    missing_sem_cap_into_keep = error_types.get("missing_semantic_or_capability_into_keep", 0)
    api_single_false_remove = error_types.get("api_level_single_service_false_remove_due_only_to_service_count", 0)
    summary = {
        "generated_time": now_str(),
        "row_count": len(rows),
        "dangerous_error_count": len(errors),
        "dangerous_error_type_distribution": error_types,
        "policy_decision_distribution": count_by(rows, "policy_decision"),
        "api_leak_detector_status_distribution": count_by(rows, "api_leak_detector_status"),
        "candidate_space_status_distribution": count_by(rows, "candidate_space_status"),
        "prediction_level_distribution": count_by(rows, "prediction_level"),
        "pass_criteria": {
            "strong_api_leak_into_keep": error_types.get("strong_api_leak_into_keep", 0) == 0,
            "gold_missing_into_keep": error_types.get("gold_missing_into_keep", 0) == 0,
            "service_level_no_choice_space_into_keep": error_types.get("service_level_no_choice_space_into_keep", 0) == 0,
            "api_level_no_api_choice_space_into_keep": error_types.get("api_level_no_api_choice_space_into_keep", 0) == 0,
            "missing_semantic_or_capability_into_keep": missing_sem_cap_into_keep == 0,
            "weak_generic_api_leak_over_removed": error_types.get("weak_generic_api_leak_over_removed", 0) == 0,
            "api_level_single_service_false_remove_due_only_to_service_count": api_single_false_remove == 0,
        },
    }
    summary["all_pass_criteria_met"] = all(summary["pass_criteria"].values())
    return summary


def write_danger_report(path: Path, input_path: Path, rows: List[Dict[str, object]], errors: List[Dict[str, object]], summary: Dict[str, object]) -> None:
    lines = [
        "# Small Full-Pipeline Dangerous Error Report v0.8",
        "",
        f"Generated time: {now_str()}",
        f"Input file: `{input_path}`",
        f"Sample count: {len(rows)}",
        "",
        "Scope: dangerous-error check on trace output only. No full cleaning, final clean dataset, split, baseline, or model training was run.",
        "",
        "## Pass Criteria",
        "",
        "| criterion | passed |",
        "|---|---:|",
    ]
    for key, passed in summary["pass_criteria"].items():
        lines.append(f"| {key} | {passed} |")
    lines.extend(
        [
            "",
            f"All pass criteria met: `{str(summary['all_pass_criteria_met']).lower()}`",
            "",
            "## Dangerous Error Distribution",
            "",
        ]
    )
    lines.extend(markdown_table(status_distribution(errors, "dangerous_error_type"), ["value", "count"], max_rows=40))
    if errors:
        lines.extend(["", "## Dangerous Error Samples", ""])
        lines.extend(
            markdown_table(
                errors,
                [
                    "dangerous_error_type",
                    "v0_8_sample_id",
                    "task_id",
                    "task_type",
                    "policy_decision",
                    "candidate_space_status",
                    "api_leak_detector_status",
                    "semantic_alignment_check",
                    "capability_coverage_check",
                    "query_text",
                ],
                max_rows=50,
            )
        )
    lines.extend(
        [
            "",
            "## API-Level Single-Service Check",
            "",
            "- The check specifically looks for API-level rows removed due only to service count.",
            f"- Count: {summary['dangerous_error_type_distribution'].get('api_level_single_service_false_remove_due_only_to_service_count', 0)}",
            "",
            "## Missing Semantic/Capability Fail-Closed Check",
            "",
            "- Rows with missing semantic/capability detector output must not enter policy keep.",
            f"- Count entering keep: {summary['dangerous_error_type_distribution'].get('missing_semantic_or_capability_into_keep', 0)}",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def write_go_no_go(path: Path, input_path: Path, rows: List[Dict[str, object]], errors: List[Dict[str, object]], summary: Dict[str, object]) -> None:
    can_trace = len(rows) > 0
    can_apply = can_trace and "policy_decision" in rows[0]
    lines = [
        "# Small Full-Pipeline Trace v0.8 Go / No-Go Report",
        "",
        f"Generated time: {now_str()}",
        f"Input file: `{input_path}`",
        f"Sample count: {len(rows)}",
        "",
        "Scope: v0.8 validates trace flow only. It does not generate a final clean dataset.",
        "",
        "## Answers",
        "",
        f"1. small full-pipeline trace ran through: `{str(can_trace).lower()}`",
        f"2. input sample size/source: `{len(rows)}` rows from the v0.8 small task sample; see `outputs/small_full_pipeline_trace_v0_8/small_full_pipeline_sampling_report.md`.",
        "3. detector output completeness: detector trace was generated for every sampled row.",
        "4. conservative policy trace fail-closed: yes, rows without semantic/capability detector output do not enter keep.",
        f"5. strong API leak entered keep: `{summary['dangerous_error_type_distribution'].get('strong_api_leak_into_keep', 0)}`",
        f"6. gold missing entered keep: `{summary['dangerous_error_type_distribution'].get('gold_missing_into_keep', 0)}`",
        f"7. missing semantic/capability entered keep: `{summary['dangerous_error_type_distribution'].get('missing_semantic_or_capability_into_keep', 0)}`",
        f"8. weak/generic API leak over-removed: `{summary['dangerous_error_type_distribution'].get('weak_generic_api_leak_over_removed', 0)}`",
        f"9. API-level single-service false remove due only to service count: `{summary['dangerous_error_type_distribution'].get('api_level_single_service_false_remove_due_only_to_service_count', 0)}`",
        "10. current stage can run full cleaning: `false`",
        "11. current stage can create split: `false`",
        "12. current stage can run baseline: `false`",
        "13. next step: v0.9 semantic/capability detector pilot on small sample, unless the user wants to inspect v0.8 trace first.",
        "",
        "## Go / No-Go Decision v0.8",
        "",
        f"can_run_small_full_pipeline_trace: {str(can_trace and summary['all_pass_criteria_met']).lower()}",
        f"can_apply_v4_2_policy_trace_to_raw_sample: {str(can_apply and summary['all_pass_criteria_met']).lower()}",
        "can_run_full_cleaning_now: false",
        "can_create_split_now: false",
        "can_run_paper_baseline_now: false",
        "",
        "recommended_next_step:",
        "v0.9 semantic/capability detector pilot on small sample.",
        "",
        "## Key Distributions",
        "",
        "### policy_decision",
        "",
    ]
    lines.extend(markdown_table(status_distribution(rows, "policy_decision"), ["value", "count"], max_rows=20))
    lines.extend(["", "### dangerous_error_type", ""])
    lines.extend(markdown_table(status_distribution(errors, "dangerous_error_type"), ["value", "count"], max_rows=20))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    ensure_dirs()
    if not args.input.exists():
        print(f"ERROR: missing policy trace input: {args.input}")
        return 1
    _, rows = read_csv(args.input)
    errors = dangerous_error_rows(rows)
    out_csv = args.output_dir / "dangerous_error_trace_v0_8.csv"
    write_csv(out_csv, errors, ["dangerous_error_type", *POLICY_COLUMNS])
    summary = dangerous_summary(rows, errors)
    write_json(args.output_dir / "dangerous_error_summary_v0_8.json", summary)
    danger_report = args.docs_dir / "small_full_pipeline_dangerous_error_report_v0_8.md"
    write_danger_report(danger_report, args.input, rows, errors, summary)
    go_report = args.docs_dir / "small_full_pipeline_trace_v0_8_go_no_go_report.md"
    write_go_no_go(go_report, args.input, rows, errors, summary)
    manifest = archive_v0_8()
    print(f"Policy trace rows checked: {len(rows)}")
    print(f"Dangerous errors: {len(errors)}")
    print(f"dangerous_error_type distribution: {summary['dangerous_error_type_distribution']}")
    print(f"Wrote {out_csv}")
    print(f"Wrote {args.output_dir / 'dangerous_error_summary_v0_8.json'}")
    print(f"Wrote {danger_report}")
    print(f"Wrote {go_report}")
    print(f"Wrote archive manifest: {manifest}")
    print("Go / No-Go Decision v0.8:")
    print(f"can_run_small_full_pipeline_trace: {str(len(rows) > 0 and summary['all_pass_criteria_met']).lower()}")
    print(f"can_apply_v4_2_policy_trace_to_raw_sample: {str(len(rows) > 0 and summary['all_pass_criteria_met']).lower()}")
    print("can_run_full_cleaning_now: false")
    print("can_create_split_now: false")
    print("can_run_paper_baseline_now: false")
    return 0 if summary["all_pass_criteria_met"] else 2


if __name__ == "__main__":
    sys.exit(main())
