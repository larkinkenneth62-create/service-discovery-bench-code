#!/usr/bin/env python
"""Corpus-wide objective dependency mining for ServiceDiscoveryBench.

This benchmark-only stage reconciles the existing 322-row composable inventory,
performs one conservative exact-join recovery pass, streams a lightweight index
over local ToolBench traces, mines execution-grounded dependencies for eligible
multi-call records, and prepares one human review pack. It never assigns final
composable labels and never modifies source data or existing human judgments.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import sys
import time
import unicodedata
from collections import Counter, defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from audit_toolbench_composable_trace_availability_v0_1 import (  # noqa: E402
    deduplicate_index_matches,
    extract_group,
    extract_numeric_filename_id,
    load_indexed_record,
    load_json_file,
    normalize_id,
    parse_trace_record,
    resolve_join,
    source_priority,
)
from extract_toolbench_composable_objective_evidence_v0_1 import (  # noqa: E402
    extract_dependency_edges,
    iter_scalars,
    normalize_scalar,
)


VERSION = "v0.2"
CANONICAL_STATUSES = (
    "strong_objective_evidence_available",
    "partial_objective_evidence",
    "sequence_only",
    "no_dependency_evidence",
    "source_unavailable",
    "join_ambiguous",
    "parse_failed",
)
STATUS_ALIASES = {
    "strong_objective_evidence_available": "strong_objective_evidence_available",
    "partial_objective_evidence": "partial_objective_evidence",
    "sequence_only": "sequence_only",
    "no_dependency_evidence": "no_dependency_evidence",
    "source_unavailable": "source_unavailable",
    "join_ambiguous": "join_ambiguous",
    "parse_failed": "parse_failed",
}
STATUS_PRIORITY = {
    "strong_objective_evidence_available": 5,
    "partial_objective_evidence": 4,
    "sequence_only": 3,
    "no_dependency_evidence": 2,
    "parse_failed": 1,
}
HUMAN_FIELDS = [
    "dependency_edge_valid",
    "dependency_type_final",
    "dependency_evidence_sufficient",
    "composition_final_label",
    "service_level_valid",
    "api_level_valid",
    "adjudicator_id",
    "adjudicator_type",
    "adjudicated_at",
    "adjudication_notes",
]
INDEX_FIELDS = [
    "trace_record_id",
    "source_dataset",
    "source_area",
    "source_file",
    "source_record_path",
    "record_id",
    "instruction_query_id",
    "source_task_id",
    "source_group",
    "query_text",
    "ordered_call_count",
    "distinct_service_count",
    "distinct_api_count",
    "arguments_available",
    "outputs_available",
    "observations_available",
    "parse_status",
    "parse_error",
    "pass2_eligible",
]


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def truthy(value: Any) -> bool:
    return text(value).casefold() in {"1", "true", "yes"}


def bool_text(value: bool) -> str:
    return "true" if value else "false"


def int_value(value: Any) -> int:
    try:
        return int(text(value) or 0)
    except ValueError:
        return 0


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def iter_csv(path: Path) -> Iterator[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        yield from csv.DictReader(handle)


def write_csv(path: Path, fields: list[str], rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def append_jsonl(handle: Any, value: dict[str, Any]) -> None:
    handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")


def require(path: Path) -> Path:
    if not path.exists():
        raise FileNotFoundError(f"Required input does not exist: {path}")
    return path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_fixed_task_id(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", text(value)).casefold()
    normalized = re.sub(r"^(?:toolbench|stabletoolbench)[_:\-]+", "", normalized)
    return normalized


def reconcile_current_322(project_root: Path, output_dir: Path) -> dict[str, Any]:
    inventory_path = require(project_root / "outputs/composable_recovery_inventory_v0_1/candidate_inventory.csv")
    join_path = require(project_root / "outputs/toolbench_composable_trace_audit_v0_1/composable_candidate_trace_join_manifest.csv")
    status_path = require(project_root / "outputs/toolbench_composable_trace_audit_v0_1/toolbench_composable_evidence_status.csv")
    inventory = read_csv(inventory_path)
    joins = {row["inventory_row_id"]: row for row in read_csv(join_path)}
    statuses = {row["inventory_row_id"]: row for row in read_csv(status_path)}
    rows: list[dict[str, Any]] = []
    unaccounted: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    by_source: dict[str, Counter[str]] = defaultdict(Counter)
    for item in inventory:
        inventory_id = text(item.get("inventory_id"))
        status_row = statuses.get(inventory_id, {})
        raw_status = text(status_row.get("evidence_availability_status"))
        canonical = STATUS_ALIASES.get(raw_status, "")
        join_row = joins.get(inventory_id, {})
        row = {
            "inventory_row_id": inventory_id,
            "source_dataset": text(item.get("source_dataset")),
            "source_group": text(item.get("source_group")),
            "task_id": text(item.get("task_id")),
            "source_query_id": text(item.get("source_query_id")),
            "original_evidence_status": raw_status,
            "exclusive_evidence_status": canonical,
            "join_status": text(join_row.get("join_status")),
            "parse_or_join_note": text(join_row.get("ambiguity_reason")) or text(status_row.get("evidence_notes")),
            "accounted_in_exclusive_status_sum": bool_text(bool(canonical)),
        }
        rows.append(row)
        if canonical:
            counts[canonical] += 1
            by_source[row["source_dataset"]][canonical] += 1
        else:
            unaccounted.append(row)
    fields = list(rows[0]) if rows else []
    write_csv(output_dir / "current_322_status_reconciliation.csv", fields, rows)
    write_csv(output_dir / "current_322_unaccounted_rows.csv", fields, unaccounted)
    join_rows = read_csv(join_path)
    summary = {
        "generated_at": now_iso(),
        "script_version": VERSION,
        "inputs": {
            "inventory": str(inventory_path.resolve()),
            "trace_join_manifest": str(join_path.resolve()),
            "evidence_status": str(status_path.resolve()),
        },
        "inventory_unique_count": len({row["inventory_id"] for row in inventory}),
        "exact_joined_count": sum(text(row.get("join_status")) == "joined" for row in join_rows),
        "status_distribution": {status: counts[status] for status in CANONICAL_STATUSES},
        "status_by_source_dataset": {
            source: {status: values[status] for status in CANONICAL_STATUSES}
            for source, values in sorted(by_source.items())
        },
        "status_sum": sum(counts.values()),
        "status_sum_matches_inventory": sum(counts.values()) == len(inventory),
        "unaccounted_count": len(unaccounted),
    }
    write_json(output_dir / "current_322_status_reconciliation.json", summary)
    return summary


def build_index_lookup(index_rows: Iterable[dict[str, str]]) -> dict[tuple[str, str], list[dict[str, str]]]:
    lookup: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in index_rows:
        group = text(row.get("source_group_if_available")).upper()
        normalized = normalize_id(row.get("normalized_join_id"))
        if normalized:
            lookup[(group, normalized)].append(row)
    return lookup


def recover_once(project_root: Path, output_dir: Path) -> dict[str, Any]:
    summary_path = output_dir / "one_pass_join_recovery_summary.json"
    if summary_path.exists():
        with summary_path.open("r", encoding="utf-8") as handle:
            existing = json.load(handle)
        if existing.get("one_pass_completed") is True:
            print("One-pass join recovery already completed; reusing locked result.", flush=True)
            return existing
    reconciliation = read_csv(require(output_dir / "current_322_status_reconciliation.csv"))
    inventory_rows = read_csv(require(project_root / "outputs/composable_recovery_inventory_v0_1/candidate_inventory.csv"))
    inventory_by_id = {row["inventory_id"]: row for row in inventory_rows}
    index_path = require(project_root / "outputs/toolbench_composable_trace_audit_v0_1/toolbench_trace_id_index.csv")
    lookup = build_index_lookup(iter_csv(index_path))
    targets = [
        row for row in reconciliation
        if row["exclusive_evidence_status"] in {"source_unavailable", "join_ambiguous"}
    ]
    manifest: list[dict[str, Any]] = []
    join_keys: Counter[str] = Counter()
    source_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for target in targets:
        item = inventory_by_id[target["inventory_row_id"]]
        status, join_type, matches, reason = resolve_join(item, lookup)
        recovered_status = ""
        normalized_steps: dict[str, Any] = {}
        edges: list[dict[str, Any]] = []
        parse_status = "not_parsed"
        if status == "joined" and len(matches) == 1:
            match = matches[0]
            try:
                record = load_indexed_record(match)
                normalized_steps, parse_summary = parse_trace_record(record, item, match["source_file"])
                parse_status = text(parse_summary.get("parse_status"))
                if parse_status == "ok":
                    edges = extract_dependency_edges(normalized_steps)
                    recovered_status = classify_deep_record(parse_summary, edges)
                else:
                    recovered_status = "parse_failed"
            except Exception as exc:
                parse_status = "parse_failed"
                recovered_status = "parse_failed"
                reason = f"{type(exc).__name__}: {exc}"
        final_recovery_status = (
            "newly_exact_joined" if status == "joined"
            else "still_ambiguous" if status == "ambiguous"
            else "source_unavailable_hold"
        )
        if status == "joined":
            join_keys[join_type] += 1
        source_counts[target["source_dataset"]][final_recovery_status] += 1
        manifest.append(
            {
                "inventory_row_id": target["inventory_row_id"],
                "source_dataset": target["source_dataset"],
                "source_group": target["source_group"],
                "task_id": target["task_id"],
                "source_query_id": target["source_query_id"],
                "input_status": target["exclusive_evidence_status"],
                "recovery_status": final_recovery_status,
                "exact_join_type": join_type,
                "matched_source_file": text(matches[0].get("source_file")) if len(matches) == 1 else "",
                "matched_record_path": text(matches[0].get("record_offset_or_json_path")) if len(matches) == 1 else "",
                "exact_candidate_count": len(matches),
                "candidate_paths_json": json_text([
                    {"source_file": m.get("source_file", ""), "record_path": m.get("record_offset_or_json_path", "")}
                    for m in matches
                ]),
                "normalization_applied": "trim+NFKC+casefold+recorded ToolBench/StableToolBench fixed-prefix removal",
                "ambiguity_or_failure_reason": reason,
                "parse_status": parse_status,
                "recovered_evidence_status": recovered_status,
                "normalized_steps_json": json_text(normalized_steps) if normalized_steps else "",
                "dependency_edges_json": json_text(edges) if edges else "",
            }
        )
    fields = list(manifest[0]) if manifest else []
    write_csv(output_dir / "one_pass_join_recovery_manifest.csv", fields, manifest)
    summary = {
        "generated_at": now_iso(),
        "script_version": VERSION,
        "one_pass_completed": True,
        "recovery_input_count": len(targets),
        "newly_exact_joined_count": sum(row["recovery_status"] == "newly_exact_joined" for row in manifest),
        "still_unavailable_count": sum(row["recovery_status"] == "source_unavailable_hold" for row in manifest),
        "still_ambiguous_count": sum(row["recovery_status"] == "still_ambiguous" for row in manifest),
        "join_key_distribution": dict(sorted(join_keys.items())),
        "source_dataset_distribution": {
            source: dict(sorted(values.items())) for source, values in sorted(source_counts.items())
        },
        "allowed_normalization": ["trim", "NFKC", "casefold", "fixed dataset prefix removal"],
        "forbidden_methods_used": [],
        "remaining_unavailable_route": "source_unavailable_hold",
    }
    write_json(summary_path, summary)
    return summary


def iter_trace_files(answer_root: Path, reproduction_root: Path) -> list[Path]:
    files: list[Path] = []
    for root in (answer_root, reproduction_root):
        for current, directories, names in os.walk(root):
            directories[:] = sorted(name for name in directories if name != "__MACOSX")
            for name in sorted(names):
                if name.casefold().endswith(".json"):
                    files.append((Path(current) / name).resolve())
    return sorted(files, key=lambda path: str(path).casefold())


def source_area(path: Path, answer_root: Path, reproduction_root: Path) -> str:
    try:
        path.relative_to(answer_root)
        return "data_answer"
    except ValueError:
        pass
    rel = path.relative_to(reproduction_root)
    if rel.parts and rel.parts[0].casefold() == "model_predictions_converted":
        return "reproduction_converted"
    if path.name.casefold() == "human_cross_annotated_data.json":
        return "reproduction_human_cross_annotated"
    return "reproduction_model_predictions"


def query_from_record(record: Any) -> str:
    if not isinstance(record, dict):
        return ""
    if isinstance(record.get("answer_generation"), dict):
        return text(record["answer_generation"].get("query"))
    return text(record.get("query"))


def records_from_payload(path: Path, payload: Any, area: str) -> list[tuple[str, str, Any]]:
    """Return (record_id, JSON path, record) without semantic matching."""
    if area == "reproduction_converted" and isinstance(payload, dict):
        return [(text(key), f'$["{key}"]', value) for key, value in payload.items()]
    if area == "reproduction_human_cross_annotated" and isinstance(payload, list):
        rows: list[tuple[str, str, Any]] = []
        for index, item in enumerate(payload):
            if not isinstance(item, dict):
                rows.append((f"human_cross_{index}", f"$[{index}]", item))
                continue
            answers = item.get("answers")
            if isinstance(answers, list):
                for answer_index, answer in enumerate(answers):
                    method = text(answer.get("method")) if isinstance(answer, dict) else ""
                    record = {"query": item.get("query", ""), "answer": answer}
                    rows.append(
                        (
                            f"human_cross_{index}_{answer_index}_{method}",
                            f"$[{index}].answers[{answer_index}]",
                            record,
                        )
                    )
            else:
                rows.append((f"human_cross_{index}", f"$[{index}]", item))
        return rows
    return [(path.stem, "$", payload)]


def process_index_file(path_string: str, answer_root_string: str, reproduction_root_string: str) -> list[dict[str, Any]]:
    path = Path(path_string)
    answer_root = Path(answer_root_string)
    reproduction_root = Path(reproduction_root_string)
    area = source_area(path, answer_root, reproduction_root)
    group = extract_group(str(path))
    base_instruction_id = extract_numeric_filename_id(path)
    try:
        payload = load_json_file(path)
        records = records_from_payload(path, payload, area)
    except Exception as exc:
        record_id = path.stem
        source_task_id = f"ToolBench_{group}_{base_instruction_id}" if group and base_instruction_id else f"ToolBench_{record_id}"
        return [{
            "trace_record_id": hashlib.sha1(f"{path}|$".encode("utf-8")).hexdigest(),
            "source_dataset": "ToolBench",
            "source_area": area,
            "source_file": str(path),
            "source_record_path": "$",
            "record_id": record_id,
            "instruction_query_id": base_instruction_id,
            "source_task_id": source_task_id,
            "source_group": group,
            "query_text": "",
            "ordered_call_count": 0,
            "distinct_service_count": 0,
            "distinct_api_count": 0,
            "arguments_available": "false",
            "outputs_available": "false",
            "observations_available": "false",
            "parse_status": "parse_failed",
            "parse_error": f"{type(exc).__name__}: {exc}",
            "pass2_eligible": "false",
        }]
    result: list[dict[str, Any]] = []
    for record_id, record_path, record in records:
        instruction_id = record_id if area == "reproduction_converted" else base_instruction_id
        if not instruction_id and re.fullmatch(r"\d+", record_id):
            instruction_id = record_id
        source_task_id = (
            f"ToolBench_{group}_{instruction_id}" if group and instruction_id
            else f"ToolBench_{group}_{record_id}" if group
            else f"ToolBench_{record_id}"
        )
        pseudo = {
            "inventory_id": hashlib.sha1(f"{path}|{record_path}".encode("utf-8")).hexdigest(),
            "task_id": source_task_id,
            "source_task_id": source_task_id,
            "source_query_id": instruction_id,
            "query_text": query_from_record(record),
            "candidate_apis_json": "[]",
        }
        try:
            normalized, summary = parse_trace_record(record, pseudo, str(path))
            services = {text(step.get("service_name")) for step in normalized.get("steps", []) if text(step.get("service_name"))}
            apis = {text(step.get("api_name")) for step in normalized.get("steps", []) if text(step.get("api_name"))}
            calls = int_value(summary.get("step_count"))
            args = truthy(summary.get("arguments_found"))
            outputs = truthy(summary.get("outputs_found"))
            observations = truthy(summary.get("observations_found"))
            parse_status = text(summary.get("parse_status"))
            eligible = parse_status == "ok" and calls >= 2 and len(services) >= 2 and args and (outputs or observations)
            parse_error = text(summary.get("parse_error"))
            query_text = text(normalized.get("query_text"))
        except Exception as exc:
            calls, services, apis = 0, set(), set()
            args = outputs = observations = eligible = False
            parse_status = "parse_failed"
            parse_error = f"{type(exc).__name__}: {exc}"
            query_text = query_from_record(record)
        result.append({
            "trace_record_id": hashlib.sha1(f"{path}|{record_path}".encode("utf-8")).hexdigest(),
            "source_dataset": "ToolBench",
            "source_area": area,
            "source_file": str(path),
            "source_record_path": record_path,
            "record_id": record_id,
            "instruction_query_id": instruction_id,
            "source_task_id": source_task_id,
            "source_group": group,
            "query_text": query_text,
            "ordered_call_count": calls,
            "distinct_service_count": len(services),
            "distinct_api_count": len(apis),
            "arguments_available": bool_text(args),
            "outputs_available": bool_text(outputs),
            "observations_available": bool_text(observations),
            "parse_status": parse_status,
            "parse_error": parse_error,
            "pass2_eligible": bool_text(eligible),
        })
    return result


def counter_snapshot(rows: Iterable[dict[str, Any]], counters: dict[str, Counter[str]]) -> None:
    for row in rows:
        counters["parse_status"][text(row.get("parse_status"))] += 1
        counters["source_area"][text(row.get("source_area"))] += 1
        counters["source_group"][text(row.get("source_group")) or "unknown"] += 1
        counters["totals"]["records"] += 1
        if int_value(row.get("ordered_call_count")) >= 2:
            counters["totals"]["multicall"] += 1
        if int_value(row.get("distinct_service_count")) >= 2:
            counters["totals"]["multiservice"] += 1
        if truthy(row.get("pass2_eligible")):
            counters["totals"]["pass2_eligible"] += 1


def serialize_counters(counters: dict[str, Counter[str]]) -> dict[str, dict[str, int]]:
    return {name: dict(values) for name, values in counters.items()}


def deserialize_counters(value: dict[str, dict[str, int]]) -> dict[str, Counter[str]]:
    result: dict[str, Counter[str]] = defaultdict(Counter)
    for name, values in value.items():
        result[name].update(values)
    return result


def build_full_index(
    project_root: Path,
    output_dir: Path,
    *,
    workers: int,
    progress_every: int,
) -> dict[str, Any]:
    summary_path = output_dir / "toolbench_full_trace_index_summary.json"
    if summary_path.exists():
        with summary_path.open("r", encoding="utf-8") as handle:
            existing = json.load(handle)
        if existing.get("completed") is True:
            print("Full trace index already completed; reusing result.", flush=True)
            return existing
    answer_root = require(project_root / "external_sources/ToolBench/data/answer").resolve()
    reproduction_root = require(project_root / "external_sources/ToolBench/reproduction_data").resolve()
    files = iter_trace_files(answer_root, reproduction_root)
    output_path = output_dir / "toolbench_full_trace_lightweight_index.csv"
    checkpoint_path = output_dir / ".toolbench_full_trace_index_checkpoint.json"
    output_dir.mkdir(parents=True, exist_ok=True)
    next_file = 0
    counters: dict[str, Counter[str]] = defaultdict(Counter)
    mode = "w"
    if checkpoint_path.exists():
        with checkpoint_path.open("r", encoding="utf-8") as handle:
            checkpoint = json.load(handle)
        if checkpoint.get("source_file_count") != len(files):
            raise RuntimeError("Trace file inventory changed since checkpoint; refusing unsafe resume.")
        next_file = int(checkpoint.get("next_file_index", 0))
        counters = deserialize_counters(checkpoint.get("counters", {}))
        if not output_path.exists():
            raise FileNotFoundError("Checkpoint exists but index CSV is missing.")
        with output_path.open("r+", encoding="utf-8-sig", newline="") as handle:
            handle.seek(int(checkpoint.get("output_offset", 0)))
            handle.truncate()
        mode = "a"
        print(f"Resuming index at file {next_file}/{len(files)}", flush=True)
    elif output_path.exists():
        raise FileExistsError(f"Index output exists without a checkpoint or completed summary: {output_path}")
    started = time.time()
    with output_path.open(mode, encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=INDEX_FIELDS, extrasaction="ignore")
        if mode == "w":
            writer.writeheader()
            handle.flush()
        with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
            batch_size = max(8, workers * 4)
            for batch_start in range(next_file, len(files), batch_size):
                batch = files[batch_start : batch_start + batch_size]
                results = executor.map(
                    process_index_file,
                    (str(path) for path in batch),
                    (str(answer_root) for _ in batch),
                    (str(reproduction_root) for _ in batch),
                )
                for offset, file_rows in enumerate(results):
                    writer.writerows(file_rows)
                    counter_snapshot(file_rows, counters)
                    handle.flush()
                    completed_index = batch_start + offset + 1
                    checkpoint = {
                        "generated_at": now_iso(),
                        "source_file_count": len(files),
                        "next_file_index": completed_index,
                        "output_offset": handle.tell(),
                        "counters": serialize_counters(counters),
                    }
                    write_json(checkpoint_path, checkpoint)
                    if completed_index % progress_every == 0 or completed_index == len(files):
                        elapsed = time.time() - started
                        print(
                            f"index {completed_index}/{len(files)} files; "
                            f"records={counters['totals']['records']} pass2={counters['totals']['pass2_eligible']} "
                            f"elapsed={elapsed:.1f}s",
                            flush=True,
                        )
    summary = {
        "generated_at": now_iso(),
        "script_version": VERSION,
        "completed": True,
        "answer_path": str(answer_root),
        "reproduction_path": str(reproduction_root),
        "source_file_count": len(files),
        "trace_index_record_count": counters["totals"]["records"],
        "multicall_record_count": counters["totals"]["multicall"],
        "multiservice_record_count": counters["totals"]["multiservice"],
        "pass2_eligible_record_count": counters["totals"]["pass2_eligible"],
        "parse_status_distribution": dict(sorted(counters["parse_status"].items())),
        "source_area_distribution": dict(sorted(counters["source_area"].items())),
        "source_group_distribution": dict(sorted(counters["source_group"].items())),
        "streaming": True,
        "checkpoint_resume_supported": True,
        "network_used": False,
        "binary_or_pickle_executed": False,
        "elapsed_seconds_this_invocation": round(time.time() - started, 3),
    }
    write_json(summary_path, summary)
    return summary


def load_record_at_path(source_file: Path, record_path: str) -> Any:
    payload = load_json_file(source_file)
    if record_path == "$":
        return payload
    object_match = re.fullmatch(r'\$\["(.*)"\]', record_path)
    if object_match:
        return payload[object_match.group(1)]
    human_match = re.fullmatch(r"\$\[(\d+)\]\.answers\[(\d+)\]", record_path)
    if human_match:
        item = payload[int(human_match.group(1))]
        answer = item["answers"][int(human_match.group(2))]
        return {"query": item.get("query", ""), "answer": answer}
    list_match = re.fullmatch(r"\$\[(\d+)\]", record_path)
    if list_match:
        return payload[int(list_match.group(1))]
    raise ValueError(f"Unsupported exact record path: {record_path}")


def classify_deep_record(summary: dict[str, Any], edges: list[dict[str, Any]]) -> str:
    if text(summary.get("parse_status")) != "ok":
        return "parse_failed"
    non_sequence = [
        edge for edge in edges
        if edge.get("dependency_type") not in {"sequence_only", "none"}
        and not bool(edge.get("query_known_value_filtered"))
    ]
    complete_edges = [
        edge for edge in non_sequence
        if text(edge.get("upstream_source_path"))
        and text(edge.get("downstream_source_path"))
        and text(edge.get("source_file"))
    ]
    cross_scope = int_value(summary.get("distinct_service_count")) >= 2 or int_value(summary.get("distinct_api_count")) >= 2
    if complete_edges and int_value(summary.get("step_count")) >= 2 and cross_scope:
        return "strong_objective_evidence_available"
    if non_sequence:
        return "partial_objective_evidence"
    if int_value(summary.get("step_count")) >= 2:
        return "sequence_only"
    return "no_dependency_evidence"


def mine_dependencies(project_root: Path, output_dir: Path, *, common_value_threshold: int) -> dict[str, Any]:
    summary_path = output_dir / "toolbench_full_dependency_mining_summary.json"
    if summary_path.exists():
        with summary_path.open("r", encoding="utf-8") as handle:
            existing = json.load(handle)
        if existing.get("completed") is True:
            print("Full dependency mining already completed; reusing result.", flush=True)
            return existing
    index_path = require(output_dir / "toolbench_full_trace_lightweight_index.csv")
    normalized_path = output_dir / "toolbench_full_normalized_multicall_steps.jsonl"
    edge_path = output_dir / "toolbench_full_dependency_edge_candidates.jsonl"
    status_path = output_dir / "toolbench_full_dependency_evidence_status.csv"
    normalized_part = normalized_path.with_suffix(".jsonl.part")
    edge_part = edge_path.with_suffix(".jsonl.part")
    status_part = status_path.with_suffix(".csv.part")
    argument_frequency: Counter[str] = Counter()
    deep_count = 0
    deep_parse_failed = 0
    started = time.time()
    with normalized_part.open("w", encoding="utf-8") as normalized_handle:
        for row in iter_csv(index_path):
            if not truthy(row.get("pass2_eligible")):
                continue
            pseudo = {
                "inventory_id": row["trace_record_id"],
                "task_id": row["source_task_id"],
                "source_task_id": row["source_task_id"],
                "source_query_id": row["instruction_query_id"],
                "query_text": row["query_text"],
                "candidate_apis_json": "[]",
            }
            try:
                record = load_record_at_path(Path(row["source_file"]), row["source_record_path"])
                normalized, parse_summary = parse_trace_record(record, pseudo, row["source_file"])
            except Exception as exc:
                normalized = {"inventory_row_id": row["trace_record_id"], "source_task_id": row["source_task_id"], "query_text": row["query_text"], "steps": []}
                parse_summary = {
                    "parse_status": "parse_failed",
                    "parse_error": f"{type(exc).__name__}: {exc}",
                    "step_count": 0,
                    "distinct_service_count": 0,
                    "distinct_api_count": 0,
                    "arguments_found": "false",
                    "outputs_found": "false",
                    "observations_found": "false",
                }
            normalized.update({
                "trace_record_id": row["trace_record_id"],
                "source_dataset": "ToolBench",
                "source_area": row["source_area"],
                "source_group": row["source_group"],
                "instruction_query_id": row["instruction_query_id"],
                "source_record_path": row["source_record_path"],
                "source_file": row["source_file"],
                "parse_summary": parse_summary,
            })
            append_jsonl(normalized_handle, normalized)
            deep_count += 1
            if text(parse_summary.get("parse_status")) != "ok":
                deep_parse_failed += 1
            task_argument_values: set[str] = set()
            for step_index, step in enumerate(normalized.get("steps", []), start=1):
                if not isinstance(step, dict):
                    continue
                for _, value in iter_scalars(step.get("arguments"), f"steps[{step_index}].arguments"):
                    scalar = normalize_scalar(value)
                    if scalar and len(scalar) <= 512:
                        task_argument_values.add(scalar)
            argument_frequency.update(task_argument_values)
            if deep_count % 1000 == 0:
                normalized_handle.flush()
                print(f"deep parse {deep_count} pass2 records; elapsed={time.time()-started:.1f}s", flush=True)
    normalized_part.replace(normalized_path)
    status_fields = [
        "trace_record_id", "source_dataset", "source_area", "source_group", "source_task_id",
        "instruction_query_id", "query_text", "source_file", "source_record_path", "step_count",
        "distinct_service_count", "distinct_api_count", "arguments_available", "outputs_available",
        "observations_available", "query_known_edges_filtered", "strong_edge_count", "partial_edge_count",
        "data_flow_edge_count", "entity_flow_edge_count", "control_flow_edge_count",
        "conditional_flow_edge_count", "sequence_edge_count", "evidence_status", "parse_status", "parse_error",
    ]
    status_counts: Counter[str] = Counter()
    edge_type_counts: Counter[str] = Counter()
    edge_total = 0
    query_known_total = 0
    with normalized_path.open("r", encoding="utf-8") as normalized_handle, edge_part.open("w", encoding="utf-8") as edge_handle, status_part.open("w", encoding="utf-8-sig", newline="") as status_handle:
        writer = csv.DictWriter(status_handle, fieldnames=status_fields)
        writer.writeheader()
        for line_number, line in enumerate(normalized_handle, start=1):
            if not line.strip():
                continue
            record = json.loads(line)
            summary = record.get("parse_summary", {})
            edges = extract_dependency_edges(
                record,
                common_value_frequency=argument_frequency,
                common_value_threshold=common_value_threshold,
            ) if text(summary.get("parse_status")) == "ok" else []
            for edge in edges:
                edge.update({
                    "trace_record_id": record["trace_record_id"],
                    "source_dataset": "ToolBench",
                    "source_group": record.get("source_group", ""),
                    "instruction_query_id": record.get("instruction_query_id", ""),
                    "source_record_path": record.get("source_record_path", ""),
                })
                append_jsonl(edge_handle, edge)
            nonsequence = [e for e in edges if e.get("dependency_type") not in {"sequence_only", "none"} and not e.get("query_known_value_filtered")]
            query_known = [e for e in edges if e.get("query_known_value_filtered")]
            complete = [e for e in nonsequence if text(e.get("upstream_source_path")) and text(e.get("downstream_source_path")) and text(e.get("source_file"))]
            evidence_status = classify_deep_record(summary, edges)
            status_counts[evidence_status] += 1
            edge_total += len(edges)
            query_known_total += len(query_known)
            edge_types = Counter(text(e.get("dependency_type")) for e in edges)
            edge_type_counts.update(edge_types)
            writer.writerow({
                "trace_record_id": record["trace_record_id"],
                "source_dataset": "ToolBench",
                "source_area": record.get("source_area", ""),
                "source_group": record.get("source_group", ""),
                "source_task_id": record.get("source_task_id", ""),
                "instruction_query_id": record.get("instruction_query_id", ""),
                "query_text": record.get("query_text", ""),
                "source_file": record.get("source_file", ""),
                "source_record_path": record.get("source_record_path", ""),
                "step_count": summary.get("step_count", 0),
                "distinct_service_count": summary.get("distinct_service_count", 0),
                "distinct_api_count": summary.get("distinct_api_count", 0),
                "arguments_available": summary.get("arguments_found", "false"),
                "outputs_available": summary.get("outputs_found", "false"),
                "observations_available": summary.get("observations_found", "false"),
                "query_known_edges_filtered": len(query_known),
                "strong_edge_count": len(complete),
                "partial_edge_count": max(0, len(nonsequence) - len(complete)),
                "data_flow_edge_count": edge_types["data_flow"],
                "entity_flow_edge_count": edge_types["entity_flow"],
                "control_flow_edge_count": edge_types["control_flow"],
                "conditional_flow_edge_count": edge_types["conditional_flow"],
                "sequence_edge_count": edge_types["sequence_only"],
                "evidence_status": evidence_status,
                "parse_status": summary.get("parse_status", ""),
                "parse_error": summary.get("parse_error", ""),
            })
            if line_number % 1000 == 0:
                edge_handle.flush()
                status_handle.flush()
    edge_part.replace(edge_path)
    status_part.replace(status_path)
    summary = {
        "generated_at": now_iso(),
        "script_version": VERSION,
        "completed": True,
        "deep_parsed_record_count": deep_count,
        "deep_parse_failed_count": deep_parse_failed,
        "dependency_edge_candidate_count": edge_total,
        "query_known_edge_filtered_count": query_known_total,
        "evidence_status_distribution": {status: status_counts[status] for status in STATUS_PRIORITY},
        "dependency_type_distribution": dict(sorted(edge_type_counts.items())),
        "common_argument_value_filter_threshold": common_value_threshold,
        "unique_argument_scalar_count": len(argument_frequency),
        "automatic_final_composable_labels_generated": False,
        "llm_or_semantic_inference_used": False,
        "elapsed_seconds": round(time.time() - started, 3),
    }
    write_json(summary_path, summary)
    return summary


def read_jsonl_by_key(path: Path, key: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                result[text(row.get(key))] = row
    return result


def read_selected_jsonl_by_key(path: Path, key: str, allowed: set[str]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    if not allowed:
        return result
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            row_key = text(row.get(key))
            if row_key in allowed:
                result[row_key] = row
    return result


def edges_by_trace(path: Path) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                result[text(row.get("trace_record_id"))].append(row)
    return result


def selected_edges_by_trace(path: Path, allowed: set[str]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if not allowed:
        return result
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            trace_id = text(row.get("trace_record_id"))
            if trace_id in allowed:
                result[trace_id].append(row)
    return result


def evidence_score(row: dict[str, Any]) -> int:
    score = 0
    score += int_value(row.get("entity_flow_edge_count")) * 120
    score += int_value(row.get("data_flow_edge_count")) * 100
    score += int_value(row.get("control_flow_edge_count")) * 80
    score += int_value(row.get("conditional_flow_edge_count")) * 80
    score += min(int_value(row.get("strong_edge_count")), 5) * 20
    score += 20 if truthy(row.get("arguments_available")) and (truthy(row.get("outputs_available")) or truthy(row.get("observations_available"))) else 0
    score += 10 if text(row.get("source_file")) and text(row.get("source_record_path")) else 0
    score -= int_value(row.get("partial_edge_count")) * 5
    return score


def task_key(row: dict[str, Any]) -> str:
    source = text(row.get("source_dataset")) or "ToolBench"
    for field in ("source_task_id", "instruction_query_id", "source_query_id"):
        value = text(row.get(field))
        if value:
            return f"{source}|{value}"
    return f"{source}|trace:{text(row.get('trace_record_id'))}"


def parse_json_list(value: Any) -> list[Any]:
    try:
        parsed = json.loads(text(value) or "[]")
        return parsed if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        return []


def build_ranked_and_review(project_root: Path, output_dir: Path) -> dict[str, Any]:
    status_rows = read_csv(require(output_dir / "toolbench_full_dependency_evidence_status.csv"))
    inventory_rows = read_csv(require(project_root / "outputs/composable_recovery_inventory_v0_1/candidate_inventory.csv"))
    inventory_by_task = {row["task_id"]: row for row in inventory_rows}
    current_members = set(inventory_by_task)
    crosswalk_path = project_root / "outputs/composable_recovery_preparation_v0_1/composable_current_review_crosswalk.csv"
    current_review = set()
    if crosswalk_path.exists():
        current_review = {row["source_task_id"] for row in read_csv(crosswalk_path) if truthy(row.get("in_current_v0_4_2_review_pack"))}
    best: dict[str, dict[str, Any]] = {}
    trace_variant_counts: Counter[str] = Counter()
    for row in status_rows:
        row["source_dataset"] = "ToolBench"
        key = task_key(row)
        trace_variant_counts[key] += 1
        row["evidence_score"] = evidence_score(row)
        prior = best.get(key)
        candidate_sort = (STATUS_PRIORITY.get(row["evidence_status"], 0), row["evidence_score"], int_value(row.get("strong_edge_count")), -source_priority(row.get("source_file", "")))
        prior_sort = (-1, -1, -1, -9) if prior is None else (STATUS_PRIORITY.get(prior["evidence_status"], 0), int_value(prior["evidence_score"]), int_value(prior.get("strong_edge_count")), -source_priority(prior.get("source_file", "")))
        if prior is None or candidate_sort > prior_sort:
            best[key] = dict(row)
    # Preserve all eight v0.1 strong candidates even if corpus-wide frequency
    # filtering or a different trace variant changes the new machine status.
    v1_status_path = require(project_root / "outputs/toolbench_composable_trace_audit_v0_1/toolbench_composable_evidence_status.csv")
    v1_normalized_path = require(project_root / "outputs/toolbench_composable_trace_audit_v0_1/toolbench_composable_normalized_steps.jsonl")
    v1_edges_path = require(project_root / "outputs/toolbench_composable_trace_audit_v0_1/toolbench_dependency_edge_candidates.jsonl")
    v1_normalized = read_jsonl_by_key(v1_normalized_path, "source_task_id")
    v1_edges: dict[str, list[dict[str, Any]]] = defaultdict(list)
    with v1_edges_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                edge = json.loads(line)
                v1_edges[text(edge.get("source_task_id"))].append(edge)
    current_eight = []
    for row in read_csv(v1_status_path):
        if row["evidence_availability_status"] != "strong_objective_evidence_available":
            continue
        task_id = row["task_id"]
        current_eight.append(task_id)
        key = f"ToolBench|{task_id}"
        normalized = v1_normalized.get(task_id, {})
        edges = [edge for edge in v1_edges.get(task_id, []) if edge.get("dependency_type") not in {"sequence_only", "none"} and not edge.get("query_known_value_filtered")]
        existing = best.get(key)
        if existing is None or existing.get("evidence_status") != "strong_objective_evidence_available":
            services = {text(step.get("service_name")) for step in normalized.get("steps", []) if text(step.get("service_name"))}
            apis = {text(step.get("api_name")) for step in normalized.get("steps", []) if text(step.get("api_name"))}
            edge_types = Counter(text(edge.get("dependency_type")) for edge in edges)
            source_file = text(normalized.get("steps", [{}])[0].get("source_file")) if normalized.get("steps") else ""
            best[key] = {
                "trace_record_id": f"v0_1::{task_id}",
                "source_dataset": "ToolBench",
                "source_area": "data_answer" if "data\\answer" in source_file else "v0_1_recovered",
                "source_group": row["source_group"],
                "source_task_id": task_id,
                "instruction_query_id": row["source_query_id"],
                "query_text": normalized.get("query_text", inventory_by_task.get(task_id, {}).get("query_text", "")),
                "source_file": source_file,
                "source_record_path": "$",
                "step_count": len(normalized.get("steps", [])),
                "distinct_service_count": len(services),
                "distinct_api_count": len(apis),
                "arguments_available": "true",
                "outputs_available": "true",
                "observations_available": "true",
                "query_known_edges_filtered": 0,
                "strong_edge_count": len(edges),
                "partial_edge_count": 0,
                "data_flow_edge_count": edge_types["data_flow"],
                "entity_flow_edge_count": edge_types["entity_flow"],
                "control_flow_edge_count": edge_types["control_flow"],
                "conditional_flow_edge_count": edge_types["conditional_flow"],
                "sequence_edge_count": 0,
                "evidence_status": "strong_objective_evidence_available",
                "parse_status": "ok",
                "parse_error": "",
                "evidence_score": 1000 + len(edges) * 20,
                "_v0_1_normalized": normalized,
                "_v0_1_edges": edges,
            }
    ranked = sorted(best.values(), key=lambda row: (-STATUS_PRIORITY.get(row["evidence_status"], 0), -int_value(row.get("evidence_score")), text(row.get("source_task_id"))))
    rank_fields = [
        "evidence_rank", "source_dataset", "source_task_id", "instruction_query_id", "source_group",
        "query_text", "source_area", "source_file", "source_record_path", "trace_record_id",
        "trace_variant_count", "step_count", "distinct_service_count", "distinct_api_count",
        "strong_edge_count", "partial_edge_count", "data_flow_edge_count", "entity_flow_edge_count",
        "control_flow_edge_count", "conditional_flow_edge_count", "sequence_edge_count",
        "arguments_available", "outputs_available", "observations_available", "query_known_edges_filtered",
        "source_trace_complete", "evidence_status", "evidence_score", "current_322_member",
        "current_review_pack_member", "selection_note",
    ]
    for rank, row in enumerate(ranked, start=1):
        row["evidence_rank"] = rank
        row["trace_variant_count"] = trace_variant_counts.get(task_key(row), 1)
        row["source_trace_complete"] = bool_text(bool(text(row.get("source_file")) and truthy(row.get("arguments_available")) and (truthy(row.get("outputs_available")) or truthy(row.get("observations_available")))))
        row["current_322_member"] = bool_text(row["source_task_id"] in current_members)
        row["current_review_pack_member"] = bool_text(row["source_task_id"] in current_review)
        row["selection_note"] = "Machine evidence candidate only; not a final composable label."
    write_csv(output_dir / "composable_underlying_task_candidates_ranked.csv", rank_fields, ranked)
    status_counts = Counter(row["evidence_status"] for row in ranked)
    dedup_summary = {
        "generated_at": now_iso(),
        "trace_level_rows": len(status_rows),
        "underlying_task_rows": len(ranked),
        "deduplicated_trace_variant_count": max(0, len(status_rows) - len(ranked)),
        "status_distribution": dict(sorted(status_counts.items())),
        "dedup_keys_in_priority_order": ["source_dataset+source_task_id", "source_dataset+instruction_id", "source_dataset+source_query_id"],
        "g3_used_as_decisive_evidence_factor": False,
    }
    write_json(output_dir / "composable_underlying_task_dedup_summary.json", dedup_summary)
    strong = [row for row in ranked if row["evidence_status"] == "strong_objective_evidence_available"]
    partial = [row for row in ranked if row["evidence_status"] == "partial_objective_evidence"]
    selected = select_review_rows(strong, partial, set(current_eight))
    selected_trace_ids = {
        text(row.get("trace_record_id"))
        for row in selected
        if text(row.get("trace_record_id")) and not text(row.get("trace_record_id")).startswith("v0_1::")
    }
    normalized_by_trace = read_selected_jsonl_by_key(
        require(output_dir / "toolbench_full_normalized_multicall_steps.jsonl"),
        "trace_record_id",
        selected_trace_ids,
    )
    trace_edges = selected_edges_by_trace(
        require(output_dir / "toolbench_full_dependency_edge_candidates.jsonl"),
        selected_trace_ids,
    )
    review_fields = [
        "review_item_id", "source_task_id", "source_dataset", "source_group", "query_text",
        "ordered_steps_json", "services_json", "apis_json", "dependency_edges_json",
        "dependency_evidence_json", "upstream_output_path", "downstream_input_or_control_path",
        "evidence_value", "value_present_in_original_query", "source_trace_path", "source_answer_path",
        "evidence_status", "evidence_score", "current_322_member", "prior_review_content_hash",
    ] + HUMAN_FIELDS
    review_rows: list[dict[str, Any]] = []
    for index, row in enumerate(selected, start=1):
        trace_id = text(row.get("trace_record_id"))
        normalized = row.get("_v0_1_normalized") or normalized_by_trace.get(trace_id, {})
        edges = row.get("_v0_1_edges") or trace_edges.get(trace_id, [])
        objective_edges = [edge for edge in edges if edge.get("dependency_type") not in {"sequence_only", "none"} and not edge.get("query_known_value_filtered")]
        first_edge = objective_edges[0] if objective_edges else {}
        steps = normalized.get("steps", [])
        services = list(dict.fromkeys(text(step.get("service_name")) for step in steps if text(step.get("service_name"))))
        apis = list(dict.fromkeys(text(step.get("api_name")) for step in steps if text(step.get("api_name"))))
        source_file = text(row.get("source_file"))
        review = {
            "review_item_id": f"COMP-V0.2-{index:04d}",
            "source_task_id": row["source_task_id"],
            "source_dataset": row["source_dataset"],
            "source_group": row["source_group"],
            "query_text": row["query_text"],
            "ordered_steps_json": json_text(steps),
            "services_json": json_text(services),
            "apis_json": json_text(apis),
            "dependency_edges_json": json_text(objective_edges),
            "dependency_evidence_json": json_text({
                "objective_edge_count": len(objective_edges),
                "edge_type_distribution": dict(Counter(text(edge.get("dependency_type")) for edge in objective_edges)),
                "machine_evidence_only": True,
            }),
            "upstream_output_path": text(first_edge.get("upstream_source_path")),
            "downstream_input_or_control_path": text(first_edge.get("downstream_source_path")),
            "evidence_value": text(first_edge.get("upstream_value")),
            "value_present_in_original_query": bool_text(bool(first_edge.get("value_present_in_original_query"))),
            "source_trace_path": source_file,
            "source_answer_path": source_file if "data\\answer" in source_file.replace("/", "\\") else "",
            "evidence_status": row["evidence_status"],
            "evidence_score": row["evidence_score"],
            "current_322_member": row["current_322_member"],
            "prior_review_content_hash": "",
        }
        for field in HUMAN_FIELDS:
            review[field] = ""
        review_rows.append(review)
    write_csv(output_dir / "composable_evidence_review_items_v0_2.csv", review_fields, review_rows)
    recovery_manifest = read_csv(require(output_dir / "one_pass_join_recovery_manifest.csv"))
    routing_rows = build_routing_rows(inventory_rows, read_csv(require(output_dir / "current_322_status_reconciliation.csv")), recovery_manifest, ranked)
    routing_fields = list(routing_rows[0]) if routing_rows else []
    write_csv(output_dir / "non_composable_routing_manifest.csv", routing_fields, routing_rows)
    return {
        "strong_underlying_task_candidate_count": len(strong),
        "partial_underlying_task_candidate_count": len(partial),
        "sequence_only_underlying_task_count": status_counts["sequence_only"],
        "no_dependency_underlying_task_count": status_counts["no_dependency_evidence"],
        "parse_failed_underlying_task_count": status_counts["parse_failed"],
        "evidence_review_pack_rows": len(review_rows),
        "current_8_strong_candidates_preserved": set(current_eight).issubset({row["source_task_id"] for row in review_rows}),
        "current_8_task_ids": current_eight,
        "human_confirmed_composable_count": 0,
        "routing_rows": len(routing_rows),
    }


def select_review_rows(strong: list[dict[str, Any]], partial: list[dict[str, Any]], forced: set[str]) -> list[dict[str, Any]]:
    if len(strong) <= 200:
        selected = list(strong)
    else:
        forced_rows = [row for row in strong if row["source_task_id"] in forced]
        remaining = [row for row in strong if row["source_task_id"] not in forced]
        buckets: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
        for row in remaining:
            normalized_group = text(row.get("source_group")) or "unknown"
            buckets[normalized_group].append(row)
        selected = forced_rows[:200]
        keys = sorted(buckets)
        while len(selected) < 200 and any(buckets[key] for key in keys):
            for key in keys:
                if buckets[key] and len(selected) < 200:
                    selected.append(buckets[key].popleft())
    if len(strong) < 100:
        selected.extend(partial[: max(0, 100 - len(selected))])
    unique: dict[str, dict[str, Any]] = {}
    for row in selected:
        unique.setdefault(task_key(row), row)
    return list(unique.values())


def candidate_space_usable(inventory_row: dict[str, Any]) -> bool:
    candidates_services = parse_json_list(inventory_row.get("candidate_services_json"))
    candidates_apis = parse_json_list(inventory_row.get("candidate_apis_json"))
    gold_services = parse_json_list(inventory_row.get("gold_services_json"))
    gold_apis = parse_json_list(inventory_row.get("gold_apis_json"))
    return len(candidates_services) > len(gold_services) or len(candidates_apis) > len(gold_apis)


def build_routing_rows(
    inventory: list[dict[str, str]],
    reconciliation: list[dict[str, str]],
    recovery: list[dict[str, str]],
    ranked: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    inventory_by_id = {row["inventory_id"]: row for row in inventory}
    recovery_by_id = {row["inventory_row_id"]: row for row in recovery}
    routes: dict[str, dict[str, Any]] = {}
    for row in reconciliation:
        status = row["exclusive_evidence_status"]
        if status in {"strong_objective_evidence_available", "partial_objective_evidence"}:
            continue
        item = inventory_by_id.get(row["inventory_row_id"], {})
        recovery_row = recovery_by_id.get(row["inventory_row_id"], {})
        if recovery_row.get("recovery_status") == "newly_exact_joined" and recovery_row.get("recovered_evidence_status") in {"strong_objective_evidence_available", "partial_objective_evidence"}:
            continue
        effective_status = "source_unavailable" if recovery_row.get("recovery_status") == "source_unavailable_hold" else status
        if effective_status == "source_unavailable":
            route, reason = "source_unavailable_hold", "No unique exact local trace join after the authorized one-pass recovery."
        elif effective_status == "sequence_only":
            route, reason = "multi_candidate_or_sequence_diagnostic", "Explicit order exists without an objective non-sequence dependency edge."
        elif effective_status == "no_dependency_evidence":
            usable = candidate_space_usable(item)
            route = "multi_candidate" if usable else "hold_or_excluded"
            reason = "No objective dependency edge; candidate space appears usable." if usable else "No objective dependency edge and no verified usable candidate choice space."
        elif effective_status == "join_ambiguous":
            route, reason = "source_unavailable_hold", "Multiple equally ranked exact trace records remain; no record was selected."
        else:
            route, reason = "hold_or_excluded", "Trace parse failed or evidence is not usable for composable review."
        key = f"{row['source_dataset']}|{row['task_id']}"
        routes[key] = {
            "source_dataset": row["source_dataset"],
            "source_task_id": row["task_id"],
            "source_group": row["source_group"],
            "evidence_status": effective_status,
            "candidate_space_usable": bool_text(candidate_space_usable(item)),
            "suggested_route": route,
            "routing_reason": reason,
            "final_label_modified": "false",
        }
    for row in ranked:
        status = row["evidence_status"]
        if status in {"strong_objective_evidence_available", "partial_objective_evidence"}:
            continue
        if status == "sequence_only":
            route = "multi_candidate_or_sequence_diagnostic"
            reason = "Only ordered execution was observed; no objective output/input dependency edge."
        elif status == "no_dependency_evidence":
            route = "hold_or_excluded"
            reason = "No objective dependency evidence and corpus trace does not establish benchmark candidate choice space."
        else:
            route = "hold_or_excluded"
            reason = "Deep trace parsing failed."
        key = f"ToolBench|{row['source_task_id']}"
        routes.setdefault(key, {
            "source_dataset": "ToolBench",
            "source_task_id": row["source_task_id"],
            "source_group": row["source_group"],
            "evidence_status": status,
            "candidate_space_usable": "unknown",
            "suggested_route": route,
            "routing_reason": reason,
            "final_label_modified": "false",
        })
    return sorted(routes.values(), key=lambda row: (row["source_dataset"], row["source_task_id"]))


def recommended_next_step(strong: int, partial: int) -> str:
    if strong >= 100:
        return "humanly confirm the single consolidated composable evidence review pack; derive paired composable service/API rows only from confirmed tasks."
    if strong + partial >= 100:
        return "humanly review all strong and selected partial rows; do not use sequence-only or no-evidence rows."
    return "report the evidence shortage; do not fabricate composable rows; consider StableToolBench trace/schema-grounded recovery as a separate bounded branch."


def build_gate_report(
    project_root: Path,
    output_dir: Path,
    reconciliation: dict[str, Any],
    recovery: dict[str, Any],
    index_summary: dict[str, Any],
    mining_summary: dict[str, Any],
    package_summary: dict[str, Any],
) -> Path:
    strong = package_summary["strong_underlying_task_candidate_count"]
    partial = package_summary["partial_underlying_task_candidate_count"]
    next_step = recommended_next_step(strong, partial)
    report_path = project_root / "docs/phase1/composable_corpus_mining_go_no_go_v0_2.md"
    report = f"""# Composable Corpus Mining Gate 4 Go/No-Go v0.2

