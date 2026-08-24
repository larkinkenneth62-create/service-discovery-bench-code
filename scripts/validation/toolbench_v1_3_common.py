from __future__ import annotations

import csv
import json
import shutil
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Sequence, TextIO, Tuple


ROOT = Path.cwd()
OUTPUT_DIR = Path("outputs/toolbench_full_raw_streaming_v1_3")
FULL_DIR = OUTPUT_DIR / "full"
SMOKE_DIR = OUTPUT_DIR / "smoke"
VALIDATION_DIR = OUTPUT_DIR / "validation"
DOC_DIR = Path("docs/phase1")
TOOLBENCH_ROOT_CANDIDATES = [
    Path("external_sources/ToolBench"),
    Path("data/raw/repos/ToolBench"),
]

FIRST_ATTEMPT = {
    "G1": {"tasks_read": 88995, "candidate_rows": 432799, "gold_candidate_rows": 198244},
    "G2": {"tasks_read": 87070, "candidate_rows": 570930, "gold_candidate_rows": 226333},
    "G3": {"tasks_read": 25709, "candidate_rows": 202845, "gold_candidate_rows": 73607},
}
TEACHER_TARGET = {"total_raw_tasks": 202604, "total_candidate_rows": 1211467}

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
    "candidate_service_count",
    "gold_service_count",
    "candidate_api_count",
    "gold_api_count",
    "gold_in_candidate_services",
    "gold_in_candidate_apis",
    "query_mentions_any_gold_api",
    "query_mentions_any_gold_service",
    "task_signature",
    "query_signature",
    "metadata_json",
]

CANDIDATE_FIELDNAMES = [
    "candidate_row_id",
    "task_id",
    "task_type",
    "source_dataset",
    "source_group",
    "source_query_id",
    "query_text",
    "candidate_service_name",
    "candidate_api_name",
    "candidate_service_description",
    "candidate_api_description",
    "candidate_category",
    "is_gold_service",
    "is_gold_api",
    "gold_services_json",
    "gold_apis_json",
    "query_mentions_candidate_api",
    "query_mentions_candidate_service",
    "query_mentions_any_gold_api",
    "query_mentions_any_gold_service",
    "metadata_json",
]


def build_dataset_path() -> Path:
    return ROOT / "scripts" / "build_dataset"


if str(build_dataset_path()) not in sys.path:
    sys.path.insert(0, str(build_dataset_path()))

import convert_toolbench_to_service_candidates as legacy  # noqa: E402


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def write_md(path: Path, lines: Sequence[str]) -> None:
    ensure_dir(path.parent)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")


def write_json(path: Path, payload: object) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def read_json(path: Path) -> object:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def open_csv_writer(path: Path, fieldnames: Sequence[str]) -> Tuple[TextIO, csv.DictWriter]:
    ensure_dir(path.parent)
    file = path.open("w", encoding="utf-8-sig", newline="")
    writer = csv.DictWriter(file, fieldnames=list(fieldnames), extrasaction="ignore")
    writer.writeheader()
    return file, writer


def table_lines(counter: Dict[str, int]) -> List[str]:
    lines = ["| value | count |", "|---|---|"]
    for key, count in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"| {key} | {count} |")
    return lines


def value_counter(rows: Iterable[Dict[str, Any]], field: str) -> Dict[str, int]:
    return dict(Counter((str(row.get(field, "")) or "<blank>").strip() or "<blank>" for row in rows))


def norm(value: Any) -> str:
    return legacy.normalize_text(value)


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def parse_json_array(value: str) -> List[Any]:
    try:
        data = json.loads(value or "[]")
        return data if isinstance(data, list) else []
    except Exception:
        return []


def locate_toolbench_root(root: Path = ROOT) -> Path | None:
    for candidate in TOOLBENCH_ROOT_CANDIDATES:
        path = root / candidate
        if (path / "data" / "instruction" / "G1_query.json").exists():
            return path
    return None


def instruction_paths(toolbench_root: Path) -> Dict[str, Path]:
    return {
        group: toolbench_root / "data" / "instruction" / file_name
        for group, file_name in legacy.GROUP_FILES.items()
    }


def answer_paths(toolbench_root: Path) -> Dict[str, Path]:
    answer_root = toolbench_root / "data" / "answer"
    return {
        "G1": answer_root / "G1_answer",
        "G2": answer_root / "G2_answer",
        "G3": answer_root / "G3_answer",
    }


def count_json_array_stream(path: Path, max_items: int = 0) -> Tuple[int, Dict[str, Any]]:
    count = 0
    first: Dict[str, Any] = {}
    for obj in legacy.iter_json_array(path):
        count += 1
        if not first:
            first = obj
        if max_items and count >= max_items:
            break
    return count, first


def candidate_api_key(row: Dict[str, Any]) -> Tuple[str, str]:
    return (norm(row.get("candidate_service_name")), norm(row.get("candidate_api_name")))


def task_gold_api_keys(task_rows: Sequence[Dict[str, Any]]) -> set[Tuple[str, str]]:
    if not task_rows:
        return set()
    gold = parse_json_array(str(task_rows[0].get("gold_apis_json", "[]")))
    return {(norm(item.get("service_name")), norm(item.get("api_name"))) for item in gold if isinstance(item, dict)}


