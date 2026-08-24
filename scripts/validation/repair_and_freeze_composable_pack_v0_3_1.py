#!/usr/bin/env python
"""Repair or replace invalid composable API rows and freeze the v0.3.1 pack.

This stage does not mine new evidence, assign human labels, or modify v0.3
draft outputs. Missing parent mappings are repaired only by exact local catalog
keys. Otherwise, the row is reserved and replaced from the frozen v0.2 ranked
strong-evidence pool.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import prepare_composable_paired_tasks_v0_3 as prep  # noqa: E402


VERSION = "v0.3.1"
OUTPUT_RELATIVE = Path("outputs/composable_paired_task_preparation_v0_3_1")
ARCHIVE_RELATIVE = Path("outputs/run_archives/2026-07-14_composable_final_review_pack_freeze_v0_3_1")
LEDGER_RELATIVE = Path("outputs/review_credit_ledger/composable_review_credit_ledger_v0_3_1.csv")
ORDER_SEED = "COMPOSABLE-V0.3.1-CANDIDATE-ORDER-SEED-20260714"
DOUBLE_SEED = "COMPOSABLE-V0.3.1-DOUBLE-ANNOTATION-SEED-20260714"

INPUTS = {
    "master_v0_3": Path("outputs/composable_paired_task_preparation_v0_3/composable_underlying_tasks_master_v0_3.csv"),
    "service_v0_3": Path("outputs/composable_paired_task_preparation_v0_3/composable_service_discovery_provisional_rows_v0_3.csv"),
    "api_v0_3": Path("outputs/composable_paired_task_preparation_v0_3/composable_api_recommendation_provisional_rows_v0_3.csv"),
    "review_v0_3": Path("outputs/composable_paired_task_preparation_v0_3/composable_paired_task_review_items_v0_3.csv"),
    "ranked_v0_2": Path("outputs/composable_corpus_mining_v0_2/composable_underlying_task_candidates_ranked.csv"),
    "source_review_v0_2": Path("outputs/composable_corpus_mining_v0_2/composable_evidence_review_items_v0_2.csv"),
    "steps_v0_2": Path("outputs/composable_corpus_mining_v0_2/toolbench_full_normalized_multicall_steps.jsonl"),
    "edges_v0_2": Path("outputs/composable_corpus_mining_v0_2/toolbench_full_dependency_edge_candidates.jsonl"),
    "catalog": Path("external_sources/ToolBench/data/toolenv/tools"),
    "master_plan": Path("docs/project/SERVICEDISCOVERYBENCH_BENCHMARK_MASTER_PLAN.md"),
}

INVALID_FAILURE_REASONS = {
    "candidate_equals_gold",
    "no_negative_distractor",
    "gold_not_in_candidate",
    "parent_mapping_missing",
    "duplicate_or_alias_conflict",
    "catalog_missing",
    "other_structural_failure",
}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def truthy(value: Any) -> bool:
    return text(value).casefold() in {"1", "true", "yes"}


def int_value(value: Any) -> int:
    try:
        return int(float(text(value) or 0))
    except ValueError:
        return 0


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def parse_json(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    raw = text(value)
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fields is None:
        fields = list(rows[0]) if rows else []
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def resolve_inputs(root: Path, output_dir: Path) -> dict[str, Path]:
    paths = {name: root / relative for name, relative in INPUTS.items()}
    missing = [str(path) for path in paths.values() if not path.exists()]
    if missing:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "MISSING_INPUTS.md").write_text(
            "# Missing inputs\n\n" + "\n".join(f"- `{item}`" for item in missing) + "\n",
            encoding="utf-8",
        )
        raise FileNotFoundError("Required inputs are missing; see MISSING_INPUTS.md")
    return paths


def candidate_keys(row: dict[str, Any], field: str, key: str) -> list[str]:
    return [text(item.get(key)) for item in parse_json(row.get(field), []) if isinstance(item, dict) and text(item.get(key))]


def api_parent_mapping_valid(row: dict[str, Any]) -> bool:
    candidates = parse_json(row.get("candidate_apis_json"), [])
    mapping = parse_json(row.get("service_api_map_json"), [])
    mapped = {
        text(item.get("function_key")): text(item.get("service_key"))
        for item in mapping if isinstance(item, dict)
    }
    for item in candidates:
        if not isinstance(item, dict):
            return False
        function_key = text(item.get("function_key"))
        service_key = text(item.get("service_key"))
        service_name = text(item.get("service_name"))
        if not function_key or not service_key or not service_name or mapped.get(function_key) != service_key:
            return False
    return True


def structural_failure_reason(row: dict[str, Any]) -> str:
    candidates = candidate_keys(row, "candidate_apis_json", "function_key")
    gold = candidate_keys(row, "provisional_gold_apis_json", "function_key")
    if not candidates:
        return "catalog_missing"
    if len(candidates) != len(set(candidates)) or len(gold) != len(set(gold)):
        return "duplicate_or_alias_conflict"
    if not set(gold).issubset(set(candidates)):
        return "gold_not_in_candidate"
    if candidates == gold or len(candidates) == len(gold):
        return "candidate_equals_gold"
    if len(candidates) - len(gold) <= 0 or int_value(row.get("api_negative_distractor_count")) <= 0:
        return "no_negative_distractor"
    if not api_parent_mapping_valid(row):
        return "parent_mapping_missing"
    if text(row.get("api_candidate_space_status")) != "valid":
        return "other_structural_failure"
    return ""


def build_invalid_input_rows(
    review_rows: list[dict[str, str]],
    original_8_ids: set[str],
) -> list[dict[str, Any]]:
    invalid: list[dict[str, Any]] = []
    for row in review_rows:
        reason = structural_failure_reason(row)
        if not reason:
            continue
        invalid.append({
            "underlying_task_id": row["underlying_task_id"],
            "source_task_id": row["source_task_id"],
            "query_text": row["query_text"],
            "provisional_gold_apis_json": row["provisional_gold_apis_json"],
            "current_candidate_apis_json": row["candidate_apis_json"],
            "current_candidate_api_count": row["candidate_api_count"],
            "gold_api_count": row["gold_api_count"],
            "negative_distractor_count": row["api_negative_distractor_count"],
            "api_candidate_space_status": row["api_candidate_space_status"],
            "api_parent_mapping_status": "valid" if api_parent_mapping_valid(row) else "missing_or_invalid",
            "failure_reason": reason,
            "current_review_content_hash": row["review_content_hash"],
            "whether_row_belongs_to_original_8_strong_candidates": prep.bool_text(row["source_task_id"] in original_8_ids),
        })
    return invalid


def exact_catalog_repair(
    row: dict[str, Any],
    static_apis: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    candidates = parse_json(row.get("candidate_apis_json"), [])
    gold = parse_json(row.get("provisional_gold_apis_json"), [])
    gold_keys = {text(item.get("function_key")) for item in gold if isinstance(item, dict)}
    repaired: list[dict[str, Any]] = []
    lookup_trace: list[dict[str, Any]] = []
    unresolved: list[str] = []
    changed = False
    for item in candidates:
        if not isinstance(item, dict):
            continue
        function_key = text(item.get("function_key"))
        if text(item.get("service_key")) and text(item.get("service_name")):
            repaired.append(dict(item))
            continue
        exact = static_apis.get(function_key)
        if exact and text(exact.get("service_key")) and text(exact.get("service_name")):
            repaired_item = {key: value for key, value in exact.items() if not key.startswith("_")}
            repaired_item["function_name"] = text(item.get("function_name")) or repaired_item["function_name"]
            repaired_item["function_key"] = function_key
            repaired.append(repaired_item)
            changed = True
            lookup_trace.append({
                "function_key": function_key,
                "lookup": "exact_static_function_key",
                "result": "matched",
                "catalog_source_path": text(exact.get("catalog_source_path")),
            })
        else:
            repaired.append(dict(item))
            unresolved.append(function_key)
            lookup_trace.append({
                "function_key": function_key,
                "lookup": "exact_static_function_key",
                "result": "not_found",
                "catalog_source_path": "",
            })
    mapping = [
        {
            "function_name": item.get("function_name", ""),
            "api_name": item.get("api_name", ""),
            "service_name": item.get("service_name", ""),
            "function_key": item.get("function_key", ""),
            "service_key": item.get("service_key", ""),
            "mapping_source": item.get("catalog_source_path", ""),
        }
        for item in repaired
    ]
    candidate_keys_set = {text(item.get("function_key")) for item in repaired}
    parent_valid = all(text(item.get("service_key")) and text(item.get("service_name")) for item in repaired)
    valid = all([
        not unresolved,
        parent_valid,
        gold_keys < candidate_keys_set,
        len(repaired) > len(gold),
        len(repaired) == len(candidate_keys_set),
    ])
    updated = dict(row)
    updated["candidate_apis_json"] = json_dumps(repaired)
    updated["service_api_map_json"] = json_dumps(mapping)
    updated["candidate_api_count"] = len(repaired)
    updated["api_negative_distractor_count"] = len(candidate_keys_set - gold_keys)
    updated["api_candidate_space_status"] = "valid" if valid else "reconstruction_needed"
    trace = {
        "underlying_task_id": row["underlying_task_id"],
        "source_task_id": row["source_task_id"],
        "repair_status": "repaired_valid" if valid else ("cannot_repair_mapping_missing" if unresolved else "cannot_repair_other"),
        "repaired_candidate_apis_json": updated["candidate_apis_json"],
        "repaired_candidate_api_count": len(repaired),
        "repaired_negative_distractor_count": updated["api_negative_distractor_count"],
        "negative_sources_json": row.get("api_candidate_construction_evidence_json", "{}"),
        "parent_mapping_valid": prep.bool_text(parent_valid),
        "post_repair_candidate_space_valid": prep.bool_text(valid),
        "repair_evidence_json": json_dumps({
            "policy": "exact_local_catalog_function_key_only",
            "changed": changed,
            "unresolved_function_keys": unresolved,
            "lookup_trace": lookup_trace,
            "semantic_or_llm_inference_used": False,
            "query_changed": False,
            "provisional_gold_changed": False,
            "dependency_evidence_changed": False,
        }),
    }
    return updated, trace


def read_selected_evidence(
    steps_path: Path,
    edges_path: Path,
    trace_ids: set[str],
) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    normalized: dict[str, dict[str, Any]] = {}
    with steps_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            trace_id = text(item.get("trace_record_id"))
            if trace_id in trace_ids:
                normalized[trace_id] = item
    edges: dict[str, list[dict[str, Any]]] = defaultdict(list)
    with edges_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            trace_id = text(item.get("trace_record_id"))
            if trace_id in trace_ids and text(item.get("dependency_type")) not in {"", "none", "sequence_only"}:
                edges[trace_id].append(item)
    return normalized, edges


def synthetic_source_review(ranked: dict[str, str], normalized: dict[str, Any], edges: list[dict[str, Any]]) -> dict[str, Any]:
    edge_types = Counter(text(edge.get("dependency_type")) or "unknown" for edge in edges)
    return {
        "review_item_id": "replacement_candidate_provisional",
        "source_task_id": text(ranked.get("source_task_id")),
        "source_dataset": "ToolBench",
        "source_group": text(ranked.get("source_group")),
        "query_text": text(normalized.get("query_text") or ranked.get("query_text")),
        "ordered_steps_json": json_dumps(normalized.get("steps") or []),
        "services_json": "[]",
        "apis_json": "[]",
        "dependency_edges_json": json_dumps(edges),
        "dependency_evidence_json": json_dumps({
            "edge_type_distribution": dict(sorted(edge_types.items())),
            "machine_evidence_only": True,
            "objective_edge_count": len(edges),
        }),
        "source_trace_path": text(ranked.get("source_file")),
        "source_answer_path": text(ranked.get("source_file")),
        "evidence_status": "strong_objective_evidence_available",
        "evidence_score": text(ranked.get("evidence_score")),
        "current_322_member": text(ranked.get("current_322_member")),
    }


def row_structurally_valid(row: dict[str, Any]) -> tuple[bool, list[str]]:
    issues: list[str] = []
    service_candidates = set(candidate_keys(row, "candidate_services_json", "service_key"))
    service_gold = set(candidate_keys(row, "provisional_gold_services_json", "service_key"))
    api_candidates_list = candidate_keys(row, "candidate_apis_json", "function_key")
    api_candidates = set(api_candidates_list)
    api_gold = set(candidate_keys(row, "provisional_gold_apis_json", "function_key"))
    if not text(row.get("query_text")):
        issues.append("query_empty")
    if not parse_json(row.get("dependency_evidence_json"), {}):
        issues.append("dependency_evidence_empty")
    if text(row.get("service_candidate_space_status")) != "valid":
        issues.append("service_candidate_space_not_valid")
    if text(row.get("api_candidate_space_status")) != "valid":
        issues.append("api_candidate_space_not_valid")
    if not service_gold < service_candidates:
        issues.append("service_gold_not_strict_subset")
    if not api_gold < api_candidates:
        issues.append("api_gold_not_strict_subset")
    if len(api_candidates_list) != len(api_candidates):
        issues.append("api_alias_or_duplicate")
    if not api_parent_mapping_valid(row):
        issues.append("api_parent_mapping_invalid")
    if int_value(row.get("api_negative_distractor_count")) <= 0:
        issues.append("api_no_negative_distractor")
    if not Path(text(row.get("source_trace_path"))).exists():
        issues.append("source_trace_path_missing")
    return not issues, issues


def choose_replacements(
    project_root: Path,
    ranked_rows: list[dict[str, str]],
    current_source_ids: set[str],
    needed: int,
    normalized: dict[str, dict[str, Any]],
    edges: dict[str, list[dict[str, Any]]],
    static_services: dict[str, dict[str, Any]],
    static_apis: dict[str, dict[str, Any]],
    service_to_apis: dict[str, list[str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    selected: list[dict[str, Any]] = []
    trace_rows: list[dict[str, Any]] = []
    candidates = sorted(
        [
            row for row in ranked_rows
            if text(row.get("evidence_status")) == "strong_objective_evidence_available"
            and text(row.get("source_task_id")) not in current_source_ids
        ],
        key=lambda row: (int_value(row.get("evidence_rank")) or 10**9, text(row.get("source_task_id"))),
    )
    for ranked in candidates:
        if len(selected) >= needed:
            break
        trace_id = text(ranked.get("trace_record_id"))
        normalized_item = normalized.get(trace_id)
        edge_items = edges.get(trace_id, [])
        trace = {
            "evidence_rank": text(ranked.get("evidence_rank")),
            "source_task_id": text(ranked.get("source_task_id")),
            "trace_record_id": trace_id,
            "source_group": text(ranked.get("source_group")),
            "source_file": text(ranked.get("source_file")),
            "source_record_path": text(ranked.get("source_record_path")),
            "selection_status": "",
            "rejection_reason": "",
            "service_candidate_space_status": "",
            "api_candidate_space_status": "",
            "candidate_service_count": "",
            "candidate_api_count": "",
            "gold_service_count": "",
            "gold_api_count": "",
            "selected_replacement_index": "",
        }
        if not normalized_item or not edge_items:
            trace["selection_status"] = "rejected"
            trace["rejection_reason"] = "stored_normalized_steps_or_dependency_edges_missing"
            trace_rows.append(trace)
            continue
        source_review = synthetic_source_review(ranked, normalized_item, edge_items)
        try:
            built, _, _, build_issues = prep.build_underlying_rows(
                project_root,
                {},
                [source_review],
                [ranked],
                static_services,
                static_apis,
                service_to_apis,
            )
        except Exception as exc:
            trace["selection_status"] = "rejected"
            trace["rejection_reason"] = f"construction_exception:{type(exc).__name__}:{exc}"
            trace_rows.append(trace)
            continue
        if len(built) != 1:
            trace["selection_status"] = "rejected"
            trace["rejection_reason"] = "construction_did_not_return_one_row"
            trace_rows.append(trace)
            continue
        row = built[0]
        trace.update({
            "service_candidate_space_status": row["service_candidate_space_status"],
            "api_candidate_space_status": row["api_candidate_space_status"],
            "candidate_service_count": row["candidate_service_count"],
            "candidate_api_count": row["candidate_api_count"],
            "gold_service_count": row["gold_service_count"],
            "gold_api_count": row["gold_api_count"],
        })
        valid, validation_issues = row_structurally_valid(row)
        if not valid:
            trace["selection_status"] = "rejected"
            trace["rejection_reason"] = "|".join(validation_issues)
            trace_rows.append(trace)
            continue
        replacement_index = len(selected) + 1
        row["underlying_task_id"] = f"COMPOSABLE-UNDERLYING-V0.3.1-REPL-{replacement_index:04d}"
        row["paired_task_group_id"] = f"COMPOSABLE-PAIR-V0.3.1-REPL-{replacement_index:04d}"
        row["split_group_id"] = f"TOOLBENCH-{row['source_task_id']}"
        row["replacement_evidence_rank"] = text(ranked.get("evidence_rank"))
        selected.append(row)
        trace["selection_status"] = "selected"
        trace["selected_replacement_index"] = replacement_index
        trace_rows.append(trace)
    if len(selected) != needed:
        raise RuntimeError(f"Needed {needed} valid replacements, found {len(selected)}")
    return selected, trace_rows


def deterministic_order(items: list[dict[str, Any]], source_task_id: str, key_field: str) -> list[dict[str, Any]]:
    return sorted(
        items,
        key=lambda item: (
            hashlib.sha256(f"{ORDER_SEED}|{source_task_id}|{text(item.get(key_field))}".encode("utf-8")).hexdigest(),
            text(item.get(key_field)),
        ),
    )


def freeze_master_row(row: dict[str, Any], frozen_at: str) -> dict[str, Any]:
    frozen = dict(row)
    services = deterministic_order(parse_json(frozen.get("candidate_services_json"), []), frozen["source_task_id"], "service_key")
    apis = deterministic_order(parse_json(frozen.get("candidate_apis_json"), []), frozen["source_task_id"], "function_key")
    mapping_items = parse_json(frozen.get("service_api_map_json"), [])
    mapping_by_function = {text(item.get("function_key")): item for item in mapping_items if isinstance(item, dict)}
    ordered_mapping = [mapping_by_function[text(api.get("function_key"))] for api in apis if text(api.get("function_key")) in mapping_by_function]
    frozen["candidate_services_json"] = json_dumps(services)
    frozen["candidate_apis_json"] = json_dumps(apis)
    frozen["service_api_map_json"] = json_dumps(ordered_mapping)
    frozen["candidate_service_count"] = len(services)
    frozen["candidate_api_count"] = len(apis)
    frozen["candidate_order_seed"] = ORDER_SEED
    frozen["pack_version"] = VERSION
    frozen["pack_frozen_at"] = frozen_at
    frozen["pack_status"] = "READY_FOR_SINGLE_CONSOLIDATED_HUMAN_REVIEW"
    frozen["review_content_hash"] = prep.review_hash(frozen)
    return frozen


def build_final_review_pack(
    final_master: list[dict[str, Any]],
    old_review_by_source: dict[str, dict[str, str]],
    frozen_at: str,
) -> list[dict[str, Any]]:
    rows = prep.build_review_pack(final_master)
    for index, row in enumerate(rows, start=1):
        old = old_review_by_source.get(row["source_task_id"])
        row["review_item_id"] = f"COMPOSABLE-PAIRED-REVIEW-V0.3.1-{index:04d}"
        row["prior_review_content_hash"] = text(old.get("review_content_hash")) if old else ""
        row["prior_review_credit_status"] = "draft_hash_superseded_no_human_review" if old else "new_replacement_no_prior_review"
        row["candidate_order_seed"] = ORDER_SEED
        row["pack_version"] = VERSION
        row["pack_frozen_at"] = frozen_at
        row["pack_status"] = "READY_FOR_SINGLE_CONSOLIDATED_HUMAN_REVIEW"
        row["current_322_member"] = text(final_master[index - 1].get("current_322_member"))
        for field in prep.REVIEW_HUMAN_FIELDS:
            row[field] = ""
    return rows


def build_double_subset(review_rows: list[dict[str, Any]], size: int = 40) -> list[dict[str, Any]]:
    def stratum(row: dict[str, Any]) -> str:
        dependency_types = parse_json(row.get("dependency_type_distribution_json"), {})
        dominant = sorted(dependency_types.items(), key=lambda item: (-int(item[1]), item[0]))[0][0] if dependency_types else "unknown"
        service_count = int_value(row.get("gold_service_count"))
        api_count = int_value(row.get("gold_api_count"))
        service_bucket = "s1" if service_count <= 1 else ("s2" if service_count == 2 else "s3plus")
        api_bucket = "a2" if api_count <= 2 else ("a3" if api_count == 3 else "a4plus")
        return f"{text(row.get('catalog_domain_signature')) or 'unknown'}|{dominant}|{service_bucket}|{api_bucket}"

    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in review_rows:
        buckets[stratum(row)].append(row)
    for key in buckets:
        buckets[key].sort(key=lambda row: hashlib.sha256(f"{DOUBLE_SEED}|{row['source_task_id']}".encode("utf-8")).hexdigest())
    selected: list[dict[str, Any]] = []
    keys = sorted(buckets)
    while len(selected) < size and any(buckets.values()):
        for key in keys:
            if buckets[key] and len(selected) < size:
                item = dict(buckets[key].pop(0))
                item["double_annotation_stratum"] = key
                item["double_annotation_seed"] = DOUBLE_SEED
                selected.append(item)
    for row in selected:
        for field in prep.REVIEW_HUMAN_FIELDS:
            row[f"reviewer_a_{field}"] = ""
            row[f"reviewer_b_{field}"] = ""
    return selected


def build_ledger(
    old_review_rows: list[dict[str, str]],
    final_review_rows: list[dict[str, Any]],
    reserve_source_ids: set[str],
) -> list[dict[str, Any]]:
    old_by_source = {row["source_task_id"]: row for row in old_review_rows}
    final_by_source = {row["source_task_id"]: row for row in final_review_rows}
    source_ids = sorted(set(old_by_source) | set(final_by_source))
    ledger: list[dict[str, Any]] = []
    for source_id in source_ids:
        old = old_by_source.get(source_id)
        final = final_by_source.get(source_id)
        ledger.append({
            "underlying_task_id": text(final.get("underlying_task_id")) if final else text(old.get("underlying_task_id")),
            "source_task_id": source_id,
            "old_draft_hash": text(old.get("review_content_hash")) if old else "",
            "final_review_hash": text(final.get("review_content_hash")) if final else "",
            "changed_due_to_api_candidate_repair": "false",
            "changed_due_to_replacement": prep.bool_text((old is None) or (source_id in reserve_source_ids)),
            "final_pack_member": prep.bool_text(final is not None),
            "reviewed": "false",
            "invalidated_by_content_change": "false",
        })
    return ledger


def validate_final_pack(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, str]]]:
    issues: list[dict[str, str]] = []
    human_blank_count = 0
    for row in rows:
        valid, row_issues = row_structurally_valid(row)
        if not valid:
            for issue in row_issues:
                issues.append({"review_item_id": row["review_item_id"], "source_task_id": row["source_task_id"], "issue": issue})
        if any(text(row.get(field)) for field in prep.REVIEW_HUMAN_FIELDS):
            issues.append({"review_item_id": row["review_item_id"], "source_task_id": row["source_task_id"], "issue": "human_field_nonblank"})
        else:
            human_blank_count += 1
        if prep.review_hash(row) != text(row.get("review_content_hash")):
            issues.append({"review_item_id": row["review_item_id"], "source_task_id": row["source_task_id"], "issue": "review_hash_mismatch"})
        if text(row.get("candidate_order_seed")) != ORDER_SEED:
            issues.append({"review_item_id": row["review_item_id"], "source_task_id": row["source_task_id"], "issue": "candidate_order_seed_missing"})
    metrics = {
        "final_review_pack_rows": len(rows),
        "final_unique_underlying_tasks": len({text(row.get("underlying_task_id")) for row in rows}),
        "final_query_nonempty_count": sum(1 for row in rows if text(row.get("query_text"))),
        "final_dependency_evidence_nonempty_count": sum(1 for row in rows if parse_json(row.get("dependency_evidence_json"), {})),
        "final_service_candidate_valid_count": sum(1 for row in rows if text(row.get("service_candidate_space_status")) == "valid"),
        "final_api_candidate_valid_count": sum(1 for row in rows if text(row.get("api_candidate_space_status")) == "valid"),
        "final_human_fields_blank_count": human_blank_count,
        "final_review_hash_count": sum(1 for row in rows if len(text(row.get("review_content_hash"))) == 64),
        "gold_services_strict_subset_count": sum(1 for row in rows if set(candidate_keys(row, "provisional_gold_services_json", "service_key")) < set(candidate_keys(row, "candidate_services_json", "service_key"))),
        "gold_apis_strict_subset_count": sum(1 for row in rows if set(candidate_keys(row, "provisional_gold_apis_json", "function_key")) < set(candidate_keys(row, "candidate_apis_json", "function_key"))),
        "api_parent_mapping_valid_count": sum(1 for row in rows if api_parent_mapping_valid(row)),
        "api_negative_distractor_positive_count": sum(1 for row in rows if int_value(row.get("api_negative_distractor_count")) > 0),
        "fatal_issue_count": len(issues),
    }
    return metrics, issues


def update_master_plan(path: Path, generated_at: str, repaired: int, replaced: int) -> None:
    content = path.read_text(encoding="utf-8-sig")
    old_pattern = r"<!-- BEGIN GATE4 V0\.3 VARIABLE STATUS -->.*?<!-- END GATE4 V0\.3 VARIABLE STATUS -->"
    block = f"""<!-- BEGIN GATE4 V0.3.1 VARIABLE STATUS -->