Generated at: `{now_iso()}`  
Project root: `{project_root.resolve()}`

## Scope

This run reconciled the current inventory, executed the single authorized exact-join recovery pass, indexed local ToolBench traces, mined objective dependency evidence, and prepared one human review pack. Machine evidence status is not a final composable label.

## Fixed Gate Fields

```text
toolbench_answer_path_usable = true
toolbench_reproduction_path_usable = true
current_322_reconciled = {str(reconciliation['status_sum_matches_inventory']).lower()}
one_pass_join_recovery_completed = {str(recovery['one_pass_completed']).lower()}
full_corpus_trace_index_completed = {str(index_summary['completed']).lower()}
full_corpus_dependency_mining_completed = {str(mining_summary['completed']).lower()}
strong_underlying_task_candidate_count = {strong}
partial_underlying_task_candidate_count = {partial}
evidence_review_pack_rows = {package_summary['evidence_review_pack_rows']}
current_8_strong_candidates_preserved = {str(package_summary['current_8_strong_candidates_preserved']).lower()}
human_confirmed_composable_count = 0
can_claim_composable_benchmark_now = false
can_start_composable_human_confirmation = {str(package_summary['evidence_review_pack_rows'] > 0).lower()}
can_start_six_task_candidate_assembly = false
can_generate_final_dataset = false
can_create_split = false
can_run_baseline = false
```

