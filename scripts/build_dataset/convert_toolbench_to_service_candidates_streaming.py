#!/usr/bin/env python
"""Stream ToolBench instruction files into service-discovery CSVs.

This dry-run converter reuses the field mapping and task conversion logic from
`convert_toolbench_to_service_candidates.py`, but writes each G group
incrementally instead of accumulating all rows in memory.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Sequence, TextIO

import convert_toolbench_to_service_candidates as legacy


SCRIPT_VERSION = "streaming-dryrun-v0.1"
SOURCE_DATASET = "ToolBench"

CANDIDATE_FIELDNAMES = [
    "task_id",
    "task_type",
    "source_dataset",
    "source_group",
    "source_query_id",
    "query_text",
    "candidate_rank",
    "candidate_category_name",
    "candidate_service_name",
    "candidate_service_description",
    "candidate_service_title",
    "candidate_service_home_url",
    "candidate_service_host",
    "candidate_api_name",
    "candidate_api_description",
    "candidate_api_method",
    "candidate_required_parameters_json",
    "candidate_optional_parameters_json",
    "gold_services_json",
    "gold_apis_json",
    "is_gold_api",
    "is_gold_service",
    "query_mentions_any_gold_api",
    "query_mentions_any_gold_service",
    "task_signature",
    "query_signature",
]

TASK_FIELDNAMES = [
    "task_id",
    "task_type",
    "source_dataset",
    "source_group",
    "source_query_id",
    "query_text",
    "candidate_services_json",
    "candidate_apis_json",
    "gold_services_json",
    "gold_apis_json",
    "query_mentions_any_gold_api",
    "query_mentions_any_gold_service",
    "task_signature",
    "query_signature",
]


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def compact_preview(value: Any, limit: int = 2000) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except Exception:
        text = repr(value)
    return text[:limit]


def empty_json_array(value: str) -> bool:
    try:
        return json.loads(value or "[]") == []
    except Exception:
        return False


def build_task_level_row(task_rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Build one task-level row using the legacy sample builder semantics."""
    first = task_rows[0]
    candidate_services = []
    candidate_apis = []
    seen_services = set()

    for row in task_rows:
        service_key = row["candidate_service_name"]
        if service_key not in seen_services:
            seen_services.add(service_key)
            candidate_services.append(
                {
                    "category_name": row["candidate_category_name"],
                    "service_name": row["candidate_service_name"],
                    "service_description": row["candidate_service_description"],
                }
            )
        candidate_apis.append(
            {
                "category_name": row["candidate_category_name"],
                "service_name": row["candidate_service_name"],
                "api_name": row["candidate_api_name"],
                "api_description": row["candidate_api_description"],
                "is_gold_api": row["is_gold_api"],
            }
        )

    return {
        "task_id": first["task_id"],
        "task_type": first["task_type"],
        "source_dataset": first["source_dataset"],
        "source_group": first["source_group"],
        "source_query_id": first["source_query_id"],
        "query_text": first["query_text"],
        "candidate_services_json": legacy.json_dumps(candidate_services),
        "candidate_apis_json": legacy.json_dumps(candidate_apis),
        "gold_services_json": first["gold_services_json"],
        "gold_apis_json": first["gold_apis_json"],
        "query_mentions_any_gold_api": first["query_mentions_any_gold_api"],
        "query_mentions_any_gold_service": first["query_mentions_any_gold_service"],
        "task_signature": first["task_signature"],
        "query_signature": first["query_signature"],
    }


def write_error(
    error_file: TextIO,
    group: str,
    task: Dict[str, Any],
    error_type: str,
    error_message: str,
) -> None:
    source_query_id = task.get("query_id", "")
    record = {
        "group": group,
        "source_query_id": source_query_id,
        "task_id": f"{SOURCE_DATASET}_{group}_{source_query_id}",
        "error_type": error_type,
        "error_message": error_message,
        "raw_record_preview": compact_preview(task),
    }
    error_file.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    error_file.flush()


def log(log_file: TextIO, message: str) -> None:
    line = f"[{now_iso()}] {message}"
    print(line, flush=True)
    log_file.write(line + "\n")
    log_file.flush()


