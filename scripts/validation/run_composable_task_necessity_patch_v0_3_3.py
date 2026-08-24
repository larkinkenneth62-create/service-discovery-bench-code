#!/usr/bin/env python3
"""Build the v0.3.3 paired composable review pack from corrected v0.3.2 evidence."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import subprocess
import sys
from collections import Counter, defaultdict, deque
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import composable_task_necessity_gate_v0_3_3 as gate  # noqa: E402
import prepare_composable_paired_tasks_v0_3 as prep  # noqa: E402
import repair_and_freeze_composable_pack_v0_3_1 as repair  # noqa: E402
import run_composable_dependency_extractor_patch_v0_3_2 as v032  # noqa: E402


VERSION = "v0.3.3"
ORDER_SEED = "COMPOSABLE-V0.3.3-CANDIDATE-ORDER-SEED-20260715"
DOUBLE_SEED = "COMPOSABLE-V0.3.3-DOUBLE-ANNOTATION-SEED-20260715"
DEFAULT_PATCH_DIR = Path("outputs/composable_task_necessity_patch_v0_3_3")
DEFAULT_PACK_DIR = Path("outputs/composable_paired_task_preparation_v0_3_3")
DEFAULT_ARCHIVE_DIR = Path(
    "outputs/run_archives/2026-07-15_composable_task_necessity_patch_v0_3_3"
)

INPUTS = {
    "old_review": Path(
        "outputs/composable_paired_task_preparation_v0_3_2/"
        "composable_paired_task_review_items_v0_3_2.csv"
    ),
    "old_master": Path(
        "outputs/composable_paired_task_preparation_v0_3_2/"
        "composable_underlying_tasks_master_v0_3_2.csv"
    ),
    "corrected_ranked": Path(
        "outputs/composable_dependency_extractor_patch_v0_3_2/"
        "corrected_underlying_task_candidates_ranked.csv"
    ),
    "corrected_edges": Path(
        "outputs/composable_dependency_extractor_patch_v0_3_2/"
        "corrected_dependency_edge_candidates.jsonl"
    ),
    "normalized": Path(
        "outputs/composable_corpus_mining_v0_2/"
        "toolbench_full_normalized_multicall_steps.jsonl"
    ),
    "catalog": Path("external_sources/ToolBench/data/toolenv/tools"),
    "rules": Path(
        "docs/project/SERVICEDISCOVERYBENCH_COMPOSABLE_MACHINE_REVIEW_RULES.md"
    ),
    "master_plan": Path("docs/project/SERVICEDISCOVERYBENCH_BENCHMARK_MASTER_PLAN.md"),
    "tests": Path("tests/validation/test_composable_task_necessity_gate_v0_3_3.py"),
}

MACHINE_FIELDS = [
    "machine_review_status",
    "machine_rule_spec_version",
    "structural_hard_gate_pass",
    "structural_ineligibility_reasons_json",
    "distinct_gold_service_count",
    "distinct_gold_api_count",
    "strong_edge_count",
    "cross_service_strong_edge_count",
    "same_service_strong_edge_count",
    "unresolved_service_edge_count",
    "successful_gold_call_count",
    "failed_or_error_gold_call_count",
    "failed_call_dependency_count",
    "disconnected_query_relevant_call_count",
    "exact_gold_service_name_leak",
    "exact_gold_api_name_leak",
    "service_level_structurally_eligible",
    "api_level_structurally_eligible",
    "service_candidate_space_structurally_valid",
    "api_candidate_space_structurally_valid",
    "api_only_workflow_candidate",
    "possible_redundant_recomputation",
    "upstream_already_returns_requested_result",
    "repeated_result_type_json",
    "downstream_adds_new_required_information",
    "necessity_risk_reason_json",
    "only_redundant_recomputation_dependency",
    "redundancy_evidence_json",
    "parallel_subgoal_risk",
    "hybrid_composable_multi_risk",
    "possible_incomplete_gold_chain",
    "possible_incidental_call",
    "disconnected_query_relevant_calls_json",
    "cross_service_edges_json",
    "same_service_edges_json",
    "requires_human_semantic_review",
    "requires_human_necessity_review",
]

ALL_HUMAN_FIELDS = [*prep.REVIEW_HUMAN_FIELDS, *gate.HUMAN_NECESSITY_FIELDS]


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
        return int(float(text(value) or 0))
    except ValueError:
        return 0


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


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def read_csv(path: Path) -> list[dict[str, str]]:
    csv.field_size_limit(min(sys.maxsize, 2**31 - 1))
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(
    path: Path,
    rows: Iterable[dict[str, Any]],
    fieldnames: list[str] | None = None,
) -> None:
    materialized = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        fieldnames = []
        seen = set()
        for row in materialized:
            for key in row:
                if key not in seen:
                    fieldnames.append(key)
                    seen.add(key)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(materialized)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def resolve_inputs(root: Path) -> dict[str, Path]:
    resolved = {key: (root / relative).resolve() for key, relative in INPUTS.items()}
    missing = [f"{key}: {path}" for key, path in resolved.items() if not path.exists()]
    if missing:
        output = root / DEFAULT_PATCH_DIR / "MISSING_INPUTS.md"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            "# Missing inputs for v0.3.3\n\n" + "\n".join(f"- `{item}`" for item in missing) + "\n",
            encoding="utf-8",
        )
        raise FileNotFoundError("Missing required inputs; see " + str(output))
    return resolved


def run_tests(root: Path, test_path: Path, patch_dir: Path) -> dict[str, int]:
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
    test_output = patch_dir / "composable_task_necessity_test_results_v0_3_3.txt"
    test_output.parent.mkdir(parents=True, exist_ok=True)
    test_output.write_text(output, encoding="utf-8")
    match = re.search(r"Ran\s+(\d+)\s+tests?", output)
    run = int(match.group(1)) if match else 0
    failed = 0 if result.returncode == 0 else 1
    if result.returncode != 0:
        raise RuntimeError(f"Task-necessity tests failed; see {test_output}")
    return {"tests_run": run, "tests_passed": run, "tests_failed": failed}


def result_from_existing_evidence(
    record: dict[str, Any], ranked: dict[str, Any], edges: list[dict[str, Any]]
) -> dict[str, Any]:
    strong = [edge for edge in edges if truthy(edge.get("strong_edge_eligible"))]
    failed_calls = parse_json(ranked.get("incidental_or_failed_calls_json"), [])
    if not isinstance(failed_calls, list):
        failed_calls = []
    shared_values = parse_json(ranked.get("shared_input_values_json"), [])
    if not isinstance(shared_values, list):
        shared_values = []
    edge_source_counts = Counter(text(edge.get("edge_source_type")) for edge in edges)
    call_status_counts = Counter(
        text(step.get("call_execution_status"))
        for step in record.get("steps", [])
        if isinstance(step, dict)
    )
    return {
        "record": record,
        "edges": edges,
        "strong_edges": strong,
        "strong_edge_count": len(strong),
        "edge_source_type_counts": dict(sorted(edge_source_counts.items())),
        "call_execution_status_counts": dict(sorted(call_status_counts.items())),
        "shared_input_values": shared_values,
        "failed_calls": failed_calls,
        "parse_errors": [],
        "execution_evidence_incomplete": bool(failed_calls),
        "evidence_status": text(ranked.get("evidence_status")),
    }


def trace_prefilter(
    record: dict[str, Any], strong_edges: list[dict[str, Any]]
) -> tuple[bool, list[str]]:
    steps = record.get("steps", [])
    steps = steps if isinstance(steps, list) else []
    by_index = gate.step_by_index(steps)
    cross, same, unresolved = gate.classify_edge_services(strong_edges, by_index)
    endpoint_services = {
        gate.step_service_key(by_index.get(int_value(edge.get(key)), {}))
        for edge in strong_edges
        for key in ("from_step", "to_step")
    }
    endpoint_services.discard("")
    endpoint_apis = {
        gate.step_api_key(by_index.get(int_value(edge.get(key)), {}))
        for edge in strong_edges
        for key in ("from_step", "to_step")
    }
    endpoint_apis.discard("")
    failed = sum(
        text(edge.get("upstream_call_execution_status")).casefold() in gate.FAILED_STATUSES
        or text(edge.get("downstream_call_execution_status")).casefold() in gate.FAILED_STATUSES
        for edge in strong_edges
    )
    reasons = []
    if not text(record.get("query_text")):
        reasons.append("empty_query")
    if len(endpoint_services) < 2:
        reasons.append("endpoint_service_count_lt_2")
    if len(endpoint_apis) < 2:
        reasons.append("endpoint_api_count_lt_2")
    if not cross:
        reasons.append("no_cross_service_edge")
    if same and not cross:
        reasons.append("same_service_only")
    if unresolved:
        reasons.append("unresolved_service_mapping")
    if failed:
        reasons.append("failed_dependency_edge")
    return not reasons, reasons


def assessment_fields(assessment: dict[str, Any]) -> dict[str, str]:
    return gate.assessment_csv_fields(assessment)


def add_necessity_evidence(
    row: dict[str, Any], assessment: dict[str, Any]
) -> None:
    evidence = parse_json(row.get("dependency_evidence_json"), {})
    if not isinstance(evidence, dict):
        evidence = {}
    evidence["composable_machine_rule_spec_version"] = gate.MACHINE_RULE_SPEC_VERSION
    evidence["task_necessity_gate_version"] = VERSION
    evidence["task_necessity_machine_assessment"] = {
        "machine_review_status": assessment["machine_review_status"],
        "structural_hard_gate_pass": assessment["structural_hard_gate_pass"],
        "structural_ineligibility_reasons": assessment["structural_ineligibility_reasons"],
        "distinct_gold_service_count": assessment["distinct_gold_service_count"],
        "distinct_gold_api_count": assessment["distinct_gold_api_count"],
        "cross_service_strong_edge_count": assessment["cross_service_strong_edge_count"],
        "same_service_strong_edge_count": assessment["same_service_strong_edge_count"],
        "failed_call_dependency_count": assessment["failed_call_dependency_count"],
        "exact_gold_service_name_leak": assessment["exact_gold_service_name_leak"],
        "exact_gold_api_name_leak": assessment["exact_gold_api_name_leak"],
        "possible_redundant_recomputation": assessment["possible_redundant_recomputation"],
        "parallel_subgoal_risk": assessment["parallel_subgoal_risk"],
        "hybrid_composable_multi_risk": assessment["hybrid_composable_multi_risk"],
        "machine_semantic_boundary": "risk_flags_only_human_final_required",
    }
    row["dependency_evidence_json"] = json_dumps(evidence)


def freeze_row(
    source: dict[str, Any],
    assessment: dict[str, Any],
    index: int,
    frozen_at: str,
) -> dict[str, Any]:
    row = dict(source)
    row["underlying_task_id"] = f"COMPOSABLE-UNDERLYING-V0.3.3-{index:04d}"
    row["paired_task_group_id"] = f"COMPOSABLE-PAIR-V0.3.3-{index:04d}"
    row["split_group_id"] = f"TOOLBENCH-{row['source_task_id']}"
    row["pack_version"] = VERSION
    row["pack_frozen_at"] = frozen_at
    row["pack_status"] = "TASK_NECESSITY_CORRECTED_REVIEW_PACK_READY"
    row["preparation_script_version"] = "composable_task_necessity_patch_v0_3_3"
    row["candidate_order_seed"] = ORDER_SEED
    row.update(assessment_fields(assessment))
    add_necessity_evidence(row, assessment)
    row["review_content_hash"] = prep.review_hash(row)
    return row


def audit_old_pack(
    old_rows: list[dict[str, str]], patch_dir: Path
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, int]]:
    audits = []
    assessments = {}
    counter = Counter()
    for row in old_rows:
        assessment = gate.assess_task(row)
        assessments[row["source_task_id"]] = assessment
        csv_assessment = assessment_fields(assessment)
        retained = assessment["structural_hard_gate_pass"]
        audit = {
            "review_item_id": row.get("review_item_id", ""),
            "underlying_task_id": row.get("underlying_task_id", ""),
            "source_task_id": row.get("source_task_id", ""),
            "source_group": row.get("source_group", ""),
            "query_text": row.get("query_text", ""),
            **csv_assessment,
            "retained_for_v0_3_3": bool_text(retained),
            "removed_from_main_pack": bool_text(not retained),
            "replacement_required": bool_text(not retained),
            "old_pack_status": "SUPERSEDED_PENDING_V0_3_3",
        }
        audits.append(audit)
        counter["input_rows"] += 1
        counter["gold_service_count_lt_2"] += assessment["distinct_gold_service_count"] < 2
        counter["gold_api_count_lt_2"] += assessment["distinct_gold_api_count"] < 2
        counter["no_cross_service_strong_edge"] += assessment["cross_service_strong_edge_count"] == 0
        counter["same_service_only_dependency"] += bool(
            assessment["same_service_strong_edge_count"] > 0
            and assessment["cross_service_strong_edge_count"] == 0
        )
        counter["failed_downstream_dependency"] += assessment["failed_call_dependency_count"] > 0
        counter["exact_service_leak"] += assessment["exact_gold_service_name_leak"]
        counter["exact_api_leak"] += assessment["exact_gold_api_name_leak"]
        counter["possible_redundant_recomputation"] += assessment["possible_redundant_recomputation"]
        counter["disconnected_parallel_subgoal_risk"] += assessment["parallel_subgoal_risk"]
        counter["retained_for_v0_3_3"] += retained
        counter["removed_from_main_pack"] += not retained
        counter["replacement_required"] += not retained
    write_csv(patch_dir / "v0_3_2_task_necessity_reaudit.csv", audits)
    summary = {key: int(value) for key, value in sorted(counter.items())}
    summary["v0_3_2_pack_status"] = "SUPERSEDED_PENDING_V0_3_3"
    write_json(patch_dir / "v0_3_2_task_necessity_reaudit_summary.json", summary)
    return audits, assessments, {key: int(value) for key, value in counter.items()}


def load_corrected_evidence(
    ranked_rows: list[dict[str, str]],
    edges_path: Path,
    normalized_path: Path,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]]]:
    strong_rows = [
        row
        for row in ranked_rows
        if text(row.get("evidence_status")) == "strong_objective_evidence_available"
    ]
    source_ids = {row["source_task_id"] for row in strong_rows}
    trace_ids = {text(row.get("trace_record_id")) for row in strong_rows}
    edges: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in iter_jsonl(edges_path):
        source_id = text(edge.get("source_task_id"))
        if source_id in source_ids:
            edges[source_id].append(edge)
    normalized = v032.load_selected_normalized(normalized_path, trace_ids)
    return edges, normalized


def build_pack(
    root: Path,
    paths: dict[str, Path],
    old_rows: list[dict[str, str]],
    old_assessments: dict[str, dict[str, Any]],
    ranked_rows: list[dict[str, str]],
    patch_dir: Path,
    pack_dir: Path,
    target_rows: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    frozen_at = now_iso()
    selected: list[dict[str, Any]] = []
    selection_trace = []
    old_by_source = {row["source_task_id"]: row for row in old_rows}
    used_sources = set()

    for old in old_rows:
        assessment = old_assessments[old["source_task_id"]]
        if not assessment["structural_hard_gate_pass"]:
            continue
        selected.append(freeze_row(old, assessment, len(selected) + 1, frozen_at))
        used_sources.add(old["source_task_id"])
        selection_trace.append(
            {
                "source_task_id": old["source_task_id"],
                "origin": "v0_3_2_retained",
                "selection_status": "selected",
                "rejection_reason": "",
                "old_review_item_id": old.get("review_item_id", ""),
                "new_index": len(selected),
            }
        )

    edges_by_source, normalized_by_trace = load_corrected_evidence(
        ranked_rows, paths["corrected_edges"], paths["normalized"]
    )
    static_services, static_apis, service_to_apis, _ = prep.load_static_catalog(paths["catalog"])
    reserve_rows = [
        row
        for row in ranked_rows
        if text(row.get("evidence_status")) == "strong_objective_evidence_available"
        and row["source_task_id"] not in old_by_source
    ]
    reserve_audits = []

    for ranked in reserve_rows:
        if len(selected) >= target_rows:
            break
        source_id = ranked["source_task_id"]
        trace_id = text(ranked.get("trace_record_id"))
        record = normalized_by_trace.get(trace_id)
        source_edges = edges_by_source.get(source_id, [])
        strong_edges = [edge for edge in source_edges if truthy(edge.get("strong_edge_eligible"))]
        if not record:
            selection_trace.append(
                {
                    "source_task_id": source_id,
                    "origin": "corrected_strong_reserve",
                    "selection_status": "rejected",
                    "rejection_reason": "normalized_record_missing",
                }
            )
            continue
        prefilter_ok, prefilter_reasons = trace_prefilter(record, strong_edges)
        if not prefilter_ok:
            reserve_audits.append(
                {
                    "source_task_id": source_id,
                    "trace_record_id": trace_id,
                    "stage": "trace_prefilter",
                    "selection_status": "rejected",
                    "rejection_reason": "|".join(prefilter_reasons),
                }
            )
            continue
        result = result_from_existing_evidence(record, ranked, source_edges)
        built, construction_reason = v032.construct_candidate(
            root,
            record,
            result,
            ranked,
            static_services,
            static_apis,
            service_to_apis,
        )
        if not built:
            reserve_audits.append(
                {
                    "source_task_id": source_id,
                    "trace_record_id": trace_id,
                    "stage": "candidate_construction",
                    "selection_status": "rejected",
                    "rejection_reason": construction_reason,
                }
            )
            continue
        enriched = v032.enrich_master_row(
            built, result, None, len(selected) + 1, frozen_at
        )
        assessment = gate.assess_task(enriched)
        reserve_audits.append(
            {
                "source_task_id": source_id,
                "trace_record_id": trace_id,
                "stage": "full_structural_gate",
                "selection_status": "selected" if assessment["structural_hard_gate_pass"] else "rejected",
                "rejection_reason": "|".join(assessment["structural_ineligibility_reasons"]),
                **assessment_fields(assessment),
            }
        )
        if not assessment["structural_hard_gate_pass"]:
            continue
        selected.append(freeze_row(enriched, assessment, len(selected) + 1, frozen_at))
        used_sources.add(source_id)
        selection_trace.append(
            {
                "source_task_id": source_id,
                "trace_record_id": trace_id,
                "origin": "corrected_strong_reserve",
                "selection_status": "selected",
                "rejection_reason": "",
                "new_index": len(selected),
            }
        )

    pack_dir.mkdir(parents=True, exist_ok=True)
    write_csv(pack_dir / "composable_underlying_tasks_master_v0_3_3.csv", selected)
    write_csv(patch_dir / "corrected_strong_reserve_necessity_prefilter.csv", reserve_audits)
    write_csv(patch_dir / "v0_3_3_candidate_selection_trace.csv", selection_trace)

    review_rows = []
    for index, row in enumerate(selected, start=1):
        old = old_by_source.get(row["source_task_id"])
        review = {
            "review_item_id": f"COMPOSABLE-PAIRED-REVIEW-V0.3.3-{index:04d}",
            **row,
            "prior_review_content_hash": text(old.get("review_content_hash")) if old else "",
            "prior_review_credit_status": (
                "invalidated_by_task_necessity_gate_v0_3_3"
                if old
                else "new_structurally_eligible_candidate"
            ),
        }
        for field in ALL_HUMAN_FIELDS:
            review[field] = ""
        review_rows.append(review)
    write_csv(pack_dir / "composable_paired_task_review_items_v0_3_3.csv", review_rows)

    service_rows, api_rows = prep.build_provisional_rows(selected)
    for index, row in enumerate(service_rows, start=1):
        source = selected[index - 1]
        row["benchmark_task_id"] = f"CSD-V0.3.3-{index:04d}"
        row.update({key: source.get(key, "") for key in MACHINE_FIELDS})
        row.update(
            {
                "pack_version": VERSION,
                "pack_frozen_at": frozen_at,
                "pack_status": "TASK_NECESSITY_CORRECTED_REVIEW_PACK_READY",
            }
        )
    for index, row in enumerate(api_rows, start=1):
        source = selected[index - 1]
        row["benchmark_task_id"] = f"CAR-V0.3.3-{index:04d}"
        row.update({key: source.get(key, "") for key in MACHINE_FIELDS})
        row.update(
            {
                "pack_version": VERSION,
                "pack_frozen_at": frozen_at,
                "pack_status": "TASK_NECESSITY_CORRECTED_REVIEW_PACK_READY",
            }
        )
    write_csv(
        pack_dir / "composable_service_discovery_provisional_rows_v0_3_3.csv",
        service_rows,
    )
    write_csv(
        pack_dir / "composable_api_recommendation_provisional_rows_v0_3_3.csv",
        api_rows,
    )

    retained = sum(row["source_task_id"] in old_by_source for row in selected)
    metrics = {
        "retained_old_rows": retained,
        "replacement_rows": len(selected) - retained,
        "final_v0_3_3_rows": len(selected),
        "final_unique_tasks": len({row["source_task_id"] for row in selected}),
        "pack_frozen_at": frozen_at,
    }
    return selected, review_rows, metrics


def build_migration_manifest(
    root: Path,
    old_rows: list[dict[str, str]],
    new_rows: list[dict[str, Any]],
    pack_dir: Path,
) -> list[dict[str, Any]]:
    new_by_source = {row["source_task_id"]: row for row in new_rows}
    reviewed_files = sorted(
        path
        for path in (root / "outputs").rglob("composable_paired_task_review_items_v0_3_2*.csv")
        if "reviewed" in path.name.casefold() and path.is_file()
    )
    rows = []
    for old in old_rows:
        new = new_by_source.get(old["source_task_id"])
        rows.append(
            {
                "source_task_id": old["source_task_id"],
                "old_review_item_id": old.get("review_item_id", ""),
                "new_review_item_id": new.get("review_item_id", "") if new else "",
                "old_review_content_hash": old.get("review_content_hash", ""),
                "new_review_content_hash": new.get("review_content_hash", "") if new else "",
                "review_credit_status": (
                    "invalidated_by_task_necessity_gate_v0_3_3"
                    if new
                    else "invalidated_structurally_ineligible_for_v0_3_3"
                ),
                "automatic_label_copy_permitted": "false",
                "reviewed_v0_3_2_files_found": json_dumps([str(path) for path in reviewed_files]),
            }
        )
    old_sources = {row["source_task_id"] for row in old_rows}
    for new in new_rows:
        if new["source_task_id"] in old_sources:
            continue
        rows.append(
            {
                "source_task_id": new["source_task_id"],
                "old_review_item_id": "",
                "new_review_item_id": new["review_item_id"],
                "old_review_content_hash": "",
                "new_review_content_hash": new["review_content_hash"],
                "review_credit_status": "new_structurally_eligible_candidate",
                "automatic_label_copy_permitted": "false",
                "reviewed_v0_3_2_files_found": json_dumps([str(path) for path in reviewed_files]),
            }
        )
    write_csv(pack_dir / "v0_3_2_to_v0_3_3_review_migration_manifest.csv", rows)
    return rows


def build_double_subset(rows: list[dict[str, Any]], size: int = 40) -> list[dict[str, Any]]:
    strata: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
    for row in rows:
        edge_types = parse_json(row.get("dependency_type_distribution_json"), {})
        dominant = max(edge_types, key=edge_types.get) if edge_types else "unknown"
        service_bucket = "2" if int_value(row.get("distinct_gold_service_count")) == 2 else "3plus"
        api_count = int_value(row.get("distinct_gold_api_count"))
        api_bucket = "2" if api_count == 2 else "3plus"
        key = f"{text(row.get('catalog_domain_signature')) or 'unknown'}|{dominant}|{service_bucket}|{api_bucket}"
        strata[key].append(row)
    for key in strata:
        ordered = sorted(
            strata[key],
            key=lambda row: hashlib.sha256(
                f"{DOUBLE_SEED}|{row['source_task_id']}".encode("utf-8")
            ).hexdigest(),
        )
        strata[key] = deque(ordered)
    selected = []
    keys = sorted(strata)
    while len(selected) < min(size, len(rows)) and keys:
        next_keys = []
        for key in keys:
            if strata[key] and len(selected) < min(size, len(rows)):
                row = dict(strata[key].popleft())
                row["double_annotation_subset_version"] = VERSION
                row["double_annotation_seed"] = DOUBLE_SEED
                selected.append(row)
            if strata[key]:
                next_keys.append(key)
        keys = next_keys
    return selected


def validate_final_pack(
    rows: list[dict[str, Any]], patch_dir: Path
) -> tuple[dict[str, int], list[dict[str, str]]]:
    issues = []
    counters = Counter()
    for row in rows:
        assessment = gate.assess_task(row)
        counters["final_cross_service_dependency_valid_count"] += assessment["cross_service_strong_edge_count"] >= 1
        counters["final_service_candidate_valid_count"] += assessment["service_candidate_space_structurally_valid"]
        counters["final_api_candidate_valid_count"] += assessment["api_candidate_space_structurally_valid"]
        counters["final_gold_service_count_ge_2"] += assessment["distinct_gold_service_count"] >= 2
        counters["final_gold_api_count_ge_2"] += assessment["distinct_gold_api_count"] >= 2
        counters["final_failed_dependency_count"] += assessment["failed_call_dependency_count"] > 0
        counters["final_exact_service_leak_count"] += assessment["exact_gold_service_name_leak"]
        counters["final_exact_api_leak_count"] += assessment["exact_gold_api_name_leak"]
        counters["final_structural_hard_gate_pass_count"] += assessment["structural_hard_gate_pass"]
        if not assessment["structural_hard_gate_pass"]:
            issues.append(
                {
                    "review_item_id": row.get("review_item_id", ""),
                    "source_task_id": row.get("source_task_id", ""),
                    "issue": "final_structural_gate_failed",
                    "details": "|".join(assessment["structural_ineligibility_reasons"]),
                }
            )
        if any(text(row.get(field)) for field in ALL_HUMAN_FIELDS):
            counters["human_fields_autofilled_count"] += 1
            issues.append(
                {
                    "review_item_id": row.get("review_item_id", ""),
                    "source_task_id": row.get("source_task_id", ""),
                    "issue": "human_field_autofilled",
                    "details": "",
                }
            )
        if prep.review_hash(row) != text(row.get("review_content_hash")):
            counters["review_hash_mismatch_count"] += 1
            issues.append(
                {
                    "review_item_id": row.get("review_item_id", ""),
                    "source_task_id": row.get("source_task_id", ""),
                    "issue": "review_hash_mismatch",
                    "details": "",
                }
            )
    metrics = {
        **{key: int(value) for key, value in counters.items()},
        "final_v0_3_3_rows": len(rows),
        "final_unique_tasks": len({row["source_task_id"] for row in rows}),
        "duplicate_task_count": len(rows) - len({row["source_task_id"] for row in rows}),
        "human_fields_autofilled_count": int(counters["human_fields_autofilled_count"]),
        "validation_issue_count": len(issues),
    }
    write_json(patch_dir / "final_pack_regression_summary_v0_3_3.json", metrics)
    write_csv(
        patch_dir / "final_pack_validation_issues_v0_3_3.csv",
        issues,
        ["review_item_id", "source_task_id", "issue", "details"],
    )
    return metrics, issues


def write_go_no_go(path: Path, summary: dict[str, Any], generated_at: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""# Composable Task Necessity Patch Go / No-Go v0.3.3

Generated at: `{generated_at}`

Machine rule spec: `v1.0`

## Fixed fields

- v0_3_2_input_rows = `{summary['v0_3_2_input_rows']}`
- gold_service_count_lt_2_count = `{summary['gold_service_count_lt_2_count']}`
- same_service_only_dependency_count = `{summary['same_service_only_dependency_count']}`
- no_cross_service_dependency_count = `{summary['no_cross_service_dependency_count']}`
- failed_dependency_count = `{summary['failed_dependency_count']}`
- exact_service_leak_count = `{summary['exact_service_leak_count']}`
- exact_api_leak_count = `{summary['exact_api_leak_count']}`
- redundant_recomputation_risk_count = `{summary['redundant_recomputation_risk_count']}`
- parallel_subgoal_risk_count = `{summary['parallel_subgoal_risk_count']}`
- retained_old_rows = `{summary['retained_old_rows']}`
- replacement_rows = `{summary['replacement_rows']}`
- final_v0_3_3_rows = `{summary['final_v0_3_3_rows']}`
- final_unique_tasks = `{summary['final_unique_tasks']}`
- final_cross_service_dependency_valid_count = `{summary['final_cross_service_dependency_valid_count']}`
- final_service_candidate_valid_count = `{summary['final_service_candidate_valid_count']}`
- final_api_candidate_valid_count = `{summary['final_api_candidate_valid_count']}`
- human_fields_autofilled_count = `{summary['human_fields_autofilled_count']}`

## Decision

- can_resume_composable_human_review = `{str(summary['can_resume_composable_human_review']).lower()}`
- can_claim_composable_service_benchmark_now = `false`
- can_claim_composable_api_benchmark_now = `false`
- can_start_full_six_task_assembly = `false`
- can_generate_final_dataset = `false`
- recommended_next_step = `{summary['recommended_next_step']}`

A trace edge remains machine evidence only. Human review must judge task-level dependency necessity, full-query gold coverage, parallel subgoals, and final labels.
""",
        encoding="utf-8",
    )