## Current 322 Reconciliation

- Inventory: `{reconciliation['inventory_unique_count']}`
- Exact joined before recovery: `{reconciliation['exact_joined_count']}`
- Exclusive status sum: `{reconciliation['status_sum']}`
- Sum matches inventory: `{reconciliation['status_sum_matches_inventory']}`
- Status distribution: `{json_text(reconciliation['status_distribution'])}`
- By source: `{json_text(reconciliation['status_by_source_dataset'])}`

## One-Pass Exact Join Recovery

- Input rows: `{recovery['recovery_input_count']}`
- Newly exact joined: `{recovery['newly_exact_joined_count']}`
- Still unavailable: `{recovery['still_unavailable_count']}`
- Still ambiguous: `{recovery['still_ambiguous_count']}`
- No fuzzy, semantic, embedding, LLM, or row-specific hard-coded join was used.

## Full Corpus Evidence Mining

- Trace index records: `{index_summary['trace_index_record_count']}`
- Multi-call records: `{index_summary['multicall_record_count']}`
- Multi-service records: `{index_summary['multiservice_record_count']}`
- Pass 2 / deep-parsed records: `{mining_summary['deep_parsed_record_count']}`
- Trace-level evidence status: `{json_text(mining_summary['evidence_status_distribution'])}`
- Underlying-task strong candidates: `{strong}`
- Underlying-task partial candidates: `{partial}`