def process_group(
    group: str,
    input_path: Path,
    output_dir: Path,
    service_metadata: Dict[Any, Dict[str, Any]],
    max_tasks: int,
    progress_interval: int,
    log_file: TextIO,
    error_file: TextIO,
) -> Dict[str, Any]:
    candidate_path = output_dir / f"{group}_candidate_level.csv"
    task_path = output_dir / f"{group}_task_level.csv"

    stats: Dict[str, Any] = {
        "input_file": str(input_path),
        "candidate_level_csv": str(candidate_path),
        "task_level_csv": str(task_path),
        "tasks_read": 0,
        "tasks_converted": 0,
        "candidate_rows": 0,
        "task_level_rows": 0,
        "errors": 0,
        "query_empty": 0,
        "candidate_empty": 0,
        "gold_empty": 0,
        "task_type_counts": {},
        "gold_candidate_rows": 0,
    }

    log(log_file, f"{group}: input={input_path}")
    group_start = time.perf_counter()

    with candidate_path.open("w", encoding="utf-8-sig", newline="") as candidate_file:
        with task_path.open("w", encoding="utf-8-sig", newline="") as task_file:
            candidate_writer = csv.DictWriter(candidate_file, fieldnames=CANDIDATE_FIELDNAMES)
            task_writer = csv.DictWriter(task_file, fieldnames=TASK_FIELDNAMES)
            candidate_writer.writeheader()
            task_writer.writeheader()

            for task in legacy.iter_json_array(input_path):
                if max_tasks and stats["tasks_read"] >= max_tasks:
                    break
                stats["tasks_read"] += 1

                try:
                    query = str(task.get("query", "") or "")
                    if not query.strip():
                        stats["query_empty"] += 1
                        stats["errors"] += 1
                        write_error(error_file, group, task, "empty_query", "query field is empty")

                    candidates = task.get("api_list", []) or []
                    if not candidates:
                        stats["candidate_empty"] += 1
                        stats["errors"] += 1
                        write_error(error_file, group, task, "empty_candidates", "api_list is empty")

                    gold_apis = legacy.get_gold_apis(task)
                    if not gold_apis:
                        stats["gold_empty"] += 1
                        stats["errors"] += 1
                        write_error(error_file, group, task, "empty_gold", "relevant APIs is empty")

                    rows = list(legacy.iter_candidate_rows(SOURCE_DATASET, group, task, service_metadata))
                    if not rows:
                        continue

                    for row in rows:
                        candidate_writer.writerow(row)
                        stats["candidate_rows"] += 1
                        stats["gold_candidate_rows"] += int(row["is_gold_api"])
                        task_type = str(row["task_type"])
                        stats["task_type_counts"][task_type] = stats["task_type_counts"].get(task_type, 0) + 1

                    task_writer.writerow(build_task_level_row(rows))
                    stats["task_level_rows"] += 1
                    stats["tasks_converted"] += 1

                except Exception as exc:
                    stats["errors"] += 1
                    write_error(error_file, group, task, type(exc).__name__, str(exc))

                if progress_interval and stats["tasks_read"] % progress_interval == 0:
                    candidate_file.flush()
                    task_file.flush()
                    log(
                        log_file,
                        (
                            f"{group}: processed={stats['tasks_read']} "
                            f"converted={stats['tasks_converted']} "
                            f"candidate_rows={stats['candidate_rows']} "
                            f"errors={stats['errors']}"
                        ),
                    )

            candidate_file.flush()
            task_file.flush()

    stats["elapsed_seconds"] = round(time.perf_counter() - group_start, 3)
    log(
        log_file,
        (
            f"{group}: finished tasks_read={stats['tasks_read']} "
            f"tasks_converted={stats['tasks_converted']} "
            f"candidate_rows={stats['candidate_rows']} "
            f"task_level_rows={stats['task_level_rows']} "
            f"errors={stats['errors']} elapsed_seconds={stats['elapsed_seconds']}"
        ),
    )
    return stats


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--toolbench-root", default="external_sources/ToolBench")
    parser.add_argument("--output-dir", default="outputs/toolbench_full_raw_v0_1_streaming_dryrun")
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--max-tasks-per-group", type=int, default=100)
    parser.add_argument("--progress-interval", type=int, default=25)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.dry_run and args.max_tasks_per_group <= 0:
        raise SystemExit("Dry-run requires --max-tasks-per-group > 0.")

    start = time.perf_counter()
    toolbench_root = Path(args.toolbench_root)
    instruction_root = toolbench_root / "data" / "instruction"
    tool_root = toolbench_root / "data" / "toolenv" / "tools"
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    log_path = output_dir / "conversion_log.txt"
    error_path = output_dir / "error_records.jsonl"
    summary_path = output_dir / "conversion_summary.json"

    service_metadata = legacy.load_tool_metadata(tool_root)

    summary: Dict[str, Any] = {
        "script_version": SCRIPT_VERSION,
        "dry_run": bool(args.dry_run),
        "max_tasks_per_group": args.max_tasks_per_group,
        "toolbench_root": str(toolbench_root),
        "output_dir": str(output_dir),
        "started_at": now_iso(),
        "groups": {},
        "outputs": {
            "conversion_log": str(log_path),
            "error_records": str(error_path),
            "conversion_summary": str(summary_path),
        },
    }

    with log_path.open("w", encoding="utf-8") as log_file:
        with error_path.open("w", encoding="utf-8") as error_file:
            log(log_file, f"script_version={SCRIPT_VERSION}")
            log(log_file, f"dry_run={args.dry_run}")
            log(log_file, f"max_tasks_per_group={args.max_tasks_per_group}")
            log(log_file, f"toolbench_root={toolbench_root}")
            log(log_file, f"output_dir={output_dir}")
            log(log_file, f"service_metadata_count={len(service_metadata)}")

            for group, file_name in legacy.GROUP_FILES.items():
                input_path = instruction_root / file_name
                if not input_path.exists():
                    log(log_file, f"{group}: missing input file {input_path}")
                    summary["groups"][group] = {
                        "input_file": str(input_path),
                        "errors": 1,
                        "missing_input": True,
                    }
                    continue
                summary["groups"][group] = process_group(
                    group=group,
                    input_path=input_path,
                    output_dir=output_dir,
                    service_metadata=service_metadata,
                    max_tasks=args.max_tasks_per_group,
                    progress_interval=args.progress_interval,
                    log_file=log_file,
                    error_file=error_file,
                )

            summary["finished_at"] = now_iso()
            summary["elapsed_seconds"] = round(time.perf_counter() - start, 3)
            log(log_file, f"total_elapsed_seconds={summary['elapsed_seconds']}")

    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