### v0.3.1 variable status ({generated_at})

- Gate 4 status: `FINAL_CONSOLIDATED_REVIEW_PACK_FROZEN_HUMAN_REVIEW_READY`;
- strong candidate reserve: `816`;
- final consolidated review pack: `200`;
- API structural issues repaired: `{repaired}`;
- API structural issues replaced: `{replaced}`;
- human-confirmed count: `0`;
- stopping condition: `both-level eligible underlying compositions >= 100`;
- current next action: `humanly review only composable_paired_task_review_items_v0_3_1.csv`;
- v0.3 draft and all older composable packs are superseded for review but retained for provenance;
- human-final authority and all frozen benchmark-only constraints remain unchanged.

<!-- END GATE4 V0.3.1 VARIABLE STATUS -->"""
    if re.search(old_pattern, content, flags=re.DOTALL):
        content = re.sub(old_pattern, block, content, count=1, flags=re.DOTALL)
    elif "<!-- BEGIN GATE4 V0.3.1 VARIABLE STATUS -->" not in content:
        raise RuntimeError("Gate 4 v0.3 variable-status markers not found")
    gate_start = content.find("## Gate 4")
    gate_end = content.find("## Gate 5", gate_start)
    if gate_start >= 0 and gate_end > gate_start:
        section = content[gate_start:gate_end]
        section = re.sub(
            r"(状态：)`[^`]+`",
            r"\1`FINAL_CONSOLIDATED_REVIEW_PACK_FROZEN_HUMAN_REVIEW_READY`",
            section,
            count=1,
        )
        content = content[:gate_start] + section + content[gate_end:]
    changelog = f"""## v1.4 - 2026-07-14