## Decision

`NO_GO_COMPOSABLE_BENCHMARK_CLAIM`

There are machine-mined evidence candidates, but zero human-confirmed composable tasks in this v0.2 pack. The review pack may now enter human confirmation; six-task assembly, final dataset generation, split, baseline, and training remain blocked.

Recommended next step: `{next_step}`
"""
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    return report_path


def update_master_plan(project_root: Path, package_summary: dict[str, Any], reconciliation: dict[str, Any], recovery: dict[str, Any], index_summary: dict[str, Any]) -> Path:
    path = require(project_root / "docs/project/SERVICEDISCOVERYBENCH_BENCHMARK_MASTER_PLAN.md")
    content = path.read_text(encoding="utf-8")
    gate_start = content.index("## Gate 4：Composable Recovery")
    gate_end = content.index("## Gate 5：", gate_start)
    section = content[gate_start:gate_end]
    section = re.sub(r"状态：`[^`]+`", "状态：`CORPUS_WIDE_MINING_COMPLETE_HUMAN_CONFIRMATION_PENDING`", section, count=1)
    begin = "<!-- BEGIN GATE4 V0.2 VARIABLE STATUS -->"
    end = "<!-- END GATE4 V0.2 VARIABLE STATUS -->"
    block = f"""{begin}

