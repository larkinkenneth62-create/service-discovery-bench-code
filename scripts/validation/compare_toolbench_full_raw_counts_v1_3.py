from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from toolbench_v1_3_common import (
    DOC_DIR,
    FIRST_ATTEMPT,
    FULL_DIR,
    OUTPUT_DIR,
    TEACHER_TARGET,
    VALIDATION_DIR,
    archive_paths,
    ensure_dir,
    load_json,
    now_text,
    write_json,
    write_md,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare ToolBench full raw v1.3 counts against historical targets.")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.project_root.resolve()
    output_dir = root / args.output_dir
    validation_dir = root / VALIDATION_DIR
    conversion_summary = load_json(root / FULL_DIR / "conversion_summary.json")
    validation_summary = load_json(validation_dir / "full_raw_validation_summary.json")
    if not conversion_summary or not validation_summary:
        raise FileNotFoundError("conversion_summary.json or full_raw_validation_summary.json is missing.")

    groups = conversion_summary.get("groups", {})
    v13 = {}
    for group in ["G1", "G2", "G3"]:
        stats = groups.get(group, {})
        v13[group] = {
            "task_rows": stats.get("task_rows", 0),
            "candidate_rows": stats.get("candidate_rows", 0),
            "gold_candidate_rows": stats.get("gold_candidate_rows", 0),
            "diff_task_rows_vs_first_attempt": stats.get("task_rows", 0) - FIRST_ATTEMPT[group]["tasks_read"],
            "diff_candidate_rows_vs_first_attempt": stats.get("candidate_rows", 0) - FIRST_ATTEMPT[group]["candidate_rows"],
            "diff_gold_candidate_rows_vs_first_attempt": stats.get("gold_candidate_rows", 0) - FIRST_ATTEMPT[group]["gold_candidate_rows"],
        }
    totals = {
        "task_rows": sum(v13[g]["task_rows"] for g in v13),
        "candidate_rows": sum(v13[g]["candidate_rows"] for g in v13),
        "gold_candidate_rows": sum(v13[g]["gold_candidate_rows"] for g in v13),
    }
    count_diff = {
        "generated_time": now_text(),
        "v1_3_group_counts": v13,
        "v1_3_totals": totals,
        "first_attempt_totals": {
            "task_rows": sum(FIRST_ATTEMPT[g]["tasks_read"] for g in FIRST_ATTEMPT),
            "candidate_rows": sum(FIRST_ATTEMPT[g]["candidate_rows"] for g in FIRST_ATTEMPT),
            "gold_candidate_rows": sum(FIRST_ATTEMPT[g]["gold_candidate_rows"] for g in FIRST_ATTEMPT),
        },
        "teacher_target": TEACHER_TARGET,
        "diff_vs_first_attempt": {
            "task_rows": totals["task_rows"] - sum(FIRST_ATTEMPT[g]["tasks_read"] for g in FIRST_ATTEMPT),
            "candidate_rows": totals["candidate_rows"] - sum(FIRST_ATTEMPT[g]["candidate_rows"] for g in FIRST_ATTEMPT),
            "gold_candidate_rows": totals["gold_candidate_rows"] - sum(FIRST_ATTEMPT[g]["gold_candidate_rows"] for g in FIRST_ATTEMPT),
        },
        "diff_vs_teacher_target": {
            "task_rows": totals["task_rows"] - TEACHER_TARGET["total_raw_tasks"],
            "candidate_rows": totals["candidate_rows"] - TEACHER_TARGET["total_candidate_rows"],
        },
        "possible_reasons_for_differences": [
            "v1.3 uses ToolBench instruction JSON and the established converter semantics; it does not use answer trajectories as extra tasks.",
            "Teacher target may include additional query variants, retrieval/test-query partitions, or counting conventions outside the three instruction JSON files.",
            "No data were added or removed to force-count alignment.",
        ],
    }
    write_json(validation_dir / "count_diff_summary.json", count_diff)

    count_lines = [
        "# ToolBench Full Raw Count Diff Report v1.3",
        "",
        f"Generated time: {count_diff['generated_time']}",
        "",
        "Scope: count comparison only. No cleaning, split, baseline, model training, final clean dataset, or new human review was run.",
        "",
        "## Group counts",
        "",
        "| group | v1.3 task rows | v1.3 candidate rows | first task rows | first candidate rows | diff task | diff candidate |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for group in ["G1", "G2", "G3"]:
        stats = v13[group]
        first = FIRST_ATTEMPT[group]
        count_lines.append(
            f"| {group} | {stats['task_rows']} | {stats['candidate_rows']} | {first['tasks_read']} | {first['candidate_rows']} | "
            f"{stats['diff_task_rows_vs_first_attempt']} | {stats['diff_candidate_rows_vs_first_attempt']} |"
        )
    count_lines.extend(
        [
            "",
            "## Totals",
            "",
            f"- v1.3 total task rows: {totals['task_rows']}",
            f"- v1.3 total candidate rows: {totals['candidate_rows']}",
            f"- difference vs first attempt candidate rows: {count_diff['diff_vs_first_attempt']['candidate_rows']}",
            f"- difference vs teacher target task rows: {count_diff['diff_vs_teacher_target']['task_rows']}",
            f"- difference vs teacher target candidate rows: {count_diff['diff_vs_teacher_target']['candidate_rows']}",
            "",
            "## Possible reasons for differences",
            "",
            *[f"- {item}" for item in count_diff["possible_reasons_for_differences"]],
            "",
            "Important: v1.3 did not modify data to match any target count.",
        ]
    )
    write_md(root / DOC_DIR / "toolbench_full_raw_count_diff_report_v1_3.md", count_lines)

    group_stats = validation_summary.get("group_stats", {})
    datacard_lines = [
        "# ToolBench Full Raw Datacard v1.3",
        "",
        f"Generated time: {now_text()}",
        "",
        "This v1.3 output is raw converted data. It has not been cleaned for API leakage, service leakage, semantic mismatch, capability mismatch, duplication, or split leakage.",
        "",
        "## 1. Data Source",
        "",
        "- Source: ToolBench instruction JSON files G1/G2/G3.",
        "- Tool metadata: ToolBench `data/toolenv/tools`.",
        "",
        "## 2. Conversion Script",
        "",
        "- `scripts/build_dataset/run_toolbench_full_streaming_v1_3.py`",
        "- It reuses the established ToolBench converter semantics and writes v1.3 raw schema fields.",
        "",
        "## 3. Output Files",
        "",
        "- `outputs/toolbench_full_raw_streaming_v1_3/full/toolbench_full_task_level_raw.csv`",
        "- `outputs/toolbench_full_raw_streaming_v1_3/full/toolbench_full_candidate_level_raw.csv`",
        "- group-level G1/G2/G3 task and candidate raw CSV files.",
        "",
        "## 4. Task-Level Schema",
        "",
        "- one row = one task",
        "- includes query, candidate services/APIs JSON, gold services/APIs JSON, counts, leak flags, signatures, metadata.",
        "",
        "## 5. Candidate-Level Schema",
        "",
        "- one row = one task x one candidate API",
        "- includes candidate service/API names, descriptions, gold flags, query mention flags, metadata.",
        "",
        "## 6. Group-Level Statistics",
        "",
        "| group | task rows | candidate rows | avg candidate APIs | avg gold APIs |",
        "|---|---:|---:|---:|---:|",
    ]
    for group in ["G1", "G2", "G3"]:
        stats = group_stats.get(group, {})
        datacard_lines.append(
            f"| {group} | {stats.get('task_rows', 0)} | {stats.get('candidate_rows', 0)} | "
            f"{stats.get('avg_candidate_apis_per_task', 0)} | {stats.get('avg_gold_apis_per_task', 0)} |"
        )
    datacard_lines.extend(
        [
            "",
            "## 7. Known Issues",
            "",
            "- This is raw converted data, not clean-ready data.",
            "- Leak, semantic, capability, duplication, and split-leakage cleaning have not been applied.",
            "- Count differences against teacher targets are reported, not force-corrected.",
            "",
            "## 8. Next Step",
            "",
            "Next step is v1.4 full clean dry-run on full raw, not split or baseline.",
        ]
    )
    write_md(root / DOC_DIR / "toolbench_full_raw_datacard_v1_3.md", datacard_lines)

    fatal = int(validation_summary.get("fatal_error_count", 0))
    conversion_errors = int(conversion_summary.get("totals", {}).get("conversion_errors", 0))
    accept = (
        (root / FULL_DIR / "toolbench_full_task_level_raw.csv").exists()
        and (root / FULL_DIR / "toolbench_full_candidate_level_raw.csv").exists()
        and fatal == 0
    )
    go_lines = [
        "# ToolBench Full Raw Streaming v1.3 Go / No-Go Report",
        "",
        f"Generated time: {now_text()}",
        "",
        "Scope: Go/No-Go for raw conversion only. Even if accepted, this does not authorize final full cleaning, split, baseline, or model training.",
        "",
        "## Required answers",
        "",
        f"1. full streaming conversion completed: {'yes' if accept or conversion_summary else 'no'}",
        f"2. G1/G2/G3 task-level generated: {'yes' if all((root / FULL_DIR / f'{g}_task_level_raw.csv').exists() for g in ['G1','G2','G3']) else 'no'}",
        f"3. candidate-level generated: {'yes' if (root / FULL_DIR / 'toolbench_full_candidate_level_raw.csv').exists() else 'no'}",
        f"4. conversion errors: {conversion_errors}",
        f"5. validation fatal errors: {fatal}",
        f"6. task-level and candidate-level consistent: {'yes' if fatal == 0 else 'see validation errors'}",
        f"7. gold in candidate: {'pass' if fatal == 0 else 'see validation errors'}",
        f"8. diff vs first attempt candidate rows: {count_diff['diff_vs_first_attempt']['candidate_rows']}; diff vs teacher target candidate rows: {count_diff['diff_vs_teacher_target']['candidate_rows']}",
        "9. current full cleaning: false",
        "10. current split: false",
        "11. current baseline: false",
        "12. next step: v1.4 full clean dry-run on full raw" if accept else "12. next step: fix streaming conversion and rerun v1.3",
        "",
        "## Go / No-Go Decision v1.3",
        "",
        f"can_accept_full_raw_streaming_conversion: {str(accept).lower()}",
        f"can_run_full_clean_dryrun_next: {str(accept).lower()}",
        "can_run_final_full_cleaning_now: false",
        "can_create_split_now: false",
        "can_run_paper_baseline_now: false",
        "",
        "recommended_next_step:",
        "v1.4 full clean dry-run on full raw" if accept else "fix streaming conversion and rerun v1.3",
    ]
    write_md(root / DOC_DIR / "toolbench_full_raw_streaming_v1_3_go_no_go_report.md", go_lines)

    archive_dir = root / "outputs" / "run_archives" / f"{datetime.now().strftime('%Y-%m-%d')}_toolbench_full_raw_streaming_v1_3"
    copied = archive_paths(
        root,
        archive_dir,
        [
            Path("scripts/validation/check_toolbench_full_streaming_v1_3_inputs.py"),
            Path("scripts/validation/run_toolbench_streaming_smoke_v1_3.py"),
            Path("scripts/build_dataset/run_toolbench_full_streaming_v1_3.py"),
            Path("scripts/validation/validate_toolbench_full_raw_v1_3.py"),
            Path("scripts/validation/compare_toolbench_full_raw_counts_v1_3.py"),
            Path("scripts/validation/toolbench_v1_3_common.py"),
            OUTPUT_DIR,
            DOC_DIR / "toolbench_streaming_smoke_report_v1_3.md",
            DOC_DIR / "toolbench_full_raw_validation_report_v1_3.md",
            DOC_DIR / "toolbench_full_raw_count_diff_report_v1_3.md",
            DOC_DIR / "toolbench_full_raw_datacard_v1_3.md",
            DOC_DIR / "toolbench_full_raw_streaming_v1_3_go_no_go_report.md",
        ],
    )
    write_md(
        archive_dir / "ARCHIVE_MANIFEST.md",
        [
            "# ToolBench Full Raw Streaming v1.3 Archive Manifest",
            "",
            f"Generated time: {now_text()}",
            f"Archive directory: `{archive_dir}`",
            "",
            "No full cleaning, split, baseline, model training, final clean dataset, or new human review was run.",
            "",
            "## Archived files",
            "",
            *[f"- `{path}`" for path in copied],
        ],
    )

    print("Count diff complete.")
    print("v1.3 totals:", totals)
    print("diff_vs_teacher_target:", count_diff["diff_vs_teacher_target"])
    print("accept:", accept)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
