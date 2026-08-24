from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path.cwd()
VALIDATION_DIR = ROOT / "scripts" / "validation"
if str(VALIDATION_DIR) not in sys.path:
    sys.path.insert(0, str(VALIDATION_DIR))

from toolbench_v1_3_common import (  # noqa: E402
    CANDIDATE_FIELDNAMES,
    FULL_DIR,
    TASK_FIELDNAMES,
    ensure_dir,
    instruction_paths,
    legacy,
    locate_toolbench_root,
    now_text,
    open_csv_writer,
    process_toolbench_group,
    write_json,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run ToolBench full streaming raw conversion v1.3.")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--output-dir", type=Path, default=FULL_DIR)
    parser.add_argument("--progress-interval", type=int, default=5000)
    parser.add_argument("--max-tasks-per-group", type=int, default=0, help="0 means full run.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.project_root.resolve()
    output_dir = root / args.output_dir
    ensure_dir(output_dir)

    toolbench_root = locate_toolbench_root(root)
    if toolbench_root is None:
        raise FileNotFoundError("ToolBench root not found. Run check_toolbench_full_streaming_v1_3_inputs.py first.")

    tool_root = toolbench_root / "data" / "toolenv" / "tools"
    service_metadata = legacy.load_tool_metadata(tool_root)
    max_tasks = None if args.max_tasks_per_group <= 0 else args.max_tasks_per_group

    log_path = output_dir / "conversion_run.log"
    error_path = output_dir / "conversion_errors.csv"
    combined_task_path = output_dir / "toolbench_full_task_level_raw.csv"
    combined_candidate_path = output_dir / "toolbench_full_candidate_level_raw.csv"

    error_file, error_writer = open_csv_writer(
        error_path,
        ["group", "source_query_id", "task_id", "error_type", "error_message", "raw_record_preview"],
    )
    combined_task_file, combined_task_writer = open_csv_writer(combined_task_path, TASK_FIELDNAMES)
    combined_candidate_file, combined_candidate_writer = open_csv_writer(combined_candidate_path, CANDIDATE_FIELDNAMES)

    summary = {
        "generated_time": now_text(),
        "dry_run": False,
        "streaming": True,
        "toolbench_root": str(toolbench_root),
        "output_dir": str(output_dir),
        "max_tasks_per_group": max_tasks,
        "groups": {},
        "outputs": {
            "combined_task_level": str(combined_task_path),
            "combined_candidate_level": str(combined_candidate_path),
            "conversion_errors": str(error_path),
            "conversion_run_log": str(log_path),
        },
    }

    with log_path.open("w", encoding="utf-8") as log_file:
        try:
            for group, input_path in instruction_paths(toolbench_root).items():
                stats = process_toolbench_group(
                    group=group,
                    input_path=input_path,
                    output_dir=output_dir,
                    service_metadata=service_metadata,
                    max_tasks=max_tasks,
                    progress_interval=args.progress_interval,
                    suffix="raw",
                    combined_task_writer=combined_task_writer,
                    combined_candidate_writer=combined_candidate_writer,
                    error_writer=error_writer,
                    log_file=log_file,
                )
                summary["groups"][group] = stats
                combined_task_file.flush()
                combined_candidate_file.flush()
        finally:
            error_file.close()
            combined_task_file.close()
            combined_candidate_file.close()

    summary["totals"] = {
        "task_rows": sum(group.get("task_rows", 0) for group in summary["groups"].values()),
        "candidate_rows": sum(group.get("candidate_rows", 0) for group in summary["groups"].values()),
        "gold_candidate_rows": sum(group.get("gold_candidate_rows", 0) for group in summary["groups"].values()),
        "conversion_errors": sum(group.get("conversion_errors", 0) for group in summary["groups"].values()),
    }
    write_json(output_dir / "conversion_summary.json", summary)
    print("Full ToolBench streaming raw conversion complete.")
    print(summary["totals"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