### v0.2 可变状态（{now_iso()}）

- trace path availability: `answer=true`, `reproduction=true`；
- current inventory reconciliation: `{reconciliation['status_sum']}/{reconciliation['inventory_unique_count']}`；
- one-pass exact join coverage: newly joined `{recovery['newly_exact_joined_count']}`, unavailable hold `{recovery['still_unavailable_count']}`, ambiguous `{recovery['still_ambiguous_count']}`；
- full trace index records: `{index_summary['trace_index_record_count']}`；
- strong objective evidence candidates: `{package_summary['strong_underlying_task_candidate_count']}`；
- current blocker: `HUMAN_COMPOSABLE_CONFIRMATION_PENDING`；
- recommended next action: `{recommended_next_step(package_summary['strong_underlying_task_candidate_count'], package_summary['partial_underlying_task_candidate_count'])}`
- human-final authority remains unchanged; no machine evidence status is a final composable label.

{end}

"""
    if begin in section and end in section:
        section = re.sub(re.escape(begin) + r".*?" + re.escape(end) + r"\s*", block, section, flags=re.S)
    else:
        status_match = re.search(r"状态：`[^`]+`\s*", section)
        insert_at = status_match.end() if status_match else len("## Gate 4：Composable Recovery\n")
        section = section[:insert_at] + "\n" + block + section[insert_at:]
    content = content[:gate_start] + section + content[gate_end:]
    changelog_header = "# 13. Change Log"
    entry_marker = "## v1.2 — 2026-07-14"
    if entry_marker not in content:
        entry = f"""