def build_task_raw_row(task_rows: Sequence[Dict[str, Any]], input_file: Path) -> Dict[str, Any]:
    first = task_rows[0]
    candidate_services: List[Dict[str, Any]] = []
    candidate_apis: List[Dict[str, Any]] = []
    seen_services: set[str] = set()
    for row in task_rows:
        service_key = norm(row["candidate_service_name"])
        if service_key not in seen_services:
            seen_services.add(service_key)
            candidate_services.append(
                {
                    "category_name": row["candidate_category_name"],
                    "service_name": row["candidate_service_name"],
                    "service_description": row["candidate_service_description"],
                    "is_gold_service": int(row["is_gold_service"]),
                }
            )
        candidate_apis.append(
            {
                "category_name": row["candidate_category_name"],
                "service_name": row["candidate_service_name"],
                "api_name": row["candidate_api_name"],
                "api_description": row["candidate_api_description"],
                "is_gold_api": int(row["is_gold_api"]),
            }
        )
    gold_services = parse_json_array(str(first["gold_services_json"]))
    gold_apis = parse_json_array(str(first["gold_apis_json"]))
    candidate_service_names = {norm(item.get("service_name")) for item in candidate_services}
    candidate_api_keys = {(norm(item.get("service_name")), norm(item.get("api_name"))) for item in candidate_apis}
    gold_service_names = {norm(item) for item in gold_services}
    gold_api_keys = {(norm(item.get("service_name")), norm(item.get("api_name"))) for item in gold_apis if isinstance(item, dict)}
    metadata = {
        "input_file": str(input_file),
        "candidate_row_count": len(task_rows),
        "script_stage": "v1.3_raw_streaming",
    }
    return {
        "task_id": first["task_id"],
        "task_type": first["task_type"],
        "source_dataset": first["source_dataset"],
        "source_group": first["source_group"],
        "source_query_id": first["source_query_id"],
        "query_text": first["query_text"],
        "candidate_services_json": compact_json(candidate_services),
        "candidate_apis_json": compact_json(candidate_apis),
        "gold_services_json": first["gold_services_json"],
        "gold_apis_json": first["gold_apis_json"],
        "candidate_service_count": len(candidate_services),
        "gold_service_count": len(gold_services),
        "candidate_api_count": len(candidate_apis),
        "gold_api_count": len(gold_apis),
        "gold_in_candidate_services": "yes" if gold_service_names.issubset(candidate_service_names) else "no",
        "gold_in_candidate_apis": "yes" if gold_api_keys.issubset(candidate_api_keys) else "no",
        "query_mentions_any_gold_api": first["query_mentions_any_gold_api"],
        "query_mentions_any_gold_service": first["query_mentions_any_gold_service"],
        "task_signature": first["task_signature"],
        "query_signature": first["query_signature"],
        "metadata_json": compact_json(metadata),
    }


def build_candidate_raw_row(row: Dict[str, Any], candidate_row_id: str) -> Dict[str, Any]:
    query = str(row.get("query_text", ""))
    metadata = {
        "candidate_rank": row.get("candidate_rank", ""),
        "candidate_service_title": row.get("candidate_service_title", ""),
        "candidate_service_home_url": row.get("candidate_service_home_url", ""),
        "candidate_service_host": row.get("candidate_service_host", ""),
        "candidate_api_method": row.get("candidate_api_method", ""),
        "candidate_required_parameters_json": row.get("candidate_required_parameters_json", "[]"),
        "candidate_optional_parameters_json": row.get("candidate_optional_parameters_json", "[]"),
        "task_signature": row.get("task_signature", ""),
        "query_signature": row.get("query_signature", ""),
        "script_stage": "v1.3_raw_streaming",
    }
    return {
        "candidate_row_id": candidate_row_id,
        "task_id": row.get("task_id", ""),
        "task_type": row.get("task_type", ""),
        "source_dataset": row.get("source_dataset", ""),
        "source_group": row.get("source_group", ""),
        "source_query_id": row.get("source_query_id", ""),
        "query_text": query,
        "candidate_service_name": row.get("candidate_service_name", ""),
        "candidate_api_name": row.get("candidate_api_name", ""),
        "candidate_service_description": row.get("candidate_service_description", ""),
        "candidate_api_description": row.get("candidate_api_description", ""),
        "candidate_category": row.get("candidate_category_name", ""),
        "is_gold_service": row.get("is_gold_service", ""),
        "is_gold_api": row.get("is_gold_api", ""),
        "gold_services_json": row.get("gold_services_json", "[]"),
        "gold_apis_json": row.get("gold_apis_json", "[]"),
        "query_mentions_candidate_api": int(legacy.query_mentions_any(query, [row.get("candidate_api_name", "")])),
        "query_mentions_candidate_service": int(legacy.query_mentions_any(query, [row.get("candidate_service_name", "")])),
        "query_mentions_any_gold_api": row.get("query_mentions_any_gold_api", ""),
        "query_mentions_any_gold_service": row.get("query_mentions_any_gold_service", ""),
        "metadata_json": compact_json(metadata),
    }