def write_report(
    path: Path,
    summary: dict[str, Any],
    generated_at: str,
    inputs: dict[str, Path],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    input_lines = "\n".join(f"- `{key}`: `{value}`" for key, value in inputs.items())
    path.write_text(
        f"""# Composable Task Necessity Patch v0.3.3 Report

Generated at: `{generated_at}`

## Inputs

{input_lines}

## Scope

This run applied the frozen machine-review rule specification v1.0 to the existing v0.3.2 pack and corrected strong reserve. It did not rescan raw ToolBench directories, rerun corpus mining, modify the v0.3.2 dependency extractor, call an LLM/API, or fill human fields.

## v0.3.2 re-audit

- input rows: `{summary['v0_3_2_input_rows']}`
- gold service count < 2: `{summary['gold_service_count_lt_2_count']}`
- same-service-only dependency: `{summary['same_service_only_dependency_count']}`
- no cross-service dependency: `{summary['no_cross_service_dependency_count']}`
- failed dependency: `{summary['failed_dependency_count']}`
- exact service/API leak: `{summary['exact_service_leak_count']}` / `{summary['exact_api_leak_count']}`
- redundancy/parallel risks: `{summary['redundant_recomputation_risk_count']}` / `{summary['parallel_subgoal_risk_count']}`

## v0.3.3 pack

- retained old rows: `{summary['retained_old_rows']}`
- replacement rows: `{summary['replacement_rows']}`
- final rows / unique tasks: `{summary['final_v0_3_3_rows']}` / `{summary['final_unique_tasks']}`
- cross-service valid: `{summary['final_cross_service_dependency_valid_count']}`
- service/API candidate-space valid: `{summary['final_service_candidate_valid_count']}` / `{summary['final_api_candidate_valid_count']}`
- human fields autofilled: `{summary['human_fields_autofilled_count']}`

## Boundary

Machine statuses are structural routing decisions, not `true_composable` labels. Redundancy, parallel-subgoal, hybrid, and incomplete-gold-chain fields remain risk flags requiring human review. v0.3.2 is retained unchanged and marked `SUPERSEDED_PENDING_V0_3_3` for future review.

## Decision

- can_resume_composable_human_review: `{str(summary['can_resume_composable_human_review']).lower()}`
- source freeze / six-task assembly / final dataset / split / baseline / training: `false`
- next: `{summary['recommended_next_step']}`
""",
        encoding="utf-8",
    )


def update_master_plan(path: Path, generated_at: str, summary: dict[str, Any]) -> Path:
    backup = path.with_name(path.name + ".pre_v0_3_3_backup")
    if not backup.exists():
        shutil.copy2(path, backup)
    content = path.read_text(encoding="utf-8-sig")
    pattern = r"<!-- BEGIN GATE4 V0\.3\.2 VARIABLE STATUS -->.*?<!-- END GATE4 V0\.3\.2 VARIABLE STATUS -->"
    block = f"""<!-- BEGIN GATE4 V0.3.3 VARIABLE STATUS -->

### v0.3.3 variable status ({generated_at})

- Gate 4 status: `TASK_NECESSITY_CORRECTED_REVIEW_PACK_READY`;
- machine review rule spec: `v1.0`;
- v0.3.2 review pack: `SUPERSEDED_PENDING_V0_3_3`;
- authoritative review pack: `composable_paired_task_review_items_v0_3_3.csv`;
- final structurally eligible review rows: `{summary['final_v0_3_3_rows']}`;
- human-confirmed composable count: `0`;
- stopping condition remains: `both-level eligible underlying tasks >= 100`;
- current next action: `review task-level dependency necessity and full-query gold coverage in v0.3.3 only`;
- human-final authority and all frozen benchmark-only constraints remain unchanged.

<!-- END GATE4 V0.3.3 VARIABLE STATUS -->"""
    if not re.search(pattern, content, flags=re.DOTALL):
        raise RuntimeError("Gate 4 v0.3.2 variable-status block was not found")
    content = re.sub(pattern, block, content, count=1, flags=re.DOTALL)
    gate_start = content.find("## Gate 4")
    gate_end = content.find("## Gate 5", gate_start)
    if gate_start >= 0 and gate_end > gate_start:
        section = content[gate_start:gate_end]
        section = re.sub(
            r"(状态：)`[^`]+`",
            r"\1`TASK_NECESSITY_CORRECTED_REVIEW_PACK_READY`",
            section,
            count=1,
        )
        content = content[:gate_start] + section + content[gate_end:]
    changelog = f"""

## v1.5 - {generated_at[:10]}

- Applied frozen Composable machine-review rules v1.0 without modifying dependency extractor v0.3.2 or rescanning raw traces.
- Added task-level necessity and cross-service hard gates; same-service workflows are routed to an API-only diagnostic reserve.
- Marked v0.3.2 superseded for future review and designated v0.3.3 as the authoritative structural review pack with `{summary['final_v0_3_3_rows']}` rows.
- Human-confirmed count remains `0`; source freeze, six-task assembly, final dataset, split, baseline, and training remain prohibited.
"""
    marker = "# 13. Change Log"
    if changelog.strip() not in content:
        position = content.find(marker)
        if position < 0:
            raise RuntimeError("Master Plan Change Log marker not found")
        insert_at = position + len(marker)
        content = content[:insert_at] + changelog + content[insert_at:]
    path.write_text(content, encoding="utf-8")
    return backup


def archive_outputs(
    root: Path,
    archive_dir: Path,
    paths: list[Path],
    constraints: dict[str, bool],
) -> None:
    archive_dir.mkdir(parents=True, exist_ok=True)
    for source in paths:
        if source.exists() and source.is_file():
            shutil.copy2(source, archive_dir / source.name)
    files = []
    for path in sorted(archive_dir.iterdir(), key=lambda item: item.name.casefold()):
        if path.is_file() and path.name != "archive_manifest_v0_3_3.json":
            files.append(
                {"filename": path.name, "bytes": path.stat().st_size, "sha256": sha256(path)}
            )
    write_json(
        archive_dir / "archive_manifest_v0_3_3.json",
        {
            "generated_at": now_iso(),
            "archive_dir": str(archive_dir),
            "file_count": len(files),
            "constraints": constraints,
            "files": files,
        },
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Apply task-necessity and cross-service gates to composable v0.3.2."
    )
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--patch-dir", type=Path, default=DEFAULT_PATCH_DIR)
    parser.add_argument("--pack-dir", type=Path, default=DEFAULT_PACK_DIR)
    parser.add_argument("--archive-dir", type=Path, default=DEFAULT_ARCHIVE_DIR)
    parser.add_argument("--target-rows", type=int, default=200)
    parser.add_argument("--skip-archive", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.project_root.resolve()
    patch_dir = (root / args.patch_dir).resolve()
    pack_dir = (root / args.pack_dir).resolve()
    archive_dir = (root / args.archive_dir).resolve()
    patch_dir.mkdir(parents=True, exist_ok=True)
    pack_dir.mkdir(parents=True, exist_ok=True)
    paths = resolve_inputs(root)
    generated_at = now_iso()
    before_hashes = {
        "v0_3_2_review_pack": sha256(paths["old_review"]),
        "v0_3_2_master": sha256(paths["old_master"]),
        "dependency_extractor_v0_3_2": sha256(root / "scripts/validation/composable_dependency_extractor_v0_3_2.py"),
        "machine_rules_v1_0": sha256(paths["rules"]),
        "master_plan": sha256(paths["master_plan"]),
    }
    test_summary = run_tests(root, paths["tests"], patch_dir)
    old_rows = read_csv(paths["old_review"])
    ranked_rows = read_csv(paths["corrected_ranked"])
    if len(old_rows) != 200:
        raise ValueError(f"Expected 200 v0.3.2 rows, found {len(old_rows)}")
    _, old_assessments, old_metrics = audit_old_pack(old_rows, patch_dir)
    master_rows, review_rows, pack_metrics = build_pack(
        root,
        paths,
        old_rows,
        old_assessments,
        ranked_rows,
        patch_dir,
        pack_dir,
        args.target_rows,
    )
    migration_rows = build_migration_manifest(root, old_rows, review_rows, pack_dir)
    double_rows = build_double_subset(review_rows, 40)
    write_csv(
        pack_dir / "composable_double_annotation_subset_40_v0_3_3.csv", double_rows
    )
    regression, issues = validate_final_pack(review_rows, patch_dir)

    if sha256(paths["old_review"]) != before_hashes["v0_3_2_review_pack"]:
        raise RuntimeError("v0.3.2 review pack was modified")
    if sha256(paths["old_master"]) != before_hashes["v0_3_2_master"]:
        raise RuntimeError("v0.3.2 master table was modified")
    if sha256(root / "scripts/validation/composable_dependency_extractor_v0_3_2.py") != before_hashes["dependency_extractor_v0_3_2"]:
        raise RuntimeError("Dependency extractor v0.3.2 was modified")

    summary = {
        "generated_at": generated_at,
        **test_summary,
        "v0_3_2_input_rows": old_metrics["input_rows"],
        "gold_service_count_lt_2_count": old_metrics["gold_service_count_lt_2"],
        "same_service_only_dependency_count": old_metrics["same_service_only_dependency"],
        "no_cross_service_dependency_count": old_metrics["no_cross_service_strong_edge"],
        "failed_dependency_count": old_metrics["failed_downstream_dependency"],
        "exact_service_leak_count": old_metrics["exact_service_leak"],
        "exact_api_leak_count": old_metrics["exact_api_leak"],
        "redundant_recomputation_risk_count": old_metrics["possible_redundant_recomputation"],
        "parallel_subgoal_risk_count": old_metrics["disconnected_parallel_subgoal_risk"],
        **pack_metrics,
        **regression,
        "migration_manifest_rows": len(migration_rows),
        "double_annotation_subset_rows": len(double_rows),
        "human_confirmed_composable_count": 0,
        "v0_3_2_overwritten": False,
        "dependency_extractor_v0_3_2_modified": False,
        "raw_trace_rescanned": False,
        "corpus_mining_run": False,
        "review_app_generated": False,
        "can_claim_composable_service_benchmark_now": False,
        "can_claim_composable_api_benchmark_now": False,
        "can_start_full_six_task_assembly": False,
        "can_generate_final_dataset": False,
    }
    go = bool(
        test_summary["tests_failed"] == 0
        and summary["final_v0_3_3_rows"] >= 100
        and summary["final_v0_3_3_rows"] == summary["final_unique_tasks"]
        and summary["final_v0_3_3_rows"] == summary["final_cross_service_dependency_valid_count"]
        and summary["final_v0_3_3_rows"] == summary["final_service_candidate_valid_count"]
        and summary["final_v0_3_3_rows"] == summary["final_api_candidate_valid_count"]
        and summary["final_v0_3_3_rows"] == summary["final_structural_hard_gate_pass_count"]
        and summary["final_failed_dependency_count"] == 0
        and summary["final_exact_service_leak_count"] == 0
        and summary["final_exact_api_leak_count"] == 0
        and summary["human_fields_autofilled_count"] == 0
        and summary["duplicate_task_count"] == 0
        and not issues
    )
    summary["can_resume_composable_human_review"] = go
    summary["recommended_next_step"] = (
        "review only v0.3.3; judge task-level dependency necessity and full-query gold coverage; do not continue reviewing v0.3.2."
        if go
        else "report structural candidate shortage; do not relax paired-composable rules or resume human review."
    )
    summary_path = patch_dir / "composable_task_necessity_patch_summary_v0_3_3.json"
    write_json(summary_path, summary)
    go_no_go = root / "docs/phase1/composable_task_necessity_patch_go_no_go_v0_3_3.md"
    report = root / "docs/phase1/composable_task_necessity_patch_report_v0_3_3.md"
    write_go_no_go(go_no_go, summary, generated_at)
    write_report(report, summary, generated_at, paths)
    master_backup = update_master_plan(paths["master_plan"], generated_at, summary)
    hashes = {
        "generated_at": generated_at,
        "before": before_hashes,
        "after": {
            "v0_3_2_review_pack": sha256(paths["old_review"]),
            "v0_3_2_master": sha256(paths["old_master"]),
            "dependency_extractor_v0_3_2": sha256(root / "scripts/validation/composable_dependency_extractor_v0_3_2.py"),
            "v0_3_3_review_pack": sha256(pack_dir / "composable_paired_task_review_items_v0_3_3.csv"),
            "machine_rules_v1_0": sha256(paths["rules"]),
            "master_plan": sha256(paths["master_plan"]),
            "master_plan_backup": sha256(master_backup),
        },
    }
    hash_path = patch_dir / "before_after_sha256_v0_3_3.json"
    write_json(hash_path, hashes)

    if not args.skip_archive:
        archive_outputs(
            root,
            archive_dir,
            [
                paths["rules"],
                paths["master_plan"],
                master_backup,
                root / "scripts/validation/composable_task_necessity_gate_v0_3_3.py",
                Path(__file__).resolve(),
                paths["tests"],
                patch_dir / "composable_task_necessity_test_results_v0_3_3.txt",
                patch_dir / "v0_3_2_task_necessity_reaudit.csv",
                patch_dir / "v0_3_2_task_necessity_reaudit_summary.json",
                patch_dir / "corrected_strong_reserve_necessity_prefilter.csv",
                patch_dir / "v0_3_3_candidate_selection_trace.csv",
                patch_dir / "final_pack_regression_summary_v0_3_3.json",
                patch_dir / "final_pack_validation_issues_v0_3_3.csv",
                summary_path,
                hash_path,
                pack_dir / "composable_underlying_tasks_master_v0_3_3.csv",
                pack_dir / "composable_paired_task_review_items_v0_3_3.csv",
                pack_dir / "composable_service_discovery_provisional_rows_v0_3_3.csv",
                pack_dir / "composable_api_recommendation_provisional_rows_v0_3_3.csv",
                pack_dir / "v0_3_2_to_v0_3_3_review_migration_manifest.csv",
                pack_dir / "composable_double_annotation_subset_40_v0_3_3.csv",
                go_no_go,
                report,
            ],
            {
                "raw_trace_rescanned": False,
                "corpus_mining_run": False,
                "dependency_extractor_rewritten": False,
                "human_fields_autofilled": False,
                "source_frozen": False,
                "six_task_assembly_run": False,
                "final_dataset_generated": False,
                "split_created": False,
                "baseline_run": False,
                "model_trained": False,
                "external_api_used": False,
            },
        )

    fixed = [
        "v0_3_2_input_rows",
        "gold_service_count_lt_2_count",
        "same_service_only_dependency_count",
        "no_cross_service_dependency_count",
        "failed_dependency_count",
        "exact_service_leak_count",
        "exact_api_leak_count",
        "redundant_recomputation_risk_count",
        "parallel_subgoal_risk_count",
        "retained_old_rows",
        "replacement_rows",
        "final_v0_3_3_rows",
        "final_unique_tasks",
        "final_cross_service_dependency_valid_count",
        "final_service_candidate_valid_count",
        "final_api_candidate_valid_count",
        "human_fields_autofilled_count",
        "can_resume_composable_human_review",
        "recommended_next_step",
    ]
    for key in fixed:
        value = summary[key]
        if isinstance(value, bool):
            value = str(value).lower()
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