{entry_marker}

- 仅更新 Gate 4 可变状态，不修改 benchmark-only 范围、六任务要求、人工作为最终裁决者或禁止自动 composable 标签的规则；
- 完成 322 条互斥状态对账与一次性精确 join recovery；
- 完成 ToolBench answer/reproduction 全语料轻量索引和客观依赖挖掘；
- strong objective evidence underlying-task candidates = `{package_summary['strong_underlying_task_candidate_count']}`；
- Gate 4 更新为 `CORPUS_WIDE_MINING_COMPLETE_HUMAN_CONFIRMATION_PENDING`；
- source freeze、six-task assembly、final dataset、split、baseline 与 training 继续禁止。
"""
        position = content.index(changelog_header) + len(changelog_header)
        content = content[:position] + entry + content[position:]
    path.write_text(content, encoding="utf-8")
    return path


def archive_run(project_root: Path, output_dir: Path, report_path: Path, master_plan_path: Path) -> Path:
    archive = project_root / "outputs/run_archives/2026-07-13_toolbench_corpus_wide_composable_dependency_mining_v0_2"
    archive.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, Any]] = []
    files = [path for path in output_dir.iterdir() if path.is_file() and not path.name.startswith(".")]
    files.extend([report_path, master_plan_path, Path(__file__).resolve()])
    for source in files:
        target = archive / source.name
        if source.resolve() == target.resolve():
            continue
        source_hash = sha256_file(source)
        if target.exists():
            try:
                same_file = os.path.samefile(source, target)
            except OSError:
                same_file = False
            if same_file or sha256_file(target) == source_hash:
                archive_mode = "already_present_same_content"
            else:
                shutil.copy2(source, target)
                archive_mode = "updated_changed_content"
        else:
            if source.stat().st_size > 100 * 1024 * 1024:
                try:
                    os.link(source, target)
                    archive_mode = "hardlink_no_duplicate_storage"
                except OSError:
                    archive_mode = "reference_only_large_derived_file"
            else:
                shutil.copy2(source, target)
                archive_mode = "copied"
        manifest.append({
            "source": str(source),
            "archive_target": str(target) if target.exists() else "",
            "archive_mode": archive_mode,
            "size_bytes": source.stat().st_size,
            "sha256": source_hash,
        })
    write_json(archive / "archive_manifest.json", {
        "generated_at": now_iso(),
        "raw_toolbench_files_archived": False,
        "files": manifest,
    })
    return archive


def run_all(args: argparse.Namespace) -> dict[str, Any]:
    project_root = args.project_root.resolve()
    output_dir = project_root / "outputs/composable_corpus_mining_v0_2"
    output_dir.mkdir(parents=True, exist_ok=True)
    reconciliation = reconcile_current_322(project_root, output_dir)
    recovery = recover_once(project_root, output_dir)
    index_summary = build_full_index(project_root, output_dir, workers=args.workers, progress_every=args.progress_every)
    mining_summary = mine_dependencies(project_root, output_dir, common_value_threshold=args.common_value_threshold)
    package_summary = build_ranked_and_review(project_root, output_dir)
    report_path = build_gate_report(project_root, output_dir, reconciliation, recovery, index_summary, mining_summary, package_summary)
    master_plan = update_master_plan(project_root, package_summary, reconciliation, recovery, index_summary)
    archive = project_root / "outputs/run_archives/2026-07-13_toolbench_corpus_wide_composable_dependency_mining_v0_2"
    run_summary = {
        "generated_at": now_iso(),
        "current_322_status_sum": reconciliation["status_sum"],
        "current_322_status_sum_matches_inventory": reconciliation["status_sum_matches_inventory"],
        "one_pass_join_recovery_input_count": recovery["recovery_input_count"],
        "one_pass_newly_exact_joined_count": recovery["newly_exact_joined_count"],
        "one_pass_still_unavailable_count": recovery["still_unavailable_count"],
        "one_pass_still_ambiguous_count": recovery["still_ambiguous_count"],
        "toolbench_trace_index_record_count": index_summary["trace_index_record_count"],
        "toolbench_multicall_record_count": index_summary["multicall_record_count"],
        "toolbench_multiservice_record_count": index_summary["multiservice_record_count"],
        "toolbench_deep_parsed_record_count": mining_summary["deep_parsed_record_count"],
        **package_summary,
        "can_start_composable_human_confirmation": package_summary["evidence_review_pack_rows"] > 0,
        "can_claim_composable_benchmark_now": False,
        "can_start_six_task_candidate_assembly": False,
        "can_generate_final_dataset": False,
        "can_create_split": False,
        "can_run_baseline": False,
        "recommended_next_step": recommended_next_step(package_summary["strong_underlying_task_candidate_count"], package_summary["partial_underlying_task_candidate_count"]),
        "archive": str(archive),
    }
    write_json(output_dir / "composable_corpus_mining_run_summary_v0_2.json", run_summary)
    archive = archive_run(project_root, output_dir, report_path, master_plan)
    run_summary["archive"] = str(archive)
    write_json(output_dir / "composable_corpus_mining_run_summary_v0_2.json", run_summary)
    return run_summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd(), help="Project root; defaults to current directory.")
    parser.add_argument(
        "--stage",
        choices=["reconcile", "recover", "index", "mine", "package", "all"],
        default="all",
        help="Run one resumable stage or the complete authorized workflow.",
    )
    parser.add_argument("--workers", type=int, default=min(8, max(2, os.cpu_count() or 2)), help="Thread count for lightweight file indexing.")
    parser.add_argument("--progress-every", type=int, default=500, help="Print index progress every N files.")
    parser.add_argument("--common-value-threshold", type=int, default=10, help="Filter scalar values reused as arguments by at least this many trace records.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.project_root.resolve()
    output_dir = root / "outputs/composable_corpus_mining_v0_2"
    output_dir.mkdir(parents=True, exist_ok=True)
    if args.stage == "all":
        summary = run_all(args)
    elif args.stage == "reconcile":
        summary = reconcile_current_322(root, output_dir)
    elif args.stage == "recover":
        reconcile_current_322(root, output_dir)
        summary = recover_once(root, output_dir)
    elif args.stage == "index":
        summary = build_full_index(root, output_dir, workers=args.workers, progress_every=args.progress_every)
    elif args.stage == "mine":
        summary = mine_dependencies(root, output_dir, common_value_threshold=args.common_value_threshold)
    else:
        reconciliation = reconcile_current_322(root, output_dir)
        recovery = recover_once(root, output_dir)
        index_summary = json.loads(require(output_dir / "toolbench_full_trace_index_summary.json").read_text(encoding="utf-8"))
        mining_summary = json.loads(require(output_dir / "toolbench_full_dependency_mining_summary.json").read_text(encoding="utf-8"))
        package_summary = build_ranked_and_review(root, output_dir)
        report = build_gate_report(root, output_dir, reconciliation, recovery, index_summary, mining_summary, package_summary)
        master = update_master_plan(root, package_summary, reconciliation, recovery, index_summary)
        archive = archive_run(root, output_dir, report, master)
        summary = {**package_summary, "archive": str(archive)}
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
