#!/usr/bin/env python
"""Audit local ToolBench trace availability without modifying source data.

This stage locates the real ToolBench checkout, inventories evidence paths,
fingerprints representative schemas, builds a lightweight exact-ID index,
joins the composable recovery inventory, and normalizes explicitly ordered
tool calls for joined records. It never assigns a final composable label.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator


EXPECTED_RELATIVE_PATHS = [
    "data/instruction/G1_query.json",
    "data/instruction/G2_query.json",
    "data/instruction/G3_query.json",
    "data/test_instruction/G1_instruction.json",
    "data/test_instruction/G2_instruction.json",
    "data/test_instruction/G3_instruction.json",
    "data/answer",
    "reproduction_data",
]

DISCOVERY_TERMS = (
    "answer",
    "trajectory",
    "trajectories",
    "trace",
    "traces",
    "reproduction",
    "solution",
    "execution",
    "tool_call",
    "tool_calls",
    "intermediate",
    "observation",
)

ID_FIELDS = {"task_id", "query_id", "source_query_id", "instruction_id", "id", "record_id", "custom_id"}
QUERY_FIELDS = {"query", "query_text", "instruction", "user_query", "prompt"}
STEP_FIELDS = {"steps", "trajectory", "trajectories", "intermediate_steps", "tool_calls", "api_calls", "function_calls", "call_sequence", "chain", "path"}
TOOL_FIELDS = {"tool", "tool_name", "api", "api_name", "function", "function_name", "action", "name", "function_call"}
ARGUMENT_FIELDS = {"arguments", "args", "parameters", "params", "input", "inputs", "request", "payload"}
OUTPUT_FIELDS = {"output", "outputs", "result", "results", "response", "observation", "observations", "tool_response", "api_response", "content", "intermediate_result"}

SAFE_TEXT_EXTENSIONS = {".json", ".jsonl", ".csv", ".txt", ".md", ".markdown"}
BINARY_OR_UNTRUSTED_EXTENSIONS = {".pkl", ".pickle", ".db", ".sqlite", ".sqlite3", ".bin", ".pt", ".pth"}

PATH_INVENTORY_FIELDS = [
    "expected_or_discovered_path",
    "path_type",
    "exists",
    "actual_path",
    "is_directory",
    "file_count",
    "total_size_bytes",
    "extensions_distribution_json",
    "readable",
    "notes",
]

FILE_MANIFEST_FIELDS = [
    "source_area",
    "relative_path",
    "actual_path",
    "extension",
    "size_bytes",
    "readable",
    "safe_parse_supported",
    "index_strategy",
    "notes",
]

ID_INDEX_FIELDS = [
    "normalized_join_id",
    "original_id",
    "id_type",
    "source_file",
    "record_offset_or_json_path",
    "source_group_if_available",
    "query_text_if_available",
]

JOIN_FIELDS = [
    "inventory_row_id",
    "source_dataset",
    "source_task_id",
    "task_id",
    "source_query_id",
    "current_policy_status",
    "join_status",
    "join_type",
    "matched_source_file",
    "matched_record_path",
    "candidate_match_count",
    "ambiguity_reason",
    "notes",
]

STEP_SUMMARY_FIELDS = [
    "inventory_row_id",
    "source_task_id",
    "joined",
    "ordered_steps_found",
    "step_count",
    "distinct_service_count",
    "distinct_api_count",
    "arguments_found",
    "outputs_found",
    "observations_found",
    "parse_status",
    "parse_error",
    "source_file",
]

PARSE_ERROR_FIELDS = ["stage", "source_file", "record_path", "inventory_row_id", "error_type", "error_message"]


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def bool_text(value: bool) -> str:
    return "true" if value else "false"


def normalize_id(value: Any) -> str:
    """Apply only conservative ID normalization allowed by the audit protocol."""
    return unicodedata.normalize("NFKC", text(value)).casefold()


def extract_group(path_or_value: Any) -> str:
    match = re.search(r"(?:^|[\\/_-])G([123])(?:[\\/_-]|$)", text(path_or_value), re.IGNORECASE)
    return f"G{match.group(1)}" if match else ""


def extract_numeric_filename_id(path: Path) -> str:
    match = re.match(r"^([^_]+)(?:_|$)", path.stem)
    return text(match.group(1)) if match else ""


def is_safe_parse_supported(path: Path) -> bool:
    return path.suffix.casefold() in SAFE_TEXT_EXTENSIONS


def path_readable(path: Path) -> bool:
    try:
        if path.is_dir():
            next(path.iterdir(), None)
        else:
            with path.open("rb") as handle:
                handle.read(1)
        return True
    except (OSError, PermissionError):
        return False


def iter_files(path: Path) -> Iterator[Path]:
    if path.is_file():
        yield path
        return
    if not path.is_dir():
        return
    for current_root, directories, filenames in os.walk(path):
        directories[:] = sorted(name for name in directories if name != "__MACOSX")
        for filename in sorted(filenames):
            yield Path(current_root) / filename


def file_stats(path: Path) -> tuple[int, int, dict[str, dict[str, int]]]:
    files = list(iter_files(path)) if path.exists() else []
    counts: Counter[str] = Counter()
    sizes: Counter[str] = Counter()
    total_size = 0
    for file_path in files:
        extension = file_path.suffix.casefold() or "<none>"
        try:
            size = file_path.stat().st_size
        except OSError:
            size = 0
        counts[extension] += 1
        sizes[extension] += size
        total_size += size
    distribution = {
        extension: {"count": counts[extension], "total_size_bytes": sizes[extension]}
        for extension in sorted(counts)
    }
    return len(files), total_size, distribution


def write_csv(path: Path, fieldnames: list[str], rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def locate_toolbench_root(project_root: Path) -> tuple[Path | None, list[dict[str, Any]]]:
    external_root = project_root / "external_sources"
    fixed_candidates = [
        external_root / "ToolBench",
        external_root / "ToolBench" / "ToolBench",
        external_root / "ToolBench" / "ToolBench-main",
        external_root / "ToolBench-main",
        external_root / "ToolBench-main" / "ToolBench",
    ]
    discovered: list[Path] = []
    for candidate in fixed_candidates:
        if candidate.is_dir() and candidate not in discovered:
            discovered.append(candidate)
    if external_root.is_dir():
        for current_root, directories, _ in os.walk(external_root):
            directories[:] = sorted(name for name in directories if name != "__MACOSX")
            for name in directories:
                if name.casefold() in {"toolbench", "toolbench-main"}:
                    candidate = Path(current_root) / name
                    if candidate not in discovered:
                        discovered.append(candidate)

    scored: list[dict[str, Any]] = []
    for candidate in discovered:
        expected_exists = sum((candidate / relative).exists() for relative in EXPECTED_RELATIVE_PATHS)
        score = expected_exists * 10
        if (candidate / "data" / "answer").is_dir():
            score += 5
        if (candidate / "reproduction_data").is_dir():
            score += 5
        scored.append(
            {
                "path": str(candidate.resolve()),
                "expected_path_hits": expected_exists,
                "score": score,
                "selected": False,
            }
        )
    if not scored:
        return None, scored
    scored.sort(key=lambda row: (-row["score"], row["path"].casefold()))
    scored[0]["selected"] = True
    return Path(scored[0]["path"]), scored


def discover_alternate_trace_paths(toolbench_root: Path) -> list[Path]:
    discovered: list[Path] = []
    for current_root, directories, filenames in os.walk(toolbench_root):
        directories[:] = sorted(name for name in directories if name != "__MACOSX")
        for name in directories:
            if any(term in name.casefold() for term in DISCOVERY_TERMS):
                discovered.append(Path(current_root) / name)
        for name in filenames:
            stem = Path(name).stem.casefold()
            if any(term in stem for term in DISCOVERY_TERMS):
                discovered.append(Path(current_root) / name)
    return sorted(set(discovered), key=lambda path: str(path).casefold())


def collect_json_paths(
    value: Any,
    prefix: str = "$",
    *,
    max_depth: int = 9,
    max_nodes: int = 8000,
) -> list[str]:
    observed: list[str] = []
    nodes_seen = 0

    def visit(node: Any, path: str, depth: int) -> None:
        nonlocal nodes_seen
        if nodes_seen >= max_nodes or depth > max_depth:
            return
        nodes_seen += 1
        observed.append(path)
        if isinstance(node, dict):
            for key, child in list(node.items())[:100]:
                visit(child, f"{path}.{key}", depth + 1)
        elif isinstance(node, list):
            for child in node[:20]:
                visit(child, f"{path}[]", depth + 1)

    visit(value, prefix, 0)
    return sorted(set(observed))


def matching_paths(paths: Iterable[str], names: set[str]) -> list[str]:
    found: list[str] = []
    for path in paths:
        final = path.replace("[]", "").rsplit(".", 1)[-1].casefold()
        if final in names:
            found.append(path)
    return sorted(set(found))


def load_json_file(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def iter_jsonl(path: Path, limit: int | None = None) -> Iterator[tuple[int, Any]]:
    with path.open("r", encoding="utf-8-sig") as handle:
        for index, line in enumerate(handle, start=1):
            if limit is not None and index > limit:
                break
            if not line.strip():
                continue
            yield index, json.loads(line)


def schema_fingerprint(path: Path, sample_limit: int = 100) -> dict[str, Any]:
    fingerprint: dict[str, Any] = {
        "source_file": str(path.resolve()),
        "top_level_type": "",
        "observed_json_paths": [],
        "possible_id_paths": [],
        "possible_query_paths": [],
        "possible_step_paths": [],
        "possible_tool_paths": [],
        "possible_argument_paths": [],
        "possible_output_paths": [],
        "sample_record_count": 0,
        "parse_status": "not_parsed",
        "parse_error": "",
    }
    try:
        extension = path.suffix.casefold()
        samples: list[Any] = []
        top_level_type = ""
        if extension == ".json":
            payload = load_json_file(path)
            top_level_type = type(payload).__name__
            if isinstance(payload, list):
                samples = payload[:sample_limit]
            elif isinstance(payload, dict):
                # Aggregate converted files use numeric task IDs as object keys.
                numeric_items = [(key, value) for key, value in payload.items() if normalize_id(key).isdigit()]
                if numeric_items and len(numeric_items) >= max(2, len(payload) // 2):
                    samples = [{"__object_key_id__": key, "record": value} for key, value in numeric_items[:sample_limit]]
                else:
                    samples = [payload]
            else:
                samples = [payload]
        elif extension == ".jsonl":
            samples = [record for _, record in iter_jsonl(path, limit=sample_limit)]
            top_level_type = "jsonl_records"
        elif extension == ".csv":
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                samples = [row for _, row in zip(range(sample_limit), reader)]
            top_level_type = "csv_rows"
        elif extension in {".txt", ".md", ".markdown"}:
            with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
                sample = handle.read(20000)
            samples = [{"text": sample}]
            top_level_type = "text"
        else:
            fingerprint["parse_status"] = "recorded_not_executed"
            fingerprint["parse_error"] = "Unsupported or binary format; no deserialization attempted."
            return fingerprint

        all_paths: set[str] = set()
        for sample in samples:
            all_paths.update(collect_json_paths(sample))
        sorted_paths = sorted(all_paths)
        fingerprint.update(
            {
                "top_level_type": top_level_type,
                "observed_json_paths": sorted_paths,
                "possible_id_paths": matching_paths(sorted_paths, ID_FIELDS | {"__object_key_id__"}),
                "possible_query_paths": matching_paths(sorted_paths, QUERY_FIELDS),
                "possible_step_paths": matching_paths(sorted_paths, STEP_FIELDS),
                "possible_tool_paths": matching_paths(sorted_paths, TOOL_FIELDS),
                "possible_argument_paths": matching_paths(sorted_paths, ARGUMENT_FIELDS),
                "possible_output_paths": matching_paths(sorted_paths, OUTPUT_FIELDS),
                "sample_record_count": len(samples),
                "parse_status": "ok",
            }
        )
    except Exception as exc:  # Each error is surfaced in output, never silently skipped.
        fingerprint["parse_status"] = "parse_failed"
        fingerprint["parse_error"] = f"{type(exc).__name__}: {exc}"
    return fingerprint


def source_area_for_path(path: Path, answer_path: Path, reproduction_path: Path) -> str:
    try:
        relative = path.resolve().relative_to(answer_path.resolve())
        return "data_answer"
    except ValueError:
        pass
    try:
        relative = path.resolve().relative_to(reproduction_path.resolve())
        if relative.parts and relative.parts[0].casefold() == "model_predictions_converted":
            return "reproduction_converted"
        return "reproduction_other"
    except ValueError:
        return "alternate_trace"


def source_priority(source_file: str) -> int:
    normalized = source_file.replace("/", "\\").casefold()
    if "\\data\\answer\\" in normalized:
        return 0
    if "\\model_predictions_converted\\" in normalized:
        return 1
    if "\\reproduction_data\\" in normalized:
        return 2
    return 3


def build_file_manifest(
    toolbench_root: Path,
    answer_path: Path,
    reproduction_path: Path,
) -> tuple[list[dict[str, Any]], list[Path]]:
    files: list[Path] = []
    if answer_path.exists():
        files.extend(iter_files(answer_path))
    if reproduction_path.exists():
        files.extend(iter_files(reproduction_path))
    unique_files = sorted(set(path.resolve() for path in files), key=lambda path: str(path).casefold())
    rows: list[dict[str, Any]] = []
    for path in unique_files:
        extension = path.suffix.casefold()
        area = source_area_for_path(path, answer_path, reproduction_path)
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        rows.append(
            {
                "source_area": area,
                "relative_path": str(path.relative_to(toolbench_root.resolve())),
                "actual_path": str(path),
                "extension": extension or "<none>",
                "size_bytes": size,
                # Opening every one of 140k+ files is prohibitively slow on
                # Windows. The manifest records OS-level read permission;
                # fingerprints and joined-record parsing perform real reads.
                "readable": bool_text(os.access(path, os.R_OK)),
                "safe_parse_supported": bool_text(is_safe_parse_supported(path)),
                "index_strategy": "filename_stem_and_selected_object_keys" if extension in SAFE_TEXT_EXTENSIONS else "record_only_no_execution",
                "notes": "Untrusted binary formats are never deserialized." if extension in BINARY_OR_UNTRUSTED_EXTENSIONS else "",
            }
        )
    return rows, unique_files


def select_fingerprint_files(files: list[Path], answer_path: Path, reproduction_path: Path, per_area: int) -> list[Path]:
    by_area: dict[str, dict[str, list[Path]]] = defaultdict(lambda: defaultdict(list))
    for path in files:
        area = source_area_for_path(path, answer_path, reproduction_path)
        by_area[area][path.suffix.casefold() or "<none>"].append(path)
    selected: list[Path] = []
    for area in sorted(by_area):
        for extension in sorted(by_area[area]):
            selected.extend(sorted(by_area[area][extension], key=lambda path: str(path).casefold())[:per_area])
    return selected


def build_id_index(files: list[Path], answer_path: Path, reproduction_path: Path, errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    converted_files: list[Path] = []
    for path in files:
        group = extract_group(str(path))
        original_id = extract_numeric_filename_id(path)
        if original_id:
            rows.append(
                {
                    "normalized_join_id": normalize_id(original_id),
                    "original_id": original_id,
                    "id_type": "filename_stem",
                    "source_file": str(path),
                    "record_offset_or_json_path": "$",
                    "source_group_if_available": group,
                    "query_text_if_available": "",
                }
            )
        if source_area_for_path(path, answer_path, reproduction_path) == "reproduction_converted" and path.suffix.casefold() == ".json":
            converted_files.append(path)

    # Converted files are comparatively small aggregate dictionaries. Index only
    # top-level object keys; no deep trajectory parsing is performed here.
    for path in converted_files:
        try:
            payload = load_json_file(path)
            if not isinstance(payload, dict):
                continue
            group = extract_group(str(path))
            for key, record in payload.items():
                original_id = text(key)
                if not original_id:
                    continue
                query = text(record.get("query")) if isinstance(record, dict) else ""
                rows.append(
                    {
                        "normalized_join_id": normalize_id(original_id),
                        "original_id": original_id,
                        "id_type": "json_object_key",
                        "source_file": str(path),
                        "record_offset_or_json_path": f'$["{original_id}"]',
                        "source_group_if_available": group,
                        "query_text_if_available": query,
                    }
                )
        except Exception as exc:
            errors.append(
                {
                    "stage": "id_index",
                    "source_file": str(path),
                    "record_path": "$",
                    "inventory_row_id": "",
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                }
            )
    return rows


def deduplicate_index_matches(matches: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for match in matches:
        key = (text(match.get("source_file")), text(match.get("record_offset_or_json_path")))
        unique[key] = match
    return sorted(unique.values(), key=lambda row: (source_priority(row["source_file"]), row["source_file"], row["record_offset_or_json_path"]))


def resolve_join(
    inventory_row: dict[str, Any],
    index_lookup: dict[tuple[str, str], list[dict[str, Any]]],
) -> tuple[str, str, list[dict[str, Any]], str]:
    """Return join status, join type, selected matches, and ambiguity reason."""
    if text(inventory_row.get("source_dataset")).casefold() != "toolbench":
        return "unmatched", "unmatched", [], "Source is not ToolBench; ToolBench evidence paths are out of scope for this row."
    group = text(inventory_row.get("source_group")).upper()
    candidates = [
        ("exact_source_task_id", inventory_row.get("source_task_id")),
        ("exact_source_query_id", inventory_row.get("source_query_id")),
    ]
    task_id = text(inventory_row.get("task_id"))
    candidates.append(("exact_task_id", task_id))
    fixed_match = re.fullmatch(r"ToolBench_G([123])_(.+)", task_id, re.IGNORECASE)
    if fixed_match:
        group = group or f"G{fixed_match.group(1)}"
        candidates.append(("exact_instruction_id", fixed_match.group(2)))

    for join_type, raw_id in candidates:
        normalized = normalize_id(raw_id)
        if not normalized:
            continue
        matches = deduplicate_index_matches(index_lookup.get((group, normalized), []))
        if not matches:
            continue
        best_priority = min(source_priority(match["source_file"]) for match in matches)
        best_matches = [match for match in matches if source_priority(match["source_file"]) == best_priority]
        if len(best_matches) == 1:
            return "joined", join_type, best_matches, ""
        return (
            "ambiguous",
            "ambiguous",
            best_matches,
            f"{len(best_matches)} exact records share the same highest-priority source class; none was selected.",
        )
    return "unmatched", "unmatched", [], "No exact source task/query/instruction/filename ID match."


def load_indexed_record(match: dict[str, Any]) -> Any:
    path = Path(match["source_file"])
    payload = load_json_file(path)
    record_path = text(match.get("record_offset_or_json_path"))
    object_match = re.fullmatch(r'\$\["(.*)"\]', record_path)
    if object_match:
        if not isinstance(payload, dict):
            raise ValueError("Indexed JSON object key used on non-object payload")
        return payload[object_match.group(1)]
    return payload


def parse_jsonish(value: Any) -> Any:
    if isinstance(value, (dict, list, int, float, bool)) or value is None:
        return value
    raw = text(value)
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {"_raw": raw}


def normalize_tool_name(value: Any) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", unicodedata.normalize("NFKC", text(value)).casefold())
    return re.sub(r"_+", "_", normalized).strip("_")


def api_function_map(inventory_row: dict[str, Any]) -> dict[str, tuple[str, str]]:
    try:
        apis = json.loads(text(inventory_row.get("candidate_apis_json")) or "[]")
    except json.JSONDecodeError:
        apis = []
    mapping: dict[str, tuple[str, str]] = {}
    if not isinstance(apis, list):
        return mapping
    for api in apis:
        if not isinstance(api, dict):
            continue
        api_name = text(api.get("api_name"))
        service_name = text(api.get("service_name"))
        if not api_name:
            continue
        function_name = f"{normalize_tool_name(api_name)}_for_{normalize_tool_name(service_name)}"
        mapping[function_name] = (service_name, api_name)
        mapping.setdefault(normalize_tool_name(api_name), (service_name, api_name))
    return mapping


def map_function_name(function_name: str, mapping: dict[str, tuple[str, str]]) -> tuple[str, str]:
    normalized = normalize_tool_name(function_name)
    if normalized in mapping:
        return mapping[normalized]
    if "_for_" in normalized:
        api_name, service_name = normalized.rsplit("_for_", 1)
        return service_name, api_name
    return "", function_name


def extract_calls_from_train_messages(record: dict[str, Any], inventory_row: dict[str, Any]) -> tuple[str, list[dict[str, Any]], str]:
    answer_generation = record.get("answer_generation")
    if not isinstance(answer_generation, dict):
        return "unsupported_record_schema", [], "answer_generation object not found"
    chains = answer_generation.get("train_messages")
    if not isinstance(chains, list):
        return "unsupported_record_schema", [], "answer_generation.train_messages is not an array"
    tool_mapping = api_function_map(inventory_row)
    target_final_answer = ""
    raw_final_answer = answer_generation.get("final_answer")
    if isinstance(raw_final_answer, str):
        try:
            decoded_final = json.loads(raw_final_answer)
        except json.JSONDecodeError:
            decoded_final = None
        if isinstance(decoded_final, dict):
            target_final_answer = text(decoded_final.get("final_answer"))
        else:
            target_final_answer = text(raw_final_answer)
    target_finish_type = text(answer_generation.get("finish_type"))
    expected_return_type = {
        "give_up": "give_up_and_restart",
        "give_answer": "give_answer",
    }.get(target_finish_type, target_finish_type)
    parsed_chains: list[tuple[list[dict[str, Any]], bool, str]] = []
    for chain_index, chain in enumerate(chains):
        if not isinstance(chain, list):
            continue
        calls: list[dict[str, Any]] = []
        pending_index: int | None = None
        finish_final_answer = ""
        finish_return_type = ""
        for message_index, message in enumerate(chain):
            if not isinstance(message, dict):
                continue
            role = text(message.get("role")).casefold()
            function_call = message.get("function_call")
            if role == "assistant" and isinstance(function_call, dict):
                function_name = text(function_call.get("name"))
                if not function_name:
                    pending_index = None
                    continue
                if function_name.casefold() == "finish":
                    finish_arguments = parse_jsonish(function_call.get("arguments"))
                    if isinstance(finish_arguments, dict):
                        finish_return_type = text(finish_arguments.get("return_type"))
                        finish_final_answer = text(finish_arguments.get("final_answer"))
                    pending_index = None
                    continue
                service_name, api_name = map_function_name(function_name, tool_mapping)
                calls.append(
                    {
                        "step_index": len(calls) + 1,
                        "service_name": service_name,
                        "api_name": api_name,
                        "function_name": function_name,
                        "arguments": parse_jsonish(function_call.get("arguments")),
                        "outputs": {},
                        "observation": None,
                        "source_file": "",
                        "source_json_path": f"$.answer_generation.train_messages[{chain_index}][{message_index}].function_call",
                        "argument_source_path": f"$.answer_generation.train_messages[{chain_index}][{message_index}].function_call.arguments",
                        "output_source_path": "",
                    }
                )
                pending_index = len(calls) - 1
                continue
            if role in {"function", "tool"} and pending_index is not None:
                response_name = text(message.get("name"))
                pending = calls[pending_index]
                if response_name and normalize_tool_name(response_name) != normalize_tool_name(pending["function_name"]):
                    continue
                content = message.get("content", message.get("message"))
                pending["outputs"] = parse_jsonish(content)
                pending["observation"] = content
                pending["output_source_path"] = f"$.answer_generation.train_messages[{chain_index}][{message_index}].content"
                pending_index = None
        if calls:
            exact_final_match = bool(target_final_answer and finish_final_answer == target_final_answer)
            parsed_chains.append((calls, exact_final_match, finish_return_type))
    if not parsed_chains:
        return "no_ordered_calls", [], "No non-Finish function calls found in explicit train_messages arrays."
    exact_matches = [calls for calls, exact_match, _ in parsed_chains if exact_match]
    if len(exact_matches) == 1:
        return "ok", exact_matches[0], ""
    if len(parsed_chains) == 1:
        return "ok", parsed_chains[0][0], ""
    terminal_matches = [calls for calls, _, return_type in parsed_chains if return_type and return_type == expected_return_type]
    if len(terminal_matches) == 1:
        return "ok", terminal_matches[0], ""
    return (
        "parse_failed",
        [],
        f"Multiple ({len(parsed_chains)}) non-empty call chains found; exact final-answer selection matched {len(exact_matches)} chains and finish-type selection matched {len(terminal_matches)} chains.",
    )


def flatten_explicit_answer_details(value: Any) -> list[dict[str, Any]]:
    ordered: list[dict[str, Any]] = []

    def visit(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                visit(item)
        elif isinstance(node, dict):
            ordered.append(node)
            next_value = node.get("next")
            if isinstance(next_value, (dict, list)):
                visit(next_value)

    visit(value)
    return ordered


def extract_calls_from_converted_record(record: dict[str, Any], inventory_row: dict[str, Any]) -> tuple[str, list[dict[str, Any]], str]:
    answer = record.get("answer")
    if not isinstance(answer, dict):
        return "unsupported_record_schema", [], "answer object not found"
    details = answer.get("answer_details")
    if not isinstance(details, list):
        return "unsupported_record_schema", [], "answer.answer_details is not an array"
    messages = flatten_explicit_answer_details(details)
    mapping = api_function_map(inventory_row)
    calls: list[dict[str, Any]] = []
    for index, node in enumerate(messages):
        if text(node.get("role")).casefold() != "tool":
            continue
        message = node.get("message")
        if not isinstance(message, dict):
            continue
        function_name = text(message.get("name"))
        if not function_name or function_name.casefold() == "finish":
            continue
        service_name, api_name = map_function_name(function_name, mapping)
        calls.append(
            {
                "step_index": len(calls) + 1,
                "service_name": service_name,
                "api_name": api_name,
                "function_name": function_name,
                "arguments": parse_jsonish(message.get("arguments")),
                "outputs": parse_jsonish(message.get("response")),
                "observation": message.get("response"),
                "source_file": "",
                "source_json_path": f"$.answer.answer_details.explicit_next[{index}].message",
                "argument_source_path": f"$.answer.answer_details.explicit_next[{index}].message.arguments",
                "output_source_path": f"$.answer.answer_details.explicit_next[{index}].message.response",
            }
        )
    if not calls:
        return "no_ordered_calls", [], "No explicit role=tool records found in answer_details next-chain."
    return "ok", calls, ""


def parse_trace_record(record: Any, inventory_row: dict[str, Any], source_file: str) -> tuple[dict[str, Any], dict[str, Any]]:
    task_id = text(inventory_row.get("task_id") or inventory_row.get("source_task_id") or inventory_row.get("source_query_id"))
    query_text = text(inventory_row.get("query_text"))
    if not isinstance(record, dict):
        status, calls, error = "parse_failed", [], "Matched record is not a JSON object."
    elif isinstance(record.get("answer_generation"), dict):
        query_text = text(record["answer_generation"].get("query")) or query_text
        status, calls, error = extract_calls_from_train_messages(record, inventory_row)
    elif isinstance(record.get("answer"), dict):
        query_text = text(record.get("query")) or query_text
        status, calls, error = extract_calls_from_converted_record(record, inventory_row)
    else:
        status, calls, error = "unsupported_record_schema", [], "Neither answer_generation nor answer schema is present."
    for call in calls:
        call["source_file"] = source_file
    services = {text(call.get("service_name")) for call in calls if text(call.get("service_name"))}
    apis = {text(call.get("api_name")) for call in calls if text(call.get("api_name"))}
    arguments_found = any(call.get("arguments") not in ({}, [], None, "") for call in calls)
    outputs_found = any(call.get("outputs") not in ({}, [], None, "") for call in calls)
    observations_found = any(call.get("observation") not in (None, "", {}, []) for call in calls)
    normalized = {
        "inventory_row_id": text(inventory_row.get("inventory_id")),
        "source_task_id": task_id,
        "query_text": query_text,
        "steps": calls,
    }
    summary = {
        "inventory_row_id": text(inventory_row.get("inventory_id")),
        "source_task_id": task_id,
        "joined": "true",
        "ordered_steps_found": bool_text(status == "ok" and bool(calls)),
        "step_count": len(calls),
        "distinct_service_count": len(services),
        "distinct_api_count": len(apis),
        "arguments_found": bool_text(arguments_found),
        "outputs_found": bool_text(outputs_found),
        "observations_found": bool_text(observations_found),
        "parse_status": status,
        "parse_error": error,
        "source_file": source_file,
    }
    return normalized, summary


def build_path_inventory(toolbench_root: Path | None, root_candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[Path]]:
    rows: list[dict[str, Any]] = []
    alternates: list[Path] = []
    for candidate in root_candidates:
        rows.append(
            {
                "expected_or_discovered_path": candidate["path"],
                "path_type": "toolbench_root_candidate",
                "exists": "true",
                "actual_path": candidate["path"],
                "is_directory": "true",
                "file_count": "",
                "total_size_bytes": "",
                "extensions_distribution_json": "{}",
                "readable": bool_text(path_readable(Path(candidate["path"]))),
                "notes": f"score={candidate['score']}; expected_path_hits={candidate['expected_path_hits']}; selected={candidate['selected']}",
            }
        )
    if toolbench_root is None:
        for relative in EXPECTED_RELATIVE_PATHS:
            rows.append(
                {
                    "expected_or_discovered_path": relative,
                    "path_type": "expected",
                    "exists": "false",
                    "actual_path": "",
                    "is_directory": "false",
                    "file_count": 0,
                    "total_size_bytes": 0,
                    "extensions_distribution_json": "{}",
                    "readable": "false",
                    "notes": "ToolBench root not found.",
                }
            )
        return rows, alternates

    for relative in EXPECTED_RELATIVE_PATHS:
        path = toolbench_root / relative
        exists = path.exists()
        file_count, total_size, distribution = file_stats(path) if exists else (0, 0, {})
        rows.append(
            {
                "expected_or_discovered_path": relative.replace("/", "\\"),
                "path_type": "expected",
                "exists": bool_text(exists),
                "actual_path": str(path.resolve()) if exists else str(path),
                "is_directory": bool_text(path.is_dir()),
                "file_count": file_count,
                "total_size_bytes": total_size,
                "extensions_distribution_json": json.dumps(distribution, ensure_ascii=False, sort_keys=True),
                "readable": bool_text(path_readable(path)) if exists else "false",
                "notes": "",
            }
        )
    expected_paths = {(toolbench_root / relative).resolve() for relative in EXPECTED_RELATIVE_PATHS if (toolbench_root / relative).exists()}
    alternates = [path for path in discover_alternate_trace_paths(toolbench_root) if path.resolve() not in expected_paths]
    for path in alternates:
        file_count, total_size, distribution = file_stats(path)
        rows.append(
            {
                "expected_or_discovered_path": str(path.relative_to(toolbench_root)),
                "path_type": "alternate_discovered",
                "exists": "true",
                "actual_path": str(path.resolve()),
                "is_directory": bool_text(path.is_dir()),
                "file_count": file_count,
                "total_size_bytes": total_size,
                "extensions_distribution_json": json.dumps(distribution, ensure_ascii=False, sort_keys=True),
                "readable": bool_text(path_readable(path)),
                "notes": "Discovered by name only; no evidence claim implied.",
            }
        )
    return rows, alternates


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit ToolBench composable trace paths, schemas, exact IDs, and ordered calls.")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--inventory", default="outputs/composable_recovery_inventory_v0_1/candidate_inventory.csv")
    parser.add_argument("--output-dir", default="outputs/toolbench_composable_trace_audit_v0_1")
    parser.add_argument("--fingerprint-files-per-area", type=int, default=20)
    parser.add_argument(
        "--reuse-static-artifacts",
        action="store_true",
        help="Reuse an existing path inventory, manifest, fingerprints, and ID index; rerun join and trace parsing only.",
    )
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    inventory_path = (project_root / args.inventory).resolve()
    output_dir = (project_root / args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if not inventory_path.is_file():
        raise SystemExit(f"Required composable inventory is missing: {inventory_path}")
    csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

    parse_errors: list[dict[str, Any]] = []
    prior_stage: dict[str, Any] = {}
    static_required = [
        output_dir / "toolbench_expected_path_inventory.csv",
        output_dir / "toolbench_trace_file_manifest.csv",
        output_dir / "toolbench_trace_schema_fingerprints.json",
        output_dir / "toolbench_trace_id_index.csv",
        output_dir / "audit_stage_summary.json",
    ]
    if args.reuse_static_artifacts:
        missing_static = [str(path) for path in static_required if not path.is_file()]
        if missing_static:
            raise SystemExit("Cannot reuse static artifacts; missing:\n- " + "\n- ".join(missing_static))
        prior_stage = json.loads((output_dir / "audit_stage_summary.json").read_text(encoding="utf-8"))
        toolbench_root = Path(prior_stage["toolbench_root_actual_path"])
        root_candidates = prior_stage.get("root_candidates", [])
        alternate_paths = [Path(path) for path in prior_stage.get("alternate_trace_paths", [])]
    else:
        toolbench_root, root_candidates = locate_toolbench_root(project_root)
        path_rows, alternate_paths = build_path_inventory(toolbench_root, root_candidates)
        write_csv(output_dir / "toolbench_expected_path_inventory.csv", PATH_INVENTORY_FIELDS, path_rows)

    if toolbench_root is None:
        stage_summary = {
            "generated_at": now_iso(),
            "toolbench_root_found": False,
            "toolbench_root_actual_path": "",
            "answer_path_exists": False,
            "reproduction_data_path_exists": False,
            "alternate_trace_paths_found_count": 0,
            "inventory_path": str(inventory_path),
        }
        (output_dir / "audit_stage_summary.json").write_text(json.dumps(stage_summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(stage_summary, ensure_ascii=False, indent=2))
        return 0

    answer_path = toolbench_root / "data" / "answer"
    reproduction_path = toolbench_root / "reproduction_data"
    if args.reuse_static_artifacts:
        fingerprints = json.loads((output_dir / "toolbench_trace_schema_fingerprints.json").read_text(encoding="utf-8"))
        with (output_dir / "toolbench_trace_id_index.csv").open("r", encoding="utf-8-sig", newline="") as handle:
            id_index_rows = list(csv.DictReader(handle))
        manifest_file_count = int(prior_stage.get("manifest_file_count", 0))
    else:
        manifest_rows, evidence_files = build_file_manifest(toolbench_root, answer_path, reproduction_path)
        manifest_file_count = len(manifest_rows)
        write_csv(output_dir / "toolbench_trace_file_manifest.csv", FILE_MANIFEST_FIELDS, manifest_rows)

        fingerprint_files = select_fingerprint_files(
            evidence_files,
            answer_path,
            reproduction_path,
            max(1, args.fingerprint_files_per_area),
        )
        fingerprints = [schema_fingerprint(path) for path in fingerprint_files]
        for fingerprint in fingerprints:
            if fingerprint["parse_status"] == "parse_failed":
                parse_errors.append(
                    {
                        "stage": "schema_fingerprint",
                        "source_file": fingerprint["source_file"],
                        "record_path": "$",
                        "inventory_row_id": "",
                        "error_type": "parse_failed",
                        "error_message": fingerprint["parse_error"],
                    }
                )
        (output_dir / "toolbench_trace_schema_fingerprints.json").write_text(
            json.dumps(fingerprints, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        id_index_rows = build_id_index(evidence_files, answer_path, reproduction_path, parse_errors)
        write_csv(output_dir / "toolbench_trace_id_index.csv", ID_INDEX_FIELDS, id_index_rows)
    index_lookup: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in id_index_rows:
        index_lookup[(text(row.get("source_group_if_available")).upper(), text(row.get("normalized_join_id")))].append(row)

    with inventory_path.open("r", encoding="utf-8-sig", newline="") as handle:
        inventory_rows = list(csv.DictReader(handle))

    join_rows: list[dict[str, Any]] = []
    step_summaries: list[dict[str, Any]] = []
    normalized_records: list[dict[str, Any]] = []
    unmatched_rows: list[dict[str, Any]] = []
    for inventory_position, inventory_row in enumerate(inventory_rows, start=1):
        inventory_id = text(inventory_row.get("inventory_id")) or f"ROW-{inventory_position:04d}"
        inventory_row["inventory_id"] = inventory_id
        status, join_type, selected_matches, ambiguity = resolve_join(inventory_row, index_lookup)
        selected = selected_matches[0] if status == "joined" else None
        all_candidate_count = len(selected_matches)
        join_row = {
            "inventory_row_id": inventory_id,
            "source_dataset": text(inventory_row.get("source_dataset")),
            "source_task_id": text(inventory_row.get("source_task_id")),
            "task_id": text(inventory_row.get("task_id")),
            "source_query_id": text(inventory_row.get("source_query_id")),
            "current_policy_status": text(inventory_row.get("current_policy_status")),
            "join_status": status,
            "join_type": join_type,
            "matched_source_file": text(selected.get("source_file")) if selected else "",
            "matched_record_path": text(selected.get("record_offset_or_json_path")) if selected else "",
            "candidate_match_count": all_candidate_count,
            "ambiguity_reason": ambiguity,
            "notes": "Exact joins only; data/answer is preferred over converted and other reproduction records." if status == "joined" else "",
        }
        join_rows.append(join_row)

        if status != "joined" or selected is None:
            unmatched_rows.append(join_row.copy())
            step_summaries.append(
                {
                    "inventory_row_id": inventory_id,
                    "source_task_id": text(inventory_row.get("task_id") or inventory_row.get("source_query_id")),
                    "joined": "false",
                    "ordered_steps_found": "false",
                    "step_count": 0,
                    "distinct_service_count": 0,
                    "distinct_api_count": 0,
                    "arguments_found": "false",
                    "outputs_found": "false",
                    "observations_found": "false",
                    "parse_status": "join_ambiguous" if status == "ambiguous" else "source_unavailable",
                    "parse_error": ambiguity,
                    "source_file": "",
                }
            )
            continue
        try:
            record = load_indexed_record(selected)
            normalized, step_summary = parse_trace_record(record, inventory_row, selected["source_file"])
            normalized_records.append(normalized)
            step_summaries.append(step_summary)
            if step_summary["parse_status"] != "ok":
                parse_errors.append(
                    {
                        "stage": "normalized_steps",
                        "source_file": selected["source_file"],
                        "record_path": selected["record_offset_or_json_path"],
                        "inventory_row_id": inventory_id,
                        "error_type": step_summary["parse_status"],
                        "error_message": step_summary["parse_error"],
                    }
                )
        except Exception as exc:
            step_summaries.append(
                {
                    "inventory_row_id": inventory_id,
                    "source_task_id": text(inventory_row.get("task_id") or inventory_row.get("source_query_id")),
                    "joined": "true",
                    "ordered_steps_found": "false",
                    "step_count": 0,
                    "distinct_service_count": 0,
                    "distinct_api_count": 0,
                    "arguments_found": "false",
                    "outputs_found": "false",
                    "observations_found": "false",
                    "parse_status": "parse_failed",
                    "parse_error": f"{type(exc).__name__}: {exc}",
                    "source_file": selected["source_file"],
                }
            )
            parse_errors.append(
                {
                    "stage": "normalized_steps",
                    "source_file": selected["source_file"],
                    "record_path": selected["record_offset_or_json_path"],
                    "inventory_row_id": inventory_id,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                }
            )

    write_csv(output_dir / "composable_candidate_trace_join_manifest.csv", JOIN_FIELDS, join_rows)
    write_csv(output_dir / "toolbench_composable_step_parse_summary.csv", STEP_SUMMARY_FIELDS, step_summaries)
    write_csv(output_dir / "parse_errors.csv", PARSE_ERROR_FIELDS, parse_errors)
    write_csv(output_dir / "unmatched_candidates.csv", JOIN_FIELDS, unmatched_rows)
    with (output_dir / "toolbench_composable_normalized_steps.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for record in normalized_records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    if args.reuse_static_artifacts:
        answer_count = int(prior_stage.get("answer_file_count", 0))
        answer_size = int(prior_stage.get("answer_total_size_bytes", 0))
        answer_extensions = prior_stage.get("answer_extensions_distribution", {})
        reproduction_count = int(prior_stage.get("reproduction_file_count", 0))
        reproduction_size = int(prior_stage.get("reproduction_total_size_bytes", 0))
        reproduction_extensions = prior_stage.get("reproduction_extensions_distribution", {})
    else:
        answer_count, answer_size, answer_extensions = file_stats(answer_path) if answer_path.exists() else (0, 0, {})
        reproduction_count, reproduction_size, reproduction_extensions = file_stats(reproduction_path) if reproduction_path.exists() else (0, 0, {})
    unique_keys = {
        (
            text(row.get("source_dataset")),
            text(row.get("source_group")),
            text(row.get("source_task_id") or row.get("source_query_id") or row.get("task_id")),
        )
        for row in inventory_rows
    }
    exact_joined = sum(row["join_status"] == "joined" for row in join_rows)
    ambiguous = sum(row["join_status"] == "ambiguous" for row in join_rows)
    unmatched = sum(row["join_status"] == "unmatched" for row in join_rows)
    stage_summary = {
        "generated_at": now_iso(),
        "project_root": str(project_root),
        "inventory_path": str(inventory_path),
        "toolbench_root_found": True,
        "toolbench_root_actual_path": str(toolbench_root),
        "root_candidates": root_candidates,
        "answer_path_exists": answer_path.is_dir(),
        "answer_path_actual": str(answer_path),
        "answer_file_count": answer_count,
        "answer_total_size_bytes": answer_size,
        "answer_extensions_distribution": answer_extensions,
        "reproduction_data_path_exists": reproduction_path.is_dir(),
        "reproduction_data_path_actual": str(reproduction_path),
        "reproduction_file_count": reproduction_count,
        "reproduction_total_size_bytes": reproduction_size,
        "reproduction_extensions_distribution": reproduction_extensions,
        "alternate_trace_paths_found_count": len(alternate_paths),
        "alternate_trace_paths": [str(path) for path in alternate_paths],
        "manifest_file_count": manifest_file_count,
        "fingerprint_file_count": len(fingerprints),
        "id_index_row_count": len(id_index_rows),
        "inventory_row_count": len(inventory_rows),
        "inventory_unique_count": len(unique_keys),
        "exact_joined_count": exact_joined,
        "ambiguous_join_count": ambiguous,
        "unmatched_count": unmatched,
        "join_rate": exact_joined / len(unique_keys) if unique_keys else 0.0,
        "ordered_steps_found_count": sum(row["ordered_steps_found"] == "true" for row in step_summaries),
        "arguments_found_count": sum(row["arguments_found"] == "true" for row in step_summaries),
        "outputs_found_count": sum(row["outputs_found"] == "true" for row in step_summaries),
        "observations_found_count": sum(row["observations_found"] == "true" for row in step_summaries),
        "parse_failed_count": sum(row["parse_status"] not in {"ok", "source_unavailable", "join_ambiguous"} for row in step_summaries),
        "parse_error_record_count": len(parse_errors),
        "source_files_modified": False,
        "automatic_composable_labels_generated": False,
    }
    (output_dir / "audit_stage_summary.json").write_text(
        json.dumps(stage_summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(stage_summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