def write_error(writer: csv.DictWriter, group: str, task: Dict[str, Any], error_type: str, error_message: str) -> None:
    writer.writerow(
        {
            "group": group,
            "source_query_id": task.get("query_id", ""),
            "task_id": f"ToolBench_{group}_{task.get('query_id', '')}",
            "error_type": error_type,
            "error_message": error_message,
            "raw_record_preview": compact_json(task)[:2000],
        }
    )


def log_line(log_file: TextIO, message: str) -> None:
    line = f"[{now_text()}] {message}"
    print(line, flush=True)
    log_file.write(line + "\n")
    log_file.flush()


def process_toolbench_group(
    group: str,
    input_path: Path,
    output_dir: Path,
    service_metadata: Dict[Any, Dict[str, Any]],
    max_tasks: int | None,
    progress_interval: int,
    suffix: str,
    combined_task_writer: csv.DictWriter | None,
    combined_candidate_writer: csv.DictWriter | None,
    error_writer: csv.DictWriter,
    log_file: TextIO,
) -> Dict[str, Any]:
    task_path = output_dir / f"{group}_task_level_{suffix}.csv"
    candidate_path = output_dir / f"{group}_candidate_level_{suffix}.csv"
    task_file, task_writer = open_csv_writer(task_path, TASK_FIELDNAMES)
    cand_file, cand_writer = open_csv_writer(candidate_path, CANDIDATE_FIELDNAMES)
    stats: Dict[str, Any] = {
        "input_file": str(input_path),
        "task_level_csv": str(task_path),
        "candidate_level_csv": str(candidate_path),
        "tasks_read": 0,
        "tasks_converted": 0,
        "task_rows": 0,
        "candidate_rows": 0,
        "gold_candidate_rows": 0,
        "conversion_errors": 0,
        "query_empty": 0,
        "candidate_empty": 0,
        "gold_empty": 0,
        "task_type_counts": {},
    }
    start = time.perf_counter()
    try:
        for task in legacy.iter_json_array(input_path):
            if max_tasks is not None and stats["tasks_read"] >= max_tasks:
                break
            stats["tasks_read"] += 1
            try:
                query = str(task.get("query", "") or "")
                if not query.strip():
                    stats["query_empty"] += 1
                    stats["conversion_errors"] += 1
                    write_error(error_writer, group, task, "empty_query", "query field is empty")
                if not (task.get("api_list", []) or []):
                    stats["candidate_empty"] += 1
                    stats["conversion_errors"] += 1
                    write_error(error_writer, group, task, "empty_candidates", "api_list is empty")
                if not legacy.get_gold_apis(task):
                    stats["gold_empty"] += 1
                    stats["conversion_errors"] += 1
                    write_error(error_writer, group, task, "empty_gold", "relevant APIs is empty")

                legacy_rows = list(legacy.iter_candidate_rows("ToolBench", group, task, service_metadata))
                if not legacy_rows:
                    continue
                task_raw = build_task_raw_row(legacy_rows, input_path)
                task_writer.writerow(task_raw)
                if combined_task_writer:
                    combined_task_writer.writerow(task_raw)
                stats["task_rows"] += 1
                stats["tasks_converted"] += 1
                for legacy_row in legacy_rows:
                    candidate_row_id = f"{legacy_row['task_id']}__cand_{int(legacy_row['candidate_rank']):04d}"
                    raw_row = build_candidate_raw_row(legacy_row, candidate_row_id)
                    cand_writer.writerow(raw_row)
                    if combined_candidate_writer:
                        combined_candidate_writer.writerow(raw_row)
                    stats["candidate_rows"] += 1
                    stats["gold_candidate_rows"] += int(legacy_row["is_gold_api"])
                    task_type = str(legacy_row["task_type"])
                    stats["task_type_counts"][task_type] = stats["task_type_counts"].get(task_type, 0) + 1
            except Exception as exc:
                stats["conversion_errors"] += 1
                write_error(error_writer, group, task, type(exc).__name__, str(exc))
            if progress_interval and stats["tasks_read"] % progress_interval == 0:
                task_file.flush()
                cand_file.flush()
                log_line(
                    log_file,
                    f"{group}: tasks_read={stats['tasks_read']} task_rows={stats['task_rows']} candidate_rows={stats['candidate_rows']} errors={stats['conversion_errors']}",
                )
    finally:
        task_file.flush()
        cand_file.flush()
        task_file.close()
        cand_file.close()
    stats["elapsed_seconds"] = round(time.perf_counter() - start, 3)
    log_line(log_file, f"{group}: finished {stats}")
    return stats


def archive_paths(root: Path, archive_dir: Path, paths: Sequence[Path]) -> List[str]:
    ensure_dir(archive_dir)
    copied: List[str] = []
    for rel in paths:
        src = root / rel
        if not src.exists():
            continue
        dest = archive_dir / rel
        ensure_dir(dest.parent)
        if src.is_dir():
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(src, dest)
        else:
            shutil.copy2(src, dest)
        copied.append(str(dest))
    return copied
