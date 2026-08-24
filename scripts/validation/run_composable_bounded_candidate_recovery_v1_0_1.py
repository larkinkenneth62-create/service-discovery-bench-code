#!/usr/bin/env python3
"""Run bounded, deterministic composable candidate recovery without raw rescan."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

import composable_candidate_recovery_v1_0_1 as recovery
import composable_task_necessity_gate_v0_3_3 as gate
import prepare_composable_paired_tasks_v0_3 as prep
import run_composable_dependency_extractor_patch_v0_3_2 as v032
import run_composable_task_necessity_patch_v0_3_3 as v033


VERSION = "v1.0.1"
POOL_TARGET = 140
POOL_MAX = 150
BATCH_SIZE = 120
BATCH_SEED = "sdbench-composable-bounded-review-batch-v1.0.1"
DOUBLE_SEED = "sdbench-composable-bounded-double-v1.0.1"

DEFAULT_OUTPUT = Path("outputs/composable_candidate_recovery_v1_0_1")
DEFAULT_LEDGER = Path("outputs/review_credit_ledger/composable_review_credit_ledger_v1_0_1.csv")
DEFAULT_REPORT = Path("docs/phase1/composable_bounded_candidate_recovery_go_no_go_v1_0_1.md")
DEFAULT_ARCHIVE = Path("outputs/run_archives/2026-07-15_composable_bounded_candidate_recovery_v1_0_1")

INPUTS = {
    "rules": Path("docs/project/SERVICEDISCOVERYBENCH_COMPOSABLE_MACHINE_REVIEW_RULES.md"),
    "old_review": Path("outputs/composable_paired_task_preparation_v0_3_2/composable_paired_task_review_items_v0_3_2.csv"),
    "old_audit": Path("outputs/composable_task_necessity_patch_v0_3_3/v0_3_2_task_necessity_reaudit.csv"),
    "current_review": Path("outputs/composable_paired_task_preparation_v0_3_3/composable_paired_task_review_items_v0_3_3.csv"),
    "current_summary": Path("outputs/composable_task_necessity_patch_v0_3_3/composable_task_necessity_patch_summary_v0_3_3.json"),
    "reserve_audit": Path("outputs/composable_task_necessity_patch_v0_3_3/corrected_strong_reserve_necessity_prefilter.csv"),
    "corrected_ranked": Path("outputs/composable_dependency_extractor_patch_v0_3_2/corrected_underlying_task_candidates_ranked.csv"),
    "corrected_edges": Path("outputs/composable_dependency_extractor_patch_v0_3_2/corrected_dependency_edge_candidates.jsonl"),
    "normalized": Path("outputs/composable_corpus_mining_v0_2/toolbench_full_normalized_multicall_steps.jsonl"),
    "catalog": Path("external_sources/ToolBench/data/toolenv/tools"),
    "prior_candidate_invalid": Path("outputs/composable_paired_task_preparation_v0_3_1/api_candidate_space_invalid_9_input.csv"),
    "prior_candidate_repair": Path("outputs/composable_paired_task_preparation_v0_3_1/api_candidate_repair_trace.csv"),
    "tests": Path("tests/validation/test_composable_candidate_recovery_v1_0_1.py"),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bounded composable candidate recovery under frozen machine rules v1.0."
    )
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--archive-dir", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--pool-target", type=int, default=POOL_TARGET)
    parser.add_argument("--pool-max", type=int, default=POOL_MAX)
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    parser.add_argument("--skip-archive", action="store_true")
    return parser.parse_args()


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"CSV has no header: {path}")
        return list(reader), list(reader.fieldnames)


def ordered_fields(rows: list[dict[str, Any]], preferred: Iterable[str] = ()) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for field in preferred:
        if field and field not in seen:
            seen.add(field)
            result.append(field)
    for row in rows:
        for field in row:
            if field not in seen:
                seen.add(field)
                result.append(field)
    return result


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = fields or ordered_fields(rows)
    if not columns:
        raise ValueError(f"Cannot write CSV without columns: {path}")
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in columns})


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def resolve_inputs(root: Path) -> dict[str, Path]:
    paths = {key: (root / value).resolve() for key, value in INPUTS.items()}
    missing = [f"{key}: {path}" for key, path in paths.items() if not path.exists()]
    if missing:
        raise FileNotFoundError("Missing required inputs:\n" + "\n".join(missing))
    return paths


def run_tests(root: Path, test_path: Path, output_dir: Path) -> dict[str, int]:
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, str(test_path), "-v"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    output = (result.stdout + "\n" + result.stderr).strip() + "\n"
    (output_dir / "composable_candidate_recovery_test_results_v1_0_1.txt").write_text(
        output, encoding="utf-8"
    )
    if result.returncode != 0:
        raise RuntimeError("Candidate recovery tests failed")
    return {"tests_run": output.count(" ... ok"), "tests_failed": 0}


def canonical_reasons(row: dict[str, Any]) -> list[str]:
    if recovery.text(row.get("stage")) == "trace_prefilter":
        raw = recovery.text(row.get("rejection_reason"))
        reasons = raw.split("|") if raw else []
    else:
        reasons = recovery.parse_json(row.get("structural_ineligibility_reasons_json"), [])
        if not reasons and recovery.text(row.get("rejection_reason")):
            reasons = recovery.text(row.get("rejection_reason")).split("|")
    return sorted({recovery.canonical_reason(item) for item in reasons if recovery.text(item)})


def reason_flags(reasons: list[str]) -> dict[str, bool]:
    values = set(reasons)
    return {
        "reason_gold_service_count_lt_2": "gold_service_count_lt_2" in values,
        "reason_same_service_only": "same_service_only_dependency" in values,
        "reason_no_cross_service_edge": "no_cross_service_strong_edge" in values,
        "reason_failed_dependency": bool(
            values & {"failed_or_error_dependency_edge", "failed_or_error_gold_call"}
        ),
        "reason_redundancy_only": "only_redundant_recomputation_dependency" in values,
        "reason_shared_or_sequence_only": bool(values & recovery.SHARED_OR_SEQUENCE_REASONS),
        "reason_exact_service_leak": "exact_blocking_service_name_leak" in values,
        "reason_exact_api_leak": "exact_blocking_api_name_leak" in values,
        "reason_service_candidate_invalid": "service_candidate_space_invalid" in values,
        "reason_api_candidate_invalid": "api_candidate_space_invalid" in values,
        "reason_source_unavailable": bool(values & {"source_unavailable", "source_mapping_unresolved"}),
    }


def reconcile_candidates(
    old_audit: list[dict[str, str]],
    reserve_audit: list[dict[str, str]],
    current_ids: set[str],
    ranked_by_source: dict[str, dict[str, str]],
    output_dir: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for origin, source_rows in (("v0_3_2_pack", old_audit), ("corrected_strong_reserve", reserve_audit)):
        for source in source_rows:
            source_id = source["source_task_id"]
            reasons = canonical_reasons(source)
            ranked = ranked_by_source.get(source_id, {})
            source_available = bool(
                recovery.text(source.get("query_text") or ranked.get("query_text"))
                and recovery.text(source.get("dependency_edges_json") or source.get("cross_service_edges_json") or ranked.get("trace_record_id"))
            ) and "source_mapping_unresolved" not in reasons
            status = recovery.classify_recovery(
                reasons,
                authoritative_existing=source_id in current_ids,
                source_available=source_available,
            )
            rows.append(
                {
                    "source_task_id": source_id,
                    "source_group": source.get("source_group") or ranked.get("source_group", ""),
                    "origin": origin,
                    "trace_record_id": source.get("trace_record_id") or ranked.get("trace_record_id", ""),
                    "query_text": source.get("query_text") or ranked.get("query_text", ""),
                    "audit_stage": source.get("stage", "v0_3_2_full_gate"),
                    "primary_recovery_status": status,
                    "canonical_reasons_json": recovery.json_dumps(reasons),
                    "source_available_for_bounded_recovery": str(source_available).lower(),
                    **{key: str(value).lower() for key, value in reason_flags(reasons).items()},
                    "machine_review_status": source.get("machine_review_status", ""),
                    "distinct_gold_service_count": source.get("distinct_gold_service_count", ""),
                    "distinct_gold_api_count": source.get("distinct_gold_api_count", ""),
                    "cross_service_strong_edge_count": source.get("cross_service_strong_edge_count", ""),
                    "same_service_strong_edge_count": source.get("same_service_strong_edge_count", ""),
                    "failed_call_dependency_count": source.get("failed_call_dependency_count", ""),
                    "exact_gold_service_name_leak": source.get("exact_gold_service_name_leak", ""),
                    "exact_gold_api_name_leak": source.get("exact_gold_api_name_leak", ""),
                    "service_candidate_space_structurally_valid": source.get("service_candidate_space_structurally_valid", ""),
                    "api_candidate_space_structurally_valid": source.get("api_candidate_space_structurally_valid", ""),
                }
            )

    source_counts = Counter(row["source_task_id"] for row in rows)
    require(all(count == 1 for count in source_counts.values()), "Candidate reconciliation contains duplicate tasks")
    status_counts = Counter(row["primary_recovery_status"] for row in rows)
    all_reason_sets = {
        row["source_task_id"]: set(recovery.parse_json(row["canonical_reasons_json"], []))
        for row in rows
    }
    reason_names = sorted({reason for reasons in all_reason_sets.values() for reason in reasons})
    overlap: dict[str, dict[str, int]] = {}
    for left in reason_names:
        overlap[left] = {}
        for right in reason_names:
            overlap[left][right] = sum(
                left in reasons and right in reasons for reasons in all_reason_sets.values()
            )
    summary = {
        "total_unique_candidate_tasks_considered": len(rows),
        "current_authoritative_valid_count": status_counts["AUTHORITATIVE_VALID_EXISTING"],
        "hard_unrecoverable_count": sum(
            count for status, count in status_counts.items() if status.startswith("HARD_UNRECOVERABLE_")
        ),
        "repairable_leak_only_count": status_counts["REPAIRABLE_EXACT_LEAK_ONLY"],
        "repairable_candidate_space_only_count": status_counts["REPAIRABLE_CANDIDATE_SPACE_ONLY"],
        "repairable_leak_plus_candidate_space_count": status_counts["REPAIRABLE_LEAK_AND_CANDIDATE_SPACE"],
        "other_repairable_count": 0,
        "repairable_task_ids_unique_count": sum(
            count for status, count in status_counts.items() if status.startswith("REPAIRABLE_")
        ),
        "primary_recovery_status_distribution": dict(sorted(status_counts.items())),
        "reason_counts": dict(
            sorted(
                Counter(reason for reasons in all_reason_sets.values() for reason in reasons).items()
            )
        ),
        "reason_overlap_matrix": overlap,
    }
    write_csv(output_dir / "candidate_shortage_reconciliation.csv", rows)
    write_json(output_dir / "candidate_shortage_reconciliation.json", summary)
    return rows, summary


def load_targeted_existing_evidence(
    target_ids: set[str],
    ranked_by_source: dict[str, dict[str, str]],
    edges_path: Path,
    normalized_path: Path,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]]]:
    edges: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in iter_jsonl(edges_path):
        source_id = recovery.text(edge.get("source_task_id"))
        if source_id in target_ids:
            edges[source_id].append(edge)
    trace_ids = {
        recovery.text(ranked_by_source[source_id].get("trace_record_id"))
        for source_id in target_ids
        if source_id in ranked_by_source
    }
    normalized = v032.load_selected_normalized(normalized_path, trace_ids)
    return edges, normalized


def candidate_map(apis: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "function_name": recovery.text(item.get("function_name")),
            "api_name": recovery.text(item.get("api_name")),
            "service_name": recovery.text(item.get("service_name")),
            "function_key": recovery.text(item.get("function_key")),
            "service_key": recovery.text(item.get("service_key")),
            "mapping_source": recovery.text(item.get("catalog_source_path")),
        }
        for item in apis
    ]


def rehydrate_reserve_row(
    source_id: str,
    ranked: dict[str, str],
    record: dict[str, Any],
    edges: list[dict[str, Any]],
    static_services: dict[str, dict[str, Any]],
    static_apis: dict[str, dict[str, Any]],
    service_to_apis: dict[str, list[str]],
    template_columns: list[str],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    result = v033.result_from_existing_evidence(record, ranked, edges)
    strong_edges = result["strong_edges"]
    if not strong_edges:
        return None, {"source_task_id": source_id, "rehydration_status": "NO_STRONG_EDGE"}
    steps = record.get("steps", []) if isinstance(record.get("steps"), list) else []
    graph = prep.graph_properties(strong_edges, steps)
    query = recovery.text(record.get("query_text"))
    provenance = f"{ranked.get('source_file','')}#{ranked.get('source_record_path','$')}"
    gold_apis = prep.dedupe_objects(
        [prep.api_for_step(step, {}, static_apis, provenance) for step in graph["gold_steps"]],
        "function_key",
    )
    gold_services = prep.dedupe_objects(
        [
            prep.service_object(
                recovery.text(item.get("service_key")),
                recovery.text(item.get("service_name")),
                static_services,
                recovery.text(item.get("catalog_source_path")),
            )
            for item in gold_apis
        ],
        "service_key",
    )
    incidental_apis = prep.dedupe_objects(
        [prep.api_for_step(step, {}, static_apis, provenance) for step in graph["incidental_steps"]],
        "function_key",
    )
    incidental_services = prep.dedupe_objects(
        [
            prep.service_object(
                recovery.text(item.get("service_key")),
                recovery.text(item.get("service_name")),
                static_services,
                recovery.text(item.get("catalog_source_path")),
            )
            for item in incidental_apis
        ],
        "service_key",
    )
    seed = {column: "" for column in template_columns}
    seed.update(
        {
            "underlying_task_id": f"COMPOSABLE-RECOVERY-SEED-{source_id}",
            "source_task_id": source_id,
            "source_dataset": recovery.text(record.get("source_dataset")) or "ToolBench",
            "source_group": recovery.text(record.get("source_group") or ranked.get("source_group")),
            "source_query_id": recovery.text(ranked.get("instruction_query_id")),
            "query_text": query,
            "query_source_path": f"existing_normalized_evidence:{ranked.get('trace_record_id','')}",
            "source_trace_path": recovery.text(ranked.get("source_file")),
            "source_answer_path": recovery.text(ranked.get("source_file")),
            "source_record_path": recovery.text(ranked.get("source_record_path")) or "$",
            "ordered_steps_json": recovery.json_dumps(steps),
            "dependency_edges_json": recovery.json_dumps(graph["usable_edges"]),
            "dependency_evidence_json": recovery.json_dumps(
                {
                    "machine_evidence_only": True,
                    "bounded_recovery_source": "existing_normalized_and_corrected_edge_evidence",
                    "extractor_version": "v0.3.2",
                    "objective_edge_count": len(strong_edges),
                    "shared_input_values": result["shared_input_values"],
                    "failed_calls": result["failed_calls"],
                }
            ),
            "dependency_type_distribution_json": recovery.json_dumps(
                prep.dependency_type_distribution(graph["usable_edges"])
            ),
            "evidence_status": "strong_objective_evidence_available",
            "evidence_score": ranked.get("evidence_score", ""),
            "provisional_gold_services_json": recovery.json_dumps(gold_services),
            "provisional_gold_apis_json": recovery.json_dumps(gold_apis),
            "provisional_gold_service_api_map_json": recovery.json_dumps(candidate_map(gold_apis)),
            "incidental_services_json": recovery.json_dumps(incidental_services),
            "incidental_apis_json": recovery.json_dumps(incidental_apis),
            "disconnected_calls_json": recovery.json_dumps(graph["incidental_steps"]),
            "dependency_components_json": recovery.json_dumps(graph["components"]),
            "connected_dependency_component_count": graph["component_count"],
            "dependency_graph_is_dag": str(graph["is_dag"]).lower(),
            "dependency_graph_is_connected": str(graph["is_connected"]).lower(),
            "dependency_structure_risk": "hybrid_or_ambiguous" if graph["component_count"] > 1 else "single_connected_dependency_component",
            "requires_human_dependency_confirmation": "true",
            "current_322_member": ranked.get("current_322_member", "false"),
            "catalog_domain_signature": prep.domain_signature(gold_services, gold_apis),
            "strong_edge_count": len(strong_edges),
            "shared_input_values_json": recovery.json_dumps(result["shared_input_values"]),
            "incidental_or_failed_calls_json": recovery.json_dumps(result["failed_calls"]),
            "execution_evidence_incomplete": str(bool(result["execution_evidence_incomplete"])).lower(),
        }
    )
    first = strong_edges[0]
    for field in (
        "edge_source_type", "upstream_field_role", "downstream_field_role",
        "upstream_source_path", "downstream_source_path", "evidence_value",
        "value_present_in_original_query", "value_present_in_upstream_arguments",
        "upstream_output_is_novel", "upstream_output_is_echo",
        "upstream_call_execution_status", "downstream_call_execution_status",
        "strong_edge_eligible",
    ):
        seed[field] = first.get(field, "")
    rebuilt, trace = recovery.reconstruct_candidate_space(
        seed, static_services, static_apis, service_to_apis
    )
    if not rebuilt:
        return None, {"source_task_id": source_id, "rehydration_status": trace["reconstruction_status"], **trace}
    candidates = recovery.parse_json(rebuilt["candidate_apis_json"], [])
    rebuilt["service_api_map_json"] = recovery.json_dumps(candidate_map(candidates))
    gold_keys = {
        recovery.text(item.get("function_key")) for item in gold_apis
    }
    rebuilt["provisional_gold_service_api_map_json"] = recovery.json_dumps(
        [item for item in candidate_map(candidates) if item["function_key"] in gold_keys]
    )
    service_leak, service_signals = prep.leak_status(query, gold_services, "service")
    api_leak, api_signals = prep.leak_status(query, gold_apis, "api")
    rebuilt["service_leak_status"] = service_leak
    rebuilt["service_leak_signals_json"] = recovery.json_dumps(service_signals)
    rebuilt["api_leak_status"] = api_leak
    rebuilt["api_leak_signals_json"] = recovery.json_dumps(api_signals)
    rebuilt["gold_service_count"] = len(gold_services)
    rebuilt["gold_api_count"] = len(gold_apis)
    rebuilt["pack_version"] = VERSION
    rebuilt["machine_rule_spec_version"] = recovery.MACHINE_RULE_SPEC_VERSION
    for field in recovery.HUMAN_FIELDS:
        rebuilt[field] = ""
    assessment = gate.assess_task(rebuilt)
    rebuilt.update(gate.assessment_csv_fields(assessment))
    return rebuilt, {"source_task_id": source_id, "rehydration_status": "REHYDRATED_FROM_EXISTING_EVIDENCE", **trace}


def apply_rewrite_to_row(row: dict[str, Any], rewrite: dict[str, Any]) -> dict[str, Any]:
    updated = dict(row)
    original = rewrite["original_query_text"]
    final = rewrite["proposed_rewritten_query_text"]
    updated["original_query_text"] = original
    updated["final_model_facing_query_text"] = final
    updated["query_text"] = final
    updated["rewrite_provenance_json"] = recovery.json_dumps(
        {
            "version": VERSION,
            "method": "deterministic_exact_name_connector_removal",
            "removed_exact_names": rewrite.get("removed_exact_names", []),
            "rewrite_patterns": rewrite.get("rewrite_patterns", []),
            "llm_used": False,
            "meaning_added": False,
        }
    )
    gold_services = recovery.parse_json(updated.get("provisional_gold_services_json"), [])
    gold_apis = recovery.parse_json(updated.get("provisional_gold_apis_json"), [])
    service_status, service_signals = prep.leak_status(final, gold_services, "service")
    api_status, api_signals = prep.leak_status(final, gold_apis, "api")
    updated["service_leak_status"] = service_status
    updated["service_leak_signals_json"] = recovery.json_dumps(service_signals)
    updated["api_leak_status"] = api_status
    updated["api_leak_signals_json"] = recovery.json_dumps(api_signals)
    return updated


def risk_score(row: dict[str, Any]) -> int:
    flags = recovery.parse_json(row.get("machine_risk_flags_json"), [])
    score = len(flags) if isinstance(flags, list) else 0
    for field in (
        "possible_redundant_recomputation", "parallel_subgoal_risk",
        "hybrid_composable_multi_risk", "possible_incomplete_gold_chain", "possible_incidental_call",
    ):
        score += recovery.truthy(row.get(field))
    return score


def dominant_dependency(row: dict[str, Any]) -> str:
    values = recovery.parse_json(row.get("dependency_type_distribution_json"), {})
    if not isinstance(values, dict) or not values:
        return "unknown"
    return sorted(values, key=lambda key: (-int(values[key]), key))[0]


def stratified_select(rows: list[dict[str, Any]], size: int, seed: str) -> list[dict[str, Any]]:
    strata: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
    for row in rows:
        service_bucket = "2" if recovery.int_value(row.get("distinct_gold_service_count")) == 2 else "3plus"
        api_bucket = "2" if recovery.int_value(row.get("distinct_gold_api_count")) == 2 else "3plus"
        key = "|".join(
            [
                recovery.text(row.get("catalog_domain_signature")) or "unknown",
                dominant_dependency(row), service_bucket, api_bucket,
            ]
        )
        strata[key].append(row)
    for key in strata:
        strata[key] = deque(
            sorted(
                strata[key],
                key=lambda row: hashlib.sha256(
                    f"{seed}|{row['source_task_id']}".encode("utf-8")
                ).hexdigest(),
            )
        )
    selected: list[dict[str, Any]] = []
    keys = sorted(strata)
    while keys and len(selected) < min(size, len(rows)):
        remaining: list[str] = []
        for key in keys:
            if strata[key] and len(selected) < min(size, len(rows)):
                selected.append(strata[key].popleft())
            if strata[key]:
                remaining.append(key)
        keys = remaining
    return selected


def build_review_ledger(
    old_rows: list[dict[str, str]],
    current_rows: list[dict[str, str]],
    pool_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    old_by_source = {row["source_task_id"]: row for row in old_rows}
    current_by_source = {row["source_task_id"]: row for row in current_rows}
    pool_by_source = {row["source_task_id"]: row for row in pool_rows}
    all_ids = sorted(set(old_by_source) | set(current_by_source) | set(pool_by_source))
    result = []
    for source_id in all_ids:
        old = old_by_source.get(source_id, {})
        current = current_by_source.get(source_id, {})
        new = pool_by_source.get(source_id, {})
        old_hashes = [value for value in [old.get("review_content_hash", ""), current.get("review_content_hash", "")] if value]
        new_hash = new.get("review_content_hash", "")
        human_present = any(
            recovery.text(source.get(field))
            for source in (old, current)
            for field in recovery.HUMAN_FIELDS
        )
        result.append(
            {
                "source_task_id": source_id,
                "prior_review_content_hashes_json": recovery.json_dumps(old_hashes),
                "new_review_content_hash": new_hash,
                "content_hash_equal_to_any_prior": str(bool(new_hash and new_hash in old_hashes)).lower(),
                "prior_human_fields_present": str(human_present).lower(),
                "review_credit_status": (
                    "NEW_REVIEW_REQUIRED_HASH_V1_0_1"
                    if new
                    else "SUPERSEDED_NOT_IN_AUTHORITATIVE_POOL"
                ),
                "automatic_label_copy_permitted": "false",
                "superseded_provenance": "v0.3.2/v0.3.3_not_formally_human_reviewed",
            }
        )
    return result


def write_go_no_go(path: Path, summary: dict[str, Any], generated_at: str, inputs: dict[str, Path]) -> None:
    go = summary["can_start_human_review"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""# Composable Bounded Candidate Recovery Go / No-Go v1.0.1

Generated at: `{generated_at}`

## Inputs

- frozen machine rules: `{inputs['rules']}`
- v0.3.2 task-necessity audit: `{inputs['old_audit']}`
- v0.3.3 authoritative review candidates: `{inputs['current_review']}`
- corrected strong reserve audit: `{inputs['reserve_audit']}`
- corrected ranking and dependency evidence: `{inputs['corrected_ranked']}`, `{inputs['corrected_edges']}`
- existing normalized evidence: `{inputs['normalized']}`
- real ToolBench catalog: `{inputs['catalog']}`

## Counts

- current authoritative valid count: `{summary['current_authoritative_valid_count']}`
- repairable leak-only / candidate-space-only / combined: `{summary['repairable_exact_leak_only_count']}` / `{summary['repairable_candidate_space_only_count']}` / `{summary['repairable_combined_count']}`
- deterministic rewrite valid / hold: `{summary['deterministic_rewrite_valid_count']}` / `{summary['deterministic_rewrite_hold_count']}`
- candidate reconstruction valid / failed: `{summary['candidate_space_reconstruction_valid_count']}` / `{summary['candidate_space_reconstruction_failed_count']}`
- post-repair machine valid: `{summary['post_repair_machine_valid_count']}`
- final authoritative pool rows: `{summary['final_authoritative_pool_rows']}`
- initial review batch / reserve / double annotation: `{summary['initial_review_batch_rows']}` / `{summary['review_reserve_rows']}` / `{summary['double_annotation_subset_rows']}`
- human fields autofilled: `{summary['human_fields_autofilled_count']}`

## Integrity

- hard standards relaxed: `false`
- raw ToolBench rescan performed: `false`
- new corpus mining performed: `false`
- machine rule version changed: `false`
- LLM/Qwen/external API used: `false`
- automatic composable final label or QA: `false`

## Decision

- can_start_human_review = `{str(go).lower()}`
- can_claim_composable benchmark now = `false`
- can_start full six-task assembly = `false`
- can_generate final dataset = `false`
- can split = `false`
- can run baseline = `false`
- recommended_next_step = `{summary['recommended_next_step']}`

The 97 existing rows remain machine-eligible candidates, not human-final composable labels. Rewrites remove only connector-bound exact names; original queries and full provenance remain available.
""",
        encoding="utf-8",
    )


