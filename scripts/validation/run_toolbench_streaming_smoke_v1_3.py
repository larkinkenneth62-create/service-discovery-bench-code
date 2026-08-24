from __future__ import annotations

import argparse
import csv
from pathlib import Path

from toolbench_v1_3_common import (
    CANDIDATE_FIELDNAMES,
    DOC_DIR,
    OUTPUT_DIR,
    SMOKE_DIR,
    TASK_FIELDNAMES,
    ensure_dir,
    instruction_paths,
    legacy,
    locate_toolbench_root,
    now_text,
    open_csv_writer,
    process_toolbench_group,
    read_csv,
    table_lines,
    value_counter,
    write_json,
    write_md,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run ToolBench streaming smoke conversion v1.3.")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--output-dir", type=Path, default=SMOKE_DIR)
    parser.add_argument("--max-tasks-per-group", type=int, default=10)
    parser.add_argument("--progress-interval", type=int, default=5)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.project_root.resolve()
    output_dir = root / args.output_dir
    ensure_dir(output_dir)
    toolbench_root = locate_toolbench_root(root)
    if toolbench_root is None:
        raise FileNotFoundError("ToolBench root not found. Run input check first.")

    tool_root = toolbench_root / "data" / "toolenv" / "tools"
    service_metadata = legacy.load_tool_metadata(tool_root)
    error_path = output_dir / "smoke_conversion_errors.csv"
    log_path = output_dir / "smoke_conversion.log"
    error_file, error_writer = open_csv_writer(
        error_path,
        ["group", "source_query_id", "task_id", "error_type", "error_message", "raw_record_preview"],
    )
    summary = {"generated_time": now_text(), "dry_run": True, "max_tasks_per_group": args.max_tasks_per_group, "groups": {}}

    with log_path.open("w", encoding="utf-8") as log_file:
        try:
            for group, input_path in instruction_paths(toolbench_root).items():
                group_stats = process_toolbench_group(
                    group=group,
                    input_path=input_path,
                    output_dir=output_dir,
                    service_metadata=service_metadata,
                    max_tasks=args.max_tasks_per_group,
                    progress_interval=args.progress_interval,
                    suffix="smoke",
                    combined_task_writer=None,
                    combined_candidate_writer=None,
                    error_writer=error_writer,
                    log_file=log_file,
                )
                summary["groups"][group] = group_stats
        finally:
            error_file.close()

    write_json(output_dir / "smoke_conversion_summary.json", summary)

    checks = []
    fatal = False
    for group in ["G1", "G2", "G3"]:
        task_rows = read_csv(output_dir / f"{group}_task_level_smoke.csv")
        cand_rows = read_csv(output_dir / f"{group}_candidate_level_smoke.csv")
        check = {
            "group": group,
            "task_rows": len(task_rows),
            "candidate_rows": len(cand_rows),
            "task_rows_gt_0": len(task_rows) > 0,
            "candidate_rows_gt_0": len(cand_rows) > 0,
            "query_non_empty": all(row.get("query_text", "").strip() for row in task_rows),
            "candidate_services_non_empty": all(row.get("candidate_services_json", "") not in {"", "[]"} for row in task_rows),
            "candidate_apis_non_empty": all(row.get("candidate_apis_json", "") not in {"", "[]"} for row in task_rows),
            "gold_services_non_empty": all(row.get("gold_services_json", "") not in {"", "[]"} for row in task_rows),
            "gold_apis_non_empty": all(row.get("gold_apis_json", "") not in {"", "[]"} for row in task_rows),
            "gold_in_candidate_services_all_yes": all(row.get("gold_in_candidate_services") == "yes" for row in task_rows),
            "gold_in_candidate_apis_all_yes": all(row.get("gold_in_candidate_apis") == "yes" for row in task_rows),
        }
        if not all(v for k, v in check.items() if k not in {"group", "task_rows", "candidate_rows"}):
            fatal = True
        checks.append(check)

    lines = [
        "# ToolBench Streaming Smoke Report v1.3",
        "",
        f"Generated time: {now_text()}",
        f"ToolBench root: `{toolbench_root}`",
        f"Smoke output dir: `{args.output_dir}`",
        "",
        "Scope: smoke conversion only. No full cleaning, split, baseline, model training, final clean dataset, or new human review was run.",
        "",
        "## Summary",
        "",
        "| group | task rows | candidate rows | gold services in candidates | gold APIs in candidates |",
        "|---|---:|---:|---|---|",
    ]
    for check in checks:
        lines.append(
            f"| {check['group']} | {check['task_rows']} | {check['candidate_rows']} | "
            f"{check['gold_in_candidate_services_all_yes']} | {check['gold_in_candidate_apis_all_yes']} |"
        )
    lines.extend(["", "## Decision", "", f"- smoke_passed: `{str(not fatal).lower()}`"])
    write_md(root / DOC_DIR / "toolbench_streaming_smoke_report_v1_3.md", lines)

    print("Smoke conversion complete.")
    print("smoke_passed:", not fatal)
    return 1 if fatal else 0


if __name__ == "__main__":
    raise SystemExit(main())
