#!/usr/bin/env python
"""Convert ToolBench instruction files into service-discovery candidate CSVs.

This is an initial, inspectable converter for Phase 1. It reads ToolBench
G1/G2/G3 instruction JSON files, expands each task into candidate-level rows,
and writes small samples by default. Use --max-tasks-per-file 0 for full output.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple


GROUP_FILES = {
    "G1": "G1_query.json",
    "G2": "G2_query.json",
    "G3": "G3_query.json",
}

COMPOSITION_PATTERNS = [
    r"\bfirst\b",
    r"\bthen\b",
    r"\bafter\b",
    r"\bbefore\b",
    r"\bnext\b",
    r"\bfinally\b",
    r"\bbased on\b",
    r"\busing the result\b",
    r"\bwith the result\b",
    r"\bdepending on\b",
]


def normalize_text(value: Any) -> str:
    text = "" if value is None else str(value)
    return re.sub(r"\s+", " ", text.strip().lower())


def signature(*parts: Any) -> str:
    joined = "\n".join(normalize_text(part) for part in parts)
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()


def api_key(tool_name: Any, api_name: Any) -> Tuple[str, str]:
    return (normalize_text(tool_name), normalize_text(api_name))


def iter_json_array(path: Path) -> Iterator[Dict[str, Any]]:
    """Stream objects from a top-level JSON array without loading the file."""
    decoder = json.JSONDecoder()
    buffer = ""
    started = False

    with path.open("r", encoding="utf-8") as file:
        while True:
            chunk = file.read(1024 * 1024)
            if not chunk:
                break
            buffer += chunk

            pos = 0
            if not started:
                start = buffer.find("[")
                if start < 0:
                    continue
                pos = start + 1
                started = True

            while True:
                while pos < len(buffer) and buffer[pos] in " \r\n\t,":
                    pos += 1
                if pos >= len(buffer):
                    break
                if buffer[pos] == "]":
                    return
                try:
                    obj, end = decoder.raw_decode(buffer, pos)
                except json.JSONDecodeError:
                    break
                if isinstance(obj, dict):
                    yield obj
                pos = end

            buffer = buffer[pos:]


def load_tool_metadata(tool_root: Path) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """Load service metadata from ToolBench toolenv/tools JSON files."""
    metadata: Dict[Tuple[str, str], Dict[str, Any]] = {}
    if not tool_root.exists():
        return metadata

    for path in tool_root.rglob("*.json"):
        try:
            with path.open("r", encoding="utf-8") as file:
                obj = json.load(file)
        except Exception:
            continue

        category = path.parent.name
        tool_name = obj.get("tool_name") or obj.get("title") or path.stem
        key = (normalize_text(category), normalize_text(tool_name))
        metadata[key] = {
            "service_description": obj.get("tool_description", ""),
            "service_title": obj.get("title", ""),
            "service_home_url": obj.get("home_url", ""),
            "service_host": obj.get("host", ""),
            "service_pricing": obj.get("pricing", ""),
            "service_score": obj.get("score", ""),
        }
    return metadata


def get_gold_apis(task: Dict[str, Any]) -> List[Tuple[str, str]]:
    gold = []
    for item in task.get("relevant APIs", []) or []:
        if isinstance(item, Sequence) and not isinstance(item, (str, bytes)) and len(item) >= 2:
            gold.append((str(item[0]), str(item[1])))
    return gold


def query_mentions_any(query: str, names: Iterable[str]) -> bool:
    query_norm = normalize_text(query)
    for name in names:
        name_norm = normalize_text(name)
        if not name_norm:
            continue
        if len(name_norm) < 3:
            continue
        if name_norm in query_norm:
            return True
    return False


def has_composition_signal(group: str, query: str, gold_categories: Sequence[str]) -> bool:
    if group == "G3":
        return True
    query_norm = normalize_text(query)
    if any(re.search(pattern, query_norm) for pattern in COMPOSITION_PATTERNS):
        return True
    if len(set(normalize_text(category) for category in gold_categories if category)) > 1:
        return True
    return False


def classify_task(group: str, query: str, gold_services: Sequence[str], gold_categories: Sequence[str]) -> str:
    if len(set(normalize_text(service) for service in gold_services if service)) <= 1:
        return "single_service_discovery_raw"
    if has_composition_signal(group, query, gold_categories):
        return "composable_service_discovery_raw"
    return "multi_service_discovery_raw"


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def iter_candidate_rows(
    source_dataset: str,
    group: str,
    task: Dict[str, Any],
    service_metadata: Dict[Tuple[str, str], Dict[str, Any]],
) -> Iterator[Dict[str, Any]]:
    query = task.get("query", "")
    source_query_id = task.get("query_id", "")
    candidates = task.get("api_list", []) or []
    gold_apis = get_gold_apis(task)
    gold_api_keys = {api_key(tool, api) for tool, api in gold_apis}
    gold_services = sorted({tool for tool, _api in gold_apis})

    gold_categories = []
    for candidate in candidates:
        if api_key(candidate.get("tool_name"), candidate.get("api_name")) in gold_api_keys:
            category = candidate.get("category_name", "")
            if category:
                gold_categories.append(str(category))

    task_type = classify_task(group, query, gold_services, gold_categories)
    task_id = f"{source_dataset}_{group}_{source_query_id}"
    task_sig = signature(source_dataset, group, source_query_id, query, json_dumps(gold_apis))
    query_sig = signature(query)
    query_mentions_gold_api = query_mentions_any(query, [api for _tool, api in gold_apis])
    query_mentions_gold_service = query_mentions_any(query, gold_services)

    for rank, candidate in enumerate(candidates):
        category = candidate.get("category_name", "")
        service_name = candidate.get("tool_name", "")
        api_name = candidate.get("api_name", "")
        service_key = (normalize_text(category), normalize_text(service_name))
        service_info = service_metadata.get(service_key, {})
        candidate_key = api_key(service_name, api_name)

        yield {
            "task_id": task_id,
            "task_type": task_type,
            "source_dataset": source_dataset,
            "source_group": group,
            "source_query_id": source_query_id,
            "query_text": query,
            "candidate_rank": rank,
            "candidate_category_name": category,
            "candidate_service_name": service_name,
            "candidate_service_description": service_info.get("service_description", ""),
            "candidate_service_title": service_info.get("service_title", ""),
            "candidate_service_home_url": service_info.get("service_home_url", ""),
            "candidate_service_host": service_info.get("service_host", ""),
            "candidate_api_name": api_name,
            "candidate_api_description": candidate.get("api_description", ""),
            "candidate_api_method": candidate.get("method", ""),
            "candidate_required_parameters_json": json_dumps(candidate.get("required_parameters", [])),
            "candidate_optional_parameters_json": json_dumps(candidate.get("optional_parameters", [])),
            "gold_services_json": json_dumps(gold_services),
            "gold_apis_json": json_dumps([{"service_name": tool, "api_name": api} for tool, api in gold_apis]),
            "is_gold_api": int(candidate_key in gold_api_keys),
            "is_gold_service": int(normalize_text(service_name) in {normalize_text(s) for s in gold_services}),
            "query_mentions_any_gold_api": int(query_mentions_gold_api),
            "query_mentions_any_gold_service": int(query_mentions_gold_service),
            "task_signature": task_sig,
            "query_signature": query_sig,
        }


def write_csv(path: Path, rows: Iterable[Dict[str, Any]], fieldnames: List[str]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
            count += 1
    return count


def build_rows(args: argparse.Namespace) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    toolbench_root = Path(args.toolbench_root)
    instruction_root = toolbench_root / "data" / "instruction"
    tool_root = toolbench_root / "data" / "toolenv" / "tools"
    service_metadata = load_tool_metadata(tool_root)

    all_rows: List[Dict[str, Any]] = []
    summary: Dict[str, Any] = {
        "source_dataset": "ToolBench",
        "max_tasks_per_file": args.max_tasks_per_file,
        "groups": {},
    }

    for group, file_name in GROUP_FILES.items():
        path = instruction_root / file_name
        group_tasks = 0
        group_rows = 0
        task_type_counts: Dict[str, int] = {}
        gold_row_count = 0

        for task in iter_json_array(path):
            if args.max_tasks_per_file and group_tasks >= args.max_tasks_per_file:
                break
            group_tasks += 1
            rows = list(iter_candidate_rows("ToolBench", group, task, service_metadata))
            for row in rows:
                task_type_counts[row["task_type"]] = task_type_counts.get(row["task_type"], 0) + 1
                gold_row_count += int(row["is_gold_api"])
            group_rows += len(rows)
            all_rows.extend(rows)

        summary["groups"][group] = {
            "tasks_read": group_tasks,
            "candidate_rows": group_rows,
            "gold_candidate_rows": gold_row_count,
            "task_type_row_counts": task_type_counts,
        }

    return all_rows, summary


def build_task_level_sample(rows: Sequence[Dict[str, Any]], max_tasks_per_type: int) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["task_id"]), []).append(row)

    tasks = []
    per_type_counts: Dict[str, int] = {}
    for task_id, task_rows in grouped.items():
        first = task_rows[0]
        task_type = str(first["task_type"])
        if per_type_counts.get(task_type, 0) >= max_tasks_per_type:
            continue
        candidate_services = []
        candidate_apis = []
        seen_services = set()
        for row in task_rows:
            service_key = row["candidate_service_name"]
            if service_key not in seen_services:
                seen_services.add(service_key)
                candidate_services.append({
                    "category_name": row["candidate_category_name"],
                    "service_name": row["candidate_service_name"],
                    "service_description": row["candidate_service_description"],
                })
            candidate_apis.append({
                "category_name": row["candidate_category_name"],
                "service_name": row["candidate_service_name"],
                "api_name": row["candidate_api_name"],
                "api_description": row["candidate_api_description"],
                "is_gold_api": row["is_gold_api"],
            })
        tasks.append({
            "task_id": task_id,
            "task_type": first["task_type"],
            "source_dataset": first["source_dataset"],
            "source_group": first["source_group"],
            "source_query_id": first["source_query_id"],
            "query_text": first["query_text"],
            "candidate_services_json": json_dumps(candidate_services),
            "candidate_apis_json": json_dumps(candidate_apis),
            "gold_services_json": first["gold_services_json"],
            "gold_apis_json": first["gold_apis_json"],
            "query_mentions_any_gold_api": first["query_mentions_any_gold_api"],
            "query_mentions_any_gold_service": first["query_mentions_any_gold_service"],
            "task_signature": first["task_signature"],
            "query_signature": first["query_signature"],
        })
        per_type_counts[task_type] = per_type_counts.get(task_type, 0) + 1
    return tasks


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--toolbench-root", default="external_sources/ToolBench")
    parser.add_argument("--output-dir", default="outputs/toolbench_conversion_samples")
    parser.add_argument(
        "--max-tasks-per-file",
        type=int,
        default=20,
        help="Tasks to read per G file. Use 0 for full conversion.",
    )
    parser.add_argument(
        "--task-level-sample-per-type",
        type=int,
        default=5,
        help="Task-level examples to keep for each inferred task type.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir)
    rows, summary = build_rows(args)
    if not rows:
        raise SystemExit("No rows produced.")

    fieldnames = list(rows[0].keys())
    candidate_path = output_dir / "toolbench_service_candidates_sample.csv"
    row_count = write_csv(candidate_path, rows, fieldnames)

    task_rows = build_task_level_sample(rows, args.task_level_sample_per_type)
    task_fieldnames = list(task_rows[0].keys())
    task_path = output_dir / "toolbench_task_level_sample.csv"
    task_count = write_csv(task_path, task_rows, task_fieldnames)

    summary["candidate_sample_csv"] = str(candidate_path)
    summary["candidate_sample_rows"] = row_count
    summary["task_level_sample_csv"] = str(task_path)
    summary["task_level_sample_rows"] = task_count
    summary_path = output_dir / "conversion_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