- Updated only Gate 4 variable status and retained all frozen benchmark-only constraints.
- Resolved the nine v0.3 API structural holds with exact local-catalog repair where possible and deterministic replacement from the existing 816 strong-evidence pool otherwise.
- Froze one 200-row v0.3.1 consolidated human review pack with fixed gold-agnostic candidate order and recalculated content hashes.
- Human-confirmed count remains `0`; full six-task assembly, final dataset, split, baseline, and training remain prohibited.

"""
    marker = "# 13. Change Log\n"
    if "## v1.4 - 2026-07-14" not in content:
        if marker not in content:
            raise RuntimeError("Master Plan Change Log heading not found")
        content = content.replace(marker, marker + "\n" + changelog, 1)
    path.write_text(content, encoding="utf-8")


def write_go_no_go(path: Path, summary: dict[str, Any], input_paths: dict[str, Path]) -> None:
    lines = [
        "# Composable Final Review Pack Freeze Go/No-Go v0.3.1",
        "",
        f"Generated at: `{summary['generated_at']}`",
        "",
        "## Inputs",
        "",
    ]
    lines.extend(f"- `{name}`: `{value}`" for name, value in input_paths.items())
    lines.extend(["", "## Fixed Metrics", ""])
    keys = [
        "strong_underlying_candidate_count", "input_review_pack_rows", "initial_api_invalid_count",
        "api_rows_repaired_count", "api_rows_replaced_count", "api_rows_still_invalid_count",
        "final_review_pack_rows", "final_unique_underlying_tasks", "final_query_nonempty_count",
        "final_dependency_evidence_nonempty_count", "final_service_candidate_valid_count",
        "final_api_candidate_valid_count", "final_human_fields_blank_count", "final_review_hash_count",
        "double_annotation_subset_rows", "original_8_in_final_pack_count",
        "original_8_in_reconstruction_reserve_count", "human_confirmed_composable_count",
        "can_start_single_consolidated_human_review", "can_claim_composable_service_benchmark_now",
        "can_claim_composable_api_benchmark_now", "can_start_full_six_task_assembly",
        "can_generate_final_dataset",
    ]
    for key in keys:
        value = summary[key]
        if isinstance(value, bool):
            value = str(value).lower()
        lines.append(f"- {key} = `{value}`")
    lines.extend([
        "",
        "## Decision",
        "",
        f"- decision = `{'GO_SINGLE_CONSOLIDATED_HUMAN_REVIEW' if summary['can_start_single_consolidated_human_review'] else 'NO_GO_FIX_FATAL_STRUCTURE'}`",
        f"- recommended_next_step = `{summary['recommended_next_step']}`",
        "",
        "No semantic QA or final composable label was assigned. All human fields remain blank, and v0.3 remains preserved only as draft provenance.",
    ])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_change_log(path: Path, master_plan: Path, generated_at: str, repaired: int, replaced: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""# Master Plan Gate 4 Change Log v0.3.1

Generated at: `{generated_at}`

Updated file: `{master_plan}`

- Gate 4 is `FINAL_CONSOLIDATED_REVIEW_PACK_FROZEN_HUMAN_REVIEW_READY`.
- Strong candidate reserve remains `816`; final frozen review pack contains `200` rows.
- API rows repaired by exact local mapping: `{repaired}`; rows replaced from the existing strong pool: `{replaced}`.
- Human-confirmed count remains `0`.
- Stop condition remains `both-level eligible underlying compositions >= 100`.
- Frozen benchmark scope, human-final authority, and downstream prohibitions were not changed.
""",
        encoding="utf-8",
    )