def archive_outputs(
    archive_dir: Path,
    sources: list[Path],
    constraints: dict[str, Any],
) -> None:
    archive_dir.mkdir(parents=True, exist_ok=True)
    for source in sources:
        if source.exists() and source.is_file():
            shutil.copy2(source, archive_dir / source.name)
    manifest_path = archive_dir / "archive_manifest_v1_0_1.json"
    files = sorted(path for path in archive_dir.iterdir() if path.is_file() and path != manifest_path)
    write_json(
        manifest_path,
        {
            "generated_at": now_iso(),
            "archive_dir": str(archive_dir),
            "file_count": len(files),
            "constraints": constraints,
            "files": [
                {"filename": path.name, "bytes": path.stat().st_size, "sha256": sha256(path)}
                for path in files
            ],
        },
    )


def main() -> int:
    args = parse_args()
    root = args.project_root.resolve()
    output_dir = (root / args.output_dir).resolve()
    ledger_path = (root / args.ledger).resolve()
    report_path = (root / args.report).resolve()
    archive_dir = (root / args.archive_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = resolve_inputs(root)
    generated_at = now_iso()
    before_hashes = {
        "rules": sha256(paths["rules"]),
        "old_review": sha256(paths["old_review"]),
        "current_review": sha256(paths["current_review"]),
        "dependency_extractor": sha256(root / "scripts/validation/composable_dependency_extractor_v0_3_2.py"),
        "task_necessity_gate": sha256(root / "scripts/validation/composable_task_necessity_gate_v0_3_3.py"),
    }
    tests = run_tests(root, paths["tests"], output_dir)

    old_rows, _ = read_csv(paths["old_review"])
    old_audit, _ = read_csv(paths["old_audit"])
    current_rows, current_columns = read_csv(paths["current_review"])
    reserve_audit, _ = read_csv(paths["reserve_audit"])
    ranked_rows, _ = read_csv(paths["corrected_ranked"])
    ranked_strong = [
        row for row in ranked_rows if row.get("evidence_status") == "strong_objective_evidence_available"
    ]
    ranked_by_source = {row["source_task_id"]: row for row in ranked_strong}
    current_by_source = {row["source_task_id"]: row for row in current_rows}
    old_by_source = {row["source_task_id"]: row for row in old_rows}
    require(len(old_rows) == 200, f"Expected 200 old rows, got {len(old_rows)}")
    require(len(reserve_audit) == 294, f"Expected 294 reserve rows, got {len(reserve_audit)}")
    require(len(current_rows) == 97, f"Expected 97 current rows, got {len(current_rows)}")
    require(len(ranked_strong) == 494, f"Expected 494 corrected strong candidates, got {len(ranked_strong)}")
    require(set(old_by_source).isdisjoint({row['source_task_id'] for row in reserve_audit}), "Old and reserve candidate universes overlap")

    reconciliation, reconciliation_summary = reconcile_candidates(
        old_audit, reserve_audit, set(current_by_source), ranked_by_source, output_dir
    )
    require(reconciliation_summary["total_unique_candidate_tasks_considered"] == 494, "Candidate universe is not 494")
    require(reconciliation_summary["current_authoritative_valid_count"] == 97, "Current authoritative count is not 97")
    rec_by_source = {row["source_task_id"]: row for row in reconciliation}
    repair_ids = {
        row["source_task_id"]
        for row in reconciliation
        if row["primary_recovery_status"] in {
            "REPAIRABLE_EXACT_LEAK_ONLY",
            "REPAIRABLE_CANDIDATE_SPACE_ONLY",
            "REPAIRABLE_LEAK_AND_CANDIDATE_SPACE",
        }
    }
    reserve_repair_ids = repair_ids - set(old_by_source) - set(current_by_source)

    edges_by_source, normalized_by_trace = load_targeted_existing_evidence(
        reserve_repair_ids, ranked_by_source, paths["corrected_edges"], paths["normalized"]
    )
    static_services_all, static_apis_all, _, catalog_stats = prep.load_static_catalog(paths["catalog"])
    static_services, static_apis, service_to_apis = recovery.filtered_catalog(
        static_services_all, static_apis_all
    )
    candidate_rows: dict[str, dict[str, Any]] = {
        source_id: dict(row) for source_id, row in old_by_source.items() if source_id in repair_ids
    }
    rehydration_trace: list[dict[str, Any]] = []
    for source_id in sorted(reserve_repair_ids):
        ranked = ranked_by_source.get(source_id, {})
        trace_id = recovery.text(ranked.get("trace_record_id"))
        record = normalized_by_trace.get(trace_id)
        if not record:
            rehydration_trace.append(
                {"source_task_id": source_id, "trace_record_id": trace_id, "rehydration_status": "SOURCE_UNAVAILABLE_HOLD"}
            )
            continue
        row, trace = rehydrate_reserve_row(
            source_id, ranked, record, edges_by_source.get(source_id, []),
            static_services, static_apis, service_to_apis, current_columns,
        )
        trace["trace_record_id"] = trace_id
        rehydration_trace.append(trace)
        if row:
            prior_reasons = set(recovery.parse_json(rec_by_source[source_id]["canonical_reasons_json"], []))
            rebuilt_reasons = set(gate.assess_task(row)["structural_ineligibility_reasons"])
            if not rebuilt_reasons <= (recovery.LEAK_REASONS | recovery.CANDIDATE_REASONS):
                trace["rehydration_status"] = "REHYDRATED_GATE_MISMATCH_HOLD"
                trace["rebuilt_reasons_json"] = recovery.json_dumps(sorted(rebuilt_reasons))
                trace["prior_reasons_json"] = recovery.json_dumps(sorted(prior_reasons))
                continue
            candidate_rows[source_id] = row
    write_csv(
        output_dir / "reserve_candidate_rehydration_trace.csv",
        rehydration_trace,
        ["source_task_id", "trace_record_id", "rehydration_status", "reconstruction_status", "prior_reasons_json", "rebuilt_reasons_json", "alias_check", "post_repair_service_candidate_validity", "post_repair_api_candidate_validity"],
    )

    rewrite_trace: list[dict[str, Any]] = []
    rewrite_valid_rows: dict[str, dict[str, Any]] = {}
    rewrite_hold_rows: list[dict[str, Any]] = []
    for source_id in sorted(
        row["source_task_id"]
        for row in reconciliation
        if row["primary_recovery_status"] in {
            "REPAIRABLE_EXACT_LEAK_ONLY", "REPAIRABLE_LEAK_AND_CANDIDATE_SPACE"
        }
    ):
        row = candidate_rows.get(source_id)
        if not row:
            result = {
                "original_query_text": rec_by_source[source_id]["query_text"],
                "proposed_rewritten_query_text": rec_by_source[source_id]["query_text"],
                "removed_exact_names": [], "rewrite_patterns": [],
                "semantic_tokens_before": recovery.semantic_tokens(rec_by_source[source_id]["query_text"]),
                "semantic_tokens_after": recovery.semantic_tokens(rec_by_source[source_id]["query_text"]),
                "query_still_nonempty": bool(rec_by_source[source_id]["query_text"]),
                "query_action_object_preserved": False,
                "exact_service_leak_after": True,
                "exact_api_leak_after": True,
                "deterministic_rewrite_status": "REWRITE_NOT_APPLICABLE",
                "hold_reason": "candidate_row_unavailable_from_existing_evidence",
            }
        else:
            pre = gate.assess_task(row)
            preconditions = (
                pre["distinct_gold_service_count"] >= 2
                and pre["distinct_gold_api_count"] >= 2
                and pre["cross_service_strong_edge_count"] >= 1
                and recovery.truthy(row.get("dependency_graph_is_dag"))
                and pre["failed_call_dependency_count"] == 0
                and bool(recovery.text(row.get("dependency_evidence_json")))
                and bool(recovery.text(row.get("query_text")))
            )
            if preconditions:
                result = recovery.deterministic_leakage_rewrite(
                    recovery.text(row.get("query_text")),
                    recovery.parse_json(row.get("provisional_gold_services_json"), []),
                    recovery.parse_json(row.get("provisional_gold_apis_json"), []),
                )
            else:
                result = {
                    "original_query_text": recovery.text(row.get("query_text")),
                    "proposed_rewritten_query_text": recovery.text(row.get("query_text")),
                    "removed_exact_names": [], "rewrite_patterns": [],
                    "semantic_tokens_before": recovery.semantic_tokens(recovery.text(row.get("query_text"))),
                    "semantic_tokens_after": recovery.semantic_tokens(recovery.text(row.get("query_text"))),
                    "query_still_nonempty": bool(recovery.text(row.get("query_text"))),
                    "query_action_object_preserved": False,
                    "exact_service_leak_after": pre["exact_gold_service_name_leak"],
                    "exact_api_leak_after": pre["exact_gold_api_name_leak"],
                    "deterministic_rewrite_status": "REWRITE_NOT_APPLICABLE",
                    "hold_reason": "structural_rewrite_preconditions_failed",
                }
        trace_row = {
            "source_task_id": source_id,
            "primary_recovery_status": rec_by_source[source_id]["primary_recovery_status"],
            "original_query_text": result["original_query_text"],
            "proposed_rewritten_query_text": result["proposed_rewritten_query_text"],
            "removed_exact_names_json": recovery.json_dumps(result.get("removed_exact_names", [])),
            "rewrite_pattern_used": "|".join(result.get("rewrite_patterns", [])),
            "semantic_tokens_before": recovery.json_dumps(result.get("semantic_tokens_before", [])),
            "semantic_tokens_after": recovery.json_dumps(result.get("semantic_tokens_after", [])),
            "query_still_nonempty": str(result.get("query_still_nonempty", False)).lower(),
            "query_action_object_preserved": str(result.get("query_action_object_preserved", False)).lower(),
            "exact_service_leak_after": str(result.get("exact_service_leak_after", False)).lower(),
            "exact_api_leak_after": str(result.get("exact_api_leak_after", False)).lower(),
            "deterministic_rewrite_status": result["deterministic_rewrite_status"],
            "hold_reason": result.get("hold_reason", ""),
        }
        rewrite_trace.append(trace_row)
        if result["deterministic_rewrite_status"] == "REWRITE_VALID" and row:
            updated = apply_rewrite_to_row(row, result)
            updated.update(trace_row)
            rewrite_valid_rows[source_id] = updated
        else:
            rewrite_hold_rows.append({**rec_by_source[source_id], **trace_row})

    rewrite_fields = [
        "source_task_id", "primary_recovery_status", "original_query_text",
        "proposed_rewritten_query_text", "removed_exact_names_json", "rewrite_pattern_used",
        "semantic_tokens_before", "semantic_tokens_after", "query_still_nonempty",
        "query_action_object_preserved", "exact_service_leak_after", "exact_api_leak_after",
        "deterministic_rewrite_status", "hold_reason",
    ]
    write_csv(output_dir / "deterministic_leakage_rewrite_trace.csv", rewrite_trace, rewrite_fields)
    write_csv(
        output_dir / "rewrite_valid_candidates.csv",
        list(rewrite_valid_rows.values()),
        ordered_fields(list(rewrite_valid_rows.values()), current_columns + rewrite_fields),
    )
    write_csv(output_dir / "rewrite_hold_candidates.csv", rewrite_hold_rows, ordered_fields(rewrite_hold_rows, rewrite_fields))

    reconstruction_trace: list[dict[str, Any]] = []
    reconstructed_rows: dict[str, dict[str, Any]] = {}
    candidate_only_ids = {
        row["source_task_id"]
        for row in reconciliation
        if row["primary_recovery_status"] == "REPAIRABLE_CANDIDATE_SPACE_ONLY"
    }
    combined_ids = {
        row["source_task_id"]
        for row in reconciliation
        if row["primary_recovery_status"] == "REPAIRABLE_LEAK_AND_CANDIDATE_SPACE"
    }
    reconstruction_inputs = {
        source_id: candidate_rows[source_id]
        for source_id in candidate_only_ids
        if source_id in candidate_rows
    }
    reconstruction_inputs.update(
        {
            source_id: rewrite_valid_rows[source_id]
            for source_id in combined_ids
            if source_id in rewrite_valid_rows
        }
    )
    for source_id in sorted(candidate_only_ids | combined_ids):
        row = reconstruction_inputs.get(source_id)
        if not row:
            reconstruction_trace.append(
                {
                    "source_task_id": source_id,
                    "reconstruction_status": "OTHER_FAILURE",
                    "failure_reason": "candidate_or_rewrite_input_unavailable",
                }
            )
            continue
        updated, trace = recovery.reconstruct_candidate_space(
            row, static_services, static_apis, service_to_apis
        )
        trace["primary_recovery_status"] = rec_by_source[source_id]["primary_recovery_status"]
        reconstruction_trace.append(trace)
        if updated:
            reconstructed_rows[source_id] = updated
    reconstruction_fields = [
        "source_task_id", "primary_recovery_status", "original_candidate_services_json",
        "original_candidate_apis_json", "repaired_candidate_services_json",
        "repaired_candidate_apis_json", "negative_source", "parent_mapping", "alias_check",
        "post_repair_service_candidate_validity", "post_repair_api_candidate_validity",
        "service_negative_distractor_count", "api_negative_distractor_count",
        "reconstruction_status", "failure_reason",
    ]
    write_csv(
        output_dir / "candidate_space_reconstruction_trace.csv",
        reconstruction_trace,
        reconstruction_fields,
    )

    repaired_candidates: dict[str, dict[str, Any]] = {}
    for source_id, row in rewrite_valid_rows.items():
        if source_id not in combined_ids:
            repaired_candidates[source_id] = row
    repaired_candidates.update(reconstructed_rows)
    revalidation_rows: list[dict[str, Any]] = []
    for source_id, source in [
        *[(row["source_task_id"], dict(row)) for row in current_rows],
        *[(source_id, dict(row)) for source_id, row in repaired_candidates.items()],
    ]:
        source.setdefault("original_query_text", source.get("query_text", ""))
        source.setdefault("final_model_facing_query_text", source.get("query_text", ""))
        source.setdefault("rewrite_provenance_json", "{}")
        result = recovery.complete_machine_revalidation(source)
        source.update(gate.assessment_csv_fields(result["assessment"]))
        source["machine_revalidation_status"] = result["machine_revalidation_status"]
        source["machine_blocking_rules_json"] = recovery.json_dumps(result["machine_blocking_rules"])
        source["machine_risk_flags_json"] = recovery.json_dumps(result["machine_risk_flags"])
        source["machine_rule_spec_version"] = recovery.MACHINE_RULE_SPEC_VERSION
        source["recovery_origin"] = (
            "AUTHORITATIVE_VALID_EXISTING" if source_id in current_by_source else rec_by_source[source_id]["primary_recovery_status"]
        )
        for field in recovery.HUMAN_FIELDS:
            source[field] = ""
        revalidation_rows.append(source)
    revalidation_rows.sort(key=lambda row: row["source_task_id"])
    write_csv(
        output_dir / "recovered_candidates_machine_revalidation.csv",
        revalidation_rows,
        ordered_fields(revalidation_rows, current_columns),
    )

    eligible_statuses = {"AUTHORITATIVE_VALID", "STRUCTURALLY_ELIGIBLE_WITH_RISK"}
    eligible = [row for row in revalidation_rows if row["machine_revalidation_status"] in eligible_statuses]
    eligible.sort(
        key=lambda row: (
            0 if row["machine_revalidation_status"] == "AUTHORITATIVE_VALID" else 1,
            risk_score(row),
            0 if row["recovery_origin"] == "AUTHORITATIVE_VALID_EXISTING" else 1,
            hashlib.sha256(f"pool|{row['source_task_id']}".encode("utf-8")).hexdigest(),
        )
    )
    pool = eligible[: min(args.pool_target, args.pool_max, len(eligible))]
    frozen_at = now_iso()
    for index, row in enumerate(pool, start=1):
        row["authoritative_pool_rank"] = index
        row["review_item_id"] = f"COMPOSABLE-BOUNDED-REVIEW-V1.0.1-{index:04d}"
        row["pack_version"] = VERSION
        row["pack_frozen_at"] = frozen_at
        row["pack_status"] = "BOUNDED_AUTHORITATIVE_REVIEW_POOL"
        row["query_text"] = row["final_model_facing_query_text"]
        for field in recovery.HUMAN_FIELDS:
            row[field] = ""
        row["review_content_hash"] = recovery.recovery_review_hash(row)
    pool_fields = ordered_fields(pool, current_columns + [
        "original_query_text", "final_model_facing_query_text", "rewrite_provenance_json",
        "recovery_origin", "machine_revalidation_status", "machine_blocking_rules_json",
        "machine_risk_flags_json", "authoritative_pool_rank",
    ])
    write_csv(
        output_dir / "composable_authoritative_review_pool_v1_0_1.csv",
        pool,
        pool_fields,
    )

    batch: list[dict[str, Any]] = []
    reserve: list[dict[str, Any]] = []
    double_subset: list[dict[str, Any]] = []
    shortage_pool: list[dict[str, Any]] = []
    if len(pool) >= args.batch_size:
        batch = stratified_select(pool, args.batch_size, BATCH_SEED)
        batch_ids = {row["source_task_id"] for row in batch}
        reserve = [row for row in pool if row["source_task_id"] not in batch_ids]
        double_size = round(len(batch) * 0.20)
        double_subset = stratified_select(batch, double_size, DOUBLE_SEED)
        write_csv(
            output_dir / "composable_human_review_batch_01_120_v1_0_1.csv", batch, pool_fields
        )
        write_csv(
            output_dir / "composable_human_review_reserve_v1_0_1.csv", reserve, pool_fields
        )
        write_csv(
            output_dir / f"composable_double_annotation_subset_{double_size}_v1_0_1.csv",
            double_subset,
            pool_fields,
        )
    else:
        shortage_pool = pool
        write_csv(
            output_dir / "composable_human_review_shortage_pool_v1_0_1.csv",
            shortage_pool,
            pool_fields,
        )

    ledger = build_review_ledger(old_rows, current_rows, pool)
    write_csv(ledger_path, ledger)
    human_autofill = sum(
        1 for row in pool if any(recovery.text(row.get(field)) for field in recovery.HUMAN_FIELDS)
    )
    post_repair_valid = sum(
        row["recovery_origin"] != "AUTHORITATIVE_VALID_EXISTING"
        and row["machine_revalidation_status"] in eligible_statuses
        for row in revalidation_rows
    )
    rewrite_valid_count = sum(
        row["deterministic_rewrite_status"] == "REWRITE_VALID" for row in rewrite_trace
    )
    reconstruction_valid = sum(
        row.get("reconstruction_status") == "RECONSTRUCTED_VALID" for row in reconstruction_trace
    )
    summary = {
        "generated_at": generated_at,
        **tests,
        "machine_rule_spec_version": recovery.MACHINE_RULE_SPEC_VERSION,
        "total_unique_candidate_tasks_considered": reconciliation_summary["total_unique_candidate_tasks_considered"],
        "current_authoritative_valid_count": 97,
        "repairable_exact_leak_only_count": reconciliation_summary["repairable_leak_only_count"],
        "repairable_candidate_space_only_count": reconciliation_summary["repairable_candidate_space_only_count"],
        "repairable_combined_count": reconciliation_summary["repairable_leak_plus_candidate_space_count"],
        "deterministic_rewrite_valid_count": rewrite_valid_count,
        "deterministic_rewrite_hold_count": len(rewrite_trace) - rewrite_valid_count,
        "candidate_space_reconstruction_valid_count": reconstruction_valid,
        "candidate_space_reconstruction_failed_count": len(reconstruction_trace) - reconstruction_valid,
        "post_repair_machine_valid_count": post_repair_valid,
        "final_authoritative_pool_rows": len(pool),
        "initial_review_batch_rows": len(batch),
        "review_reserve_rows": len(reserve),
        "shortage_pool_rows": len(shortage_pool),
        "double_annotation_subset_rows": len(double_subset),
        "human_fields_autofilled_count": human_autofill,
        "hard_standards_relaxed": False,
        "raw_toolbench_rescan_performed": False,
        "new_corpus_mining_performed": False,
        "machine_rule_version_changed": False,
        "llm_or_external_api_used": False,
        "catalog_service_count": catalog_stats["service_count"],
        "catalog_api_count": catalog_stats["api_count"],
        "prior_candidate_invalid_manifest_rows": len(read_csv(paths["prior_candidate_invalid"])[0]),
        "prior_candidate_repair_manifest_rows": len(read_csv(paths["prior_candidate_repair"])[0]),
        "can_claim_composable_benchmark_now": False,
        "can_start_full_six_task_assembly": False,
        "can_generate_final_dataset": False,
        "can_split": False,
        "can_run_baseline": False,
    }
    summary["can_start_human_review"] = bool(
        len(pool) >= args.batch_size
        and human_autofill == 0
        and all(
            row["machine_revalidation_status"] in eligible_statuses
            and not recovery.parse_json(row.get("machine_blocking_rules_json"), [])
            for row in pool
        )
    )
    summary["recommended_next_step"] = (
        "humanly review batch 01; stop when both-level eligible count reaches 100; otherwise add 20-row increments from the frozen reserve."
        if summary["can_start_human_review"]
        else "do not relax paired-composable rules; run one bounded StableToolBench trace/schema-grounded supplement branch, or report the remaining candidate shortage."
    )
    write_json(output_dir / "composable_bounded_candidate_recovery_summary_v1_0_1.json", summary)
    write_go_no_go(report_path, summary, generated_at, paths)

    after_hashes = {
        "rules": sha256(paths["rules"]),
        "old_review": sha256(paths["old_review"]),
        "current_review": sha256(paths["current_review"]),
        "dependency_extractor": sha256(root / "scripts/validation/composable_dependency_extractor_v0_3_2.py"),
        "task_necessity_gate": sha256(root / "scripts/validation/composable_task_necessity_gate_v0_3_3.py"),
    }
    require(before_hashes == after_hashes, "A frozen rule or source artifact changed during recovery")
    write_json(
        output_dir / "bounded_recovery_integrity_hashes_v1_0_1.json",
        {"generated_at": generated_at, "before": before_hashes, "after": after_hashes},
    )

    constraints = {
        "hard_standards_relaxed": False,
        "raw_toolbench_rescan_performed": False,
        "new_corpus_mining_performed": False,
        "machine_rule_version_changed": False,
        "llm_query_rewrite_used": False,
        "llm_generated_gold_or_negatives": False,
        "human_fields_autofilled": False,
        "source_frozen": False,
        "six_task_assembly_run": False,
        "final_dataset_generated": False,
        "split_created": False,
        "baseline_run": False,
        "model_trained": False,
        "qwen_used": False,
        "external_api_used": False,
    }
    if not args.skip_archive:
        generated_files = sorted(path for path in output_dir.iterdir() if path.is_file())
        archive_outputs(
            archive_dir,
            [
                *generated_files,
                ledger_path,
                report_path,
                paths["rules"],
                Path(__file__).resolve(),
                root / "scripts/validation/composable_candidate_recovery_v1_0_1.py",
                paths["tests"],
            ],
            constraints,
        )

    print(f"current_authoritative_valid_count={summary['current_authoritative_valid_count']}")
    print(f"repairable_exact_leak_only_count={summary['repairable_exact_leak_only_count']}")
    print(f"repairable_candidate_space_only_count={summary['repairable_candidate_space_only_count']}")
    print(f"repairable_combined_count={summary['repairable_combined_count']}")
    print(f"deterministic_rewrite_valid_count={summary['deterministic_rewrite_valid_count']}")
    print(f"deterministic_rewrite_hold_count={summary['deterministic_rewrite_hold_count']}")
    print(f"candidate_space_reconstruction_valid_count={summary['candidate_space_reconstruction_valid_count']}")
    print(f"candidate_space_reconstruction_failed_count={summary['candidate_space_reconstruction_failed_count']}")
    print(f"post_repair_machine_valid_count={summary['post_repair_machine_valid_count']}")
    print(f"final_authoritative_pool_rows={summary['final_authoritative_pool_rows']}")
    print(f"initial_review_batch_rows={summary['initial_review_batch_rows']}")
    print(f"review_reserve_rows={summary['review_reserve_rows']}")
    print(f"double_annotation_subset_rows={summary['double_annotation_subset_rows']}")
    print("hard_standards_relaxed=false")
    print("raw_toolbench_rescan_performed=false")
    print("machine_rule_version_changed=false")
    print(f"human_fields_autofilled_count={summary['human_fields_autofilled_count']}")
    print(f"can_start_human_review={str(summary['can_start_human_review']).lower()}")
    print(f"recommended_next_step={summary['recommended_next_step']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