def archive_files(archive_dir: Path, paths: Iterable[Path]) -> None:
    archive_dir.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, Any]] = []
    for path in paths:
        if not path.exists() or not path.is_file():
            continue
        destination = archive_dir / path.name
        shutil.copy2(path, destination)
        manifest.append({
            "source_path": str(path),
            "archived_name": destination.name,
            "bytes": destination.stat().st_size,
            "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        })
    write_json(archive_dir / "archive_manifest.json", {"generated_at": now_iso(), "files": manifest})


def parse_args() -> argparse.Namespace:
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description="Repair/replace invalid v0.3 API rows and freeze v0.3.1 review pack.")
    parser.add_argument("--project-root", type=Path, default=root)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_RELATIVE)
    parser.add_argument("--archive-dir", type=Path, default=ARCHIVE_RELATIVE)
    parser.add_argument("--ledger-path", type=Path, default=LEDGER_RELATIVE)
    parser.add_argument("--replacement-scan-limit", type=int, default=250)
    return parser.parse_args()


def resolve(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def main() -> int:
    args = parse_args()
    root = args.project_root.resolve()
    output_dir = resolve(root, args.output_dir)
    archive_dir = resolve(root, args.archive_dir)
    ledger_path = resolve(root, args.ledger_path)
    paths = resolve_inputs(root, output_dir)

    master_rows = read_csv(paths["master_v0_3"])
    service_rows_v0_3 = read_csv(paths["service_v0_3"])
    api_rows_v0_3 = read_csv(paths["api_v0_3"])
    review_rows = read_csv(paths["review_v0_3"])
    ranked_rows = read_csv(paths["ranked_v0_2"])
    source_review_rows = read_csv(paths["source_review_v0_2"])
    if not all(len(rows) == 200 for rows in (master_rows, service_rows_v0_3, api_rows_v0_3, review_rows)):
        raise ValueError("All four v0.3 pack inputs must contain exactly 200 rows")
    original_8_ids = {
        row["source_task_id"] for row in source_review_rows if truthy(row.get("current_322_member"))
    }
    if len(original_8_ids) != 8:
        raise ValueError(f"Expected 8 preserved original strong candidates, found {len(original_8_ids)}")

    invalid_input_rows = build_invalid_input_rows(review_rows, original_8_ids)
    if len(invalid_input_rows) != 9:
        raise ValueError(f"Expected 9 initial API-invalid rows, found {len(invalid_input_rows)}")
    invalid_path = output_dir / "api_candidate_space_invalid_9_input.csv"
    write_csv(invalid_path, invalid_input_rows)

    static_services, static_apis, service_to_apis, catalog_stats = prep.load_static_catalog(paths["catalog"])
    master_by_source = {row["source_task_id"]: row for row in master_rows}
    review_by_source = {row["source_task_id"]: row for row in review_rows}
    repaired_by_source: dict[str, dict[str, Any]] = {}
    repair_trace: list[dict[str, Any]] = []
    reserve_source_ids: set[str] = set()
    for invalid in invalid_input_rows:
        source_id = invalid["source_task_id"]
        updated, trace = exact_catalog_repair(master_by_source[source_id], static_apis)
        repair_trace.append(trace)
        if trace["post_repair_candidate_space_valid"] == "true":
            repaired_by_source[source_id] = updated
        else:
            reserve_source_ids.add(source_id)
    repair_trace_path = output_dir / "api_candidate_repair_trace.csv"
    write_csv(repair_trace_path, repair_trace)

    reserve_rows: list[dict[str, Any]] = []
    for source_id in sorted(reserve_source_ids, key=lambda value: review_by_source[value]["review_item_id"]):
        row = dict(review_by_source[source_id])
        row["reserve_reason"] = "deterministic_exact_parent_mapping_unavailable"
        row["reserve_status"] = "API_RECONSTRUCTION_OR_SOURCE_ADJUDICATION_REQUIRED"
        row["superseded_for_v0_3_1_final_review_pack"] = "true"
        reserve_rows.append(row)
    reserve_path = output_dir / "composable_api_reconstruction_reserve.csv"
    write_csv(reserve_path, reserve_rows)

    current_source_ids = {row["source_task_id"] for row in review_rows}
    remaining_ranked = sorted(
        [
            row for row in ranked_rows
            if text(row.get("evidence_status")) == "strong_objective_evidence_available"
            and row["source_task_id"] not in current_source_ids
        ],
        key=lambda row: (int_value(row.get("evidence_rank")) or 10**9, row["source_task_id"]),
    )
    scan_rows = remaining_ranked[: args.replacement_scan_limit]
    trace_ids = {text(row.get("trace_record_id")) for row in scan_rows}
    normalized, edges = read_selected_evidence(paths["steps_v0_2"], paths["edges_v0_2"], trace_ids)
    replacements, replacement_trace = choose_replacements(
        root,
        scan_rows,
        current_source_ids,
        len(reserve_source_ids),
        normalized,
        edges,
        static_services,
        static_apis,
        service_to_apis,
    )
    replacement_trace_path = output_dir / "replacement_candidate_trace.csv"
    write_csv(replacement_trace_path, replacement_trace)

    replacement_iter = iter(replacements)
    final_master_unfrozen: list[dict[str, Any]] = []
    repaired_source_ids = set(repaired_by_source)
    for old_review in review_rows:
        source_id = old_review["source_task_id"]
        if source_id in repaired_by_source:
            final_master_unfrozen.append(repaired_by_source[source_id])
        elif source_id in reserve_source_ids:
            replacement = dict(next(replacement_iter))
            replacement["replaced_v0_3_source_task_id"] = source_id
            replacement["replacement_reason"] = "v0_3_gold_api_parent_mapping_not_deterministically_repairable"
            final_master_unfrozen.append(replacement)
        else:
            final_master_unfrozen.append(dict(master_by_source[source_id]))

    frozen_at = now_iso()
    final_master = [freeze_master_row(row, frozen_at) for row in final_master_unfrozen]
    old_review_by_source = {row["source_task_id"]: row for row in review_rows}
    final_review = build_final_review_pack(final_master, old_review_by_source, frozen_at)
    metrics, fatal_issues = validate_final_pack(final_review)

    final_master_path = output_dir / "composable_underlying_tasks_master_v0_3_1.csv"
    final_review_path = output_dir / "composable_paired_task_review_items_v0_3_1.csv"
    write_csv(final_master_path, final_master)
    write_csv(final_review_path, final_review)
    final_service_rows, final_api_rows = prep.build_provisional_rows(final_master)
    for index, row in enumerate(final_service_rows, start=1):
        row["benchmark_task_id"] = f"CSD-V0.3.1-{index:04d}"
        row["candidate_order_seed"] = ORDER_SEED
        row["pack_version"] = VERSION
        row["pack_frozen_at"] = frozen_at
        row["pack_status"] = "READY_FOR_SINGLE_CONSOLIDATED_HUMAN_REVIEW"
    for index, row in enumerate(final_api_rows, start=1):
        row["benchmark_task_id"] = f"CAR-V0.3.1-{index:04d}"
        row["candidate_order_seed"] = ORDER_SEED
        row["pack_version"] = VERSION
        row["pack_frozen_at"] = frozen_at
        row["pack_status"] = "READY_FOR_SINGLE_CONSOLIDATED_HUMAN_REVIEW"
    final_service_path = output_dir / "composable_service_discovery_provisional_rows_v0_3_1.csv"
    final_api_path = output_dir / "composable_api_recommendation_provisional_rows_v0_3_1.csv"
    write_csv(final_service_path, final_service_rows)
    write_csv(final_api_path, final_api_rows)

    double_rows = build_double_subset(final_review, 40)
    double_path = output_dir / "composable_double_annotation_subset_40_v0_3_1.csv"
    write_csv(double_path, double_rows)
    old_subset_note = output_dir / "v0_3_double_annotation_subset_superseded_notice.md"
    old_subset_note.write_text(
        "# Superseded subset notice\n\nThe v0.3 40-row subset is retained at its original path for provenance but must not be reviewed. Use `composable_double_annotation_subset_40_v0_3_1.csv`.\n",
        encoding="utf-8",
    )

    ledger_rows = build_ledger(review_rows, final_review, reserve_source_ids)
    write_csv(ledger_path, ledger_rows)
    issues_path = output_dir / "composable_final_review_pack_fatal_issues_v0_3_1.csv"
    write_csv(issues_path, fatal_issues, ["review_item_id", "source_task_id", "issue"])

    final_source_ids = {row["source_task_id"] for row in final_review}
    summary = {
        "generated_at": frozen_at,
        "strong_underlying_candidate_count": 816,
        "input_review_pack_rows": len(review_rows),
        "initial_api_invalid_count": len(invalid_input_rows),
        "api_rows_repaired_count": len(repaired_source_ids),
        "api_rows_replaced_count": len(reserve_source_ids),
        "api_rows_still_invalid_count": metrics["fatal_issue_count"],
        **metrics,
        "original_8_in_final_pack_count": len(original_8_ids & final_source_ids),
        "original_8_in_reconstruction_reserve_count": len(original_8_ids & reserve_source_ids),
        "double_annotation_subset_rows": len(double_rows),
        "human_confirmed_composable_count": 0,
        "catalog_unique_service_count": catalog_stats["service_count"],
        "catalog_unique_api_count": catalog_stats["api_count"],
        "candidate_order_seed": ORDER_SEED,
    }
    required_200 = [
        "final_review_pack_rows", "final_unique_underlying_tasks", "final_query_nonempty_count",
        "final_dependency_evidence_nonempty_count", "final_service_candidate_valid_count",
        "final_api_candidate_valid_count", "final_human_fields_blank_count", "final_review_hash_count",
        "gold_services_strict_subset_count", "gold_apis_strict_subset_count",
        "api_parent_mapping_valid_count", "api_negative_distractor_positive_count",
    ]
    can_start = all(summary[key] == 200 for key in required_200) and summary["fatal_issue_count"] == 0
    summary.update({
        "can_start_single_consolidated_human_review": can_start,
        "can_claim_composable_service_benchmark_now": False,
        "can_claim_composable_api_benchmark_now": False,
        "can_start_full_six_task_assembly": False,
        "can_generate_final_dataset": False,
        "recommended_next_step": "humanly review only composable_paired_task_review_items_v0_3_1.csv; do not review v0.3 draft, old G3 packs, old 8-row packs, or evidence-only packs.",
    })
    summary_path = output_dir / "composable_final_review_pack_freeze_summary_v0_3_1.json"
    write_json(summary_path, summary)
    go_no_go_path = root / "docs/phase1/composable_final_review_pack_freeze_go_no_go_v0_3_1.md"
    write_go_no_go(go_no_go_path, summary, paths)
    if not can_start:
        raise RuntimeError(f"v0.3.1 final pack failed structural freeze gate: fatal_issue_count={len(fatal_issues)}")

    update_master_plan(paths["master_plan"], frozen_at, len(repaired_source_ids), len(reserve_source_ids))
    change_log_path = root / "docs/phase1/composable_final_review_pack_master_plan_change_log_v0_3_1.md"
    write_change_log(change_log_path, paths["master_plan"], frozen_at, len(repaired_source_ids), len(reserve_source_ids))

    archive_files(archive_dir, [
        invalid_path, repair_trace_path, replacement_trace_path, reserve_path,
        final_master_path, final_service_path, final_api_path, final_review_path,
        double_path, old_subset_note, ledger_path, issues_path, summary_path,
        go_no_go_path, change_log_path, paths["master_plan"], Path(__file__).resolve(),
    ])

    terminal_fields = [
        "initial_api_invalid_count", "api_rows_repaired_count", "api_rows_replaced_count",
        "api_rows_still_invalid_count", "final_review_pack_rows", "final_unique_underlying_tasks",
        "final_query_nonempty_count", "final_dependency_evidence_nonempty_count",
        "final_service_candidate_valid_count", "final_api_candidate_valid_count",
        "final_human_fields_blank_count", "final_review_hash_count",
        "original_8_in_final_pack_count", "original_8_in_reconstruction_reserve_count",
        "double_annotation_subset_rows", "can_start_single_consolidated_human_review",
        "can_claim_composable_service_benchmark_now", "can_claim_composable_api_benchmark_now",
        "can_start_full_six_task_assembly", "can_generate_final_dataset", "recommended_next_step",
    ]
    for field in terminal_fields:
        print(f"{field}={summary[field]}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
