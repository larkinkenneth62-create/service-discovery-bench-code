#!/usr/bin/env python
"""Reevaluate normalized ToolBench traces and rebuild composable pack v0.3.2.

The script reads only existing normalized traces and selected local source files
needed by the established paired-task constructor. It never traverses the raw
ToolBench corpus, calls an external API, or fills human review fields.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import composable_dependency_extractor_v0_3_2 as extractor  # noqa: E402
import prepare_composable_paired_tasks_v0_3 as prep  # noqa: E402
import repair_and_freeze_composable_pack_v0_3_1 as repair  # noqa: E402


VERSION = "v0.3.2"
ORDER_SEED = "COMPOSABLE-V0.3.2-CANDIDATE-ORDER-SEED-20260715"
DOUBLE_SEED = "COMPOSABLE-V0.3.2-DOUBLE-ANNOTATION-SEED-20260715"
DEFAULT_PATCH_DIR = Path("outputs/composable_dependency_extractor_patch_v0_3_2")
DEFAULT_PACK_DIR = Path("outputs/composable_paired_task_preparation_v0_3_2")
DEFAULT_ARCHIVE_DIR = Path("outputs/run_archives/2026-07-15_composable_dependency_extractor_patch_v0_3_2")

INPUTS = {
    "normalized": Path("outputs/composable_corpus_mining_v0_2/toolbench_full_normalized_multicall_steps.jsonl"),
    "legacy_edges": Path("outputs/composable_corpus_mining_v0_2/toolbench_full_dependency_edge_candidates.jsonl"),
    "legacy_status": Path("outputs/composable_corpus_mining_v0_2/toolbench_full_dependency_evidence_status.csv"),
    "legacy_ranked": Path("outputs/composable_corpus_mining_v0_2/composable_underlying_task_candidates_ranked.csv"),
    "legacy_review": Path("outputs/composable_corpus_mining_v0_2/composable_evidence_review_items_v0_2.csv"),
    "old_review_pack": Path("outputs/composable_paired_task_preparation_v0_3_1/composable_paired_task_review_items_v0_3_1.csv"),
    "old_service": Path("outputs/composable_paired_task_preparation_v0_3_1/composable_service_discovery_provisional_rows_v0_3_1.csv"),
    "old_api": Path("outputs/composable_paired_task_preparation_v0_3_1/composable_api_recommendation_provisional_rows_v0_3_1.csv"),
    "old_master": Path("outputs/composable_paired_task_preparation_v0_3_1/composable_underlying_tasks_master_v0_3_1.csv"),
    "old_ledger": Path("outputs/review_credit_ledger/composable_review_credit_ledger_v0_3_1.csv"),
    "catalog": Path("external_sources/ToolBench/data/toolenv/tools"),
    "master_plan": Path("docs/project/SERVICEDISCOVERYBENCH_BENCHMARK_MASTER_PLAN.md"),
    "qa_schema": Path("docs/phase1/source_qa_two_axis_adjudication_schema_v0_3.md"),
    "qa_schema_errata": Path("docs/phase1/source_qa_two_axis_adjudication_schema_errata_v0_3_1.md"),
    "old_go_no_go": Path("docs/phase1/composable_final_review_pack_freeze_go_no_go_v0_3_1.md"),
    "inventory": Path("docs/phase1/composable_dependency_extractor_patch_inventory_v0_3_2.md"),
    "unit_tests": Path("tests/validation/test_composable_dependency_extractor_v0_3_2.py"),
    "extractor": Path("scripts/validation/composable_dependency_extractor_v0_3_2.py"),
}

NEW_EDGE_FIELDS = [
    "edge_source_type", "upstream_field_role", "downstream_field_role",
    "upstream_source_path", "downstream_source_path", "evidence_value",
    "value_present_in_original_query", "value_present_in_upstream_arguments",
    "upstream_output_is_novel", "upstream_output_is_echo",
    "upstream_call_execution_status", "downstream_call_execution_status",
    "strong_edge_eligible", "shared_input_values_json",
    "incidental_or_failed_calls_json", "execution_evidence_incomplete",
    "dependency_edge_candidates_json", "strong_edge_count",
]

STATUS_PRIORITY = {
    "strong_objective_evidence_available": 5,
    "partial_objective_evidence": 4,
    "sequence_only": 3,
    "no_dependency_evidence": 2,
    "parse_failed": 1,
    "source_unavailable": 0,
    "join_ambiguous": 0,
}


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


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    csv.field_size_limit(min(sys.maxsize, 2**31 - 1))
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: Iterable[dict[str, Any]], fields: list[str] | None = None) -> None:
    materialized = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    if fields is None:
        fields = []
        seen: set[str] = set()
        for row in materialized:
            for key in row:
                if key not in seen:
                    fields.append(key)
                    seen.add(key)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(materialized)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def iter_jsonl(path: Path, parse_counter: Counter[str] | None = None) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                if parse_counter is not None:
                    parse_counter["jsonl_parse_failed"] += 1
                continue
            if isinstance(item, dict):
                item["_normalized_line_number"] = line_number
                yield item


def resolve_inputs(root: Path, patch_dir: Path) -> dict[str, Path]:
    paths = {name: root / relative for name, relative in INPUTS.items()}
    missing = [str(path) for path in paths.values() if not path.exists()]
    if missing:
        patch_dir.mkdir(parents=True, exist_ok=True)
        (patch_dir / "MISSING_INPUTS.md").write_text(
            "# Missing inputs\n\n" + "\n".join(f"- `{item}`" for item in missing) + "\n",
            encoding="utf-8",
        )
        raise FileNotFoundError("Required inputs are missing; see MISSING_INPUTS.md")
    return paths


def run_tests(root: Path, test_path: Path, patch_dir: Path) -> dict[str, Any]:
    result = subprocess.run(
        [sys.executable, str(test_path)],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    output = result.stdout + result.stderr
    test_result_path = patch_dir / "composable_dependency_extractor_test_results_v0_3_2.txt"
    test_result_path.parent.mkdir(parents=True, exist_ok=True)
    test_result_path.write_text(output, encoding="utf-8")
    match = re.search(r"Ran\s+(\d+)\s+tests?", output)
    tests_run = int(match.group(1)) if match else 0
    failed_match = re.search(r"FAILED\s+\((.*?)\)", output)
    failed = 0
    if failed_match:
        failed = sum(int(value) for value in re.findall(r"=(\d+)", failed_match.group(1)))
    passed = tests_run - failed if result.returncode == 0 else max(0, tests_run - failed)
    summary = {
        "tests_run": tests_run,
        "tests_passed": passed,
        "tests_failed": failed if failed else (0 if result.returncode == 0 else 1),
        "return_code": result.returncode,
        "test_results_file": str(test_result_path),
    }
    if result.returncode != 0:
        raise RuntimeError(f"Extractor unit tests failed; see {test_result_path}")
    return summary


def record_counts(record: dict[str, Any]) -> dict[str, int]:
    steps = [step for step in record.get("steps", []) if isinstance(step, dict)]
    return {
        "step_count": len(steps),
        "distinct_service_count": len({text(step.get("service_name")) for step in steps if text(step.get("service_name"))}),
        "distinct_api_count": len({text(step.get("function_name") or step.get("api_name")) for step in steps if text(step.get("function_name") or step.get("api_name"))}),
        "arguments_available": int(any(step.get("arguments") not in (None, "", {}, []) for step in steps)),
        "outputs_available": int(any(step.get("outputs") not in (None, "", {}, []) for step in steps)),
        "observations_available": int(any(step.get("observation") not in (None, "", {}, []) for step in steps)),
    }


def task_summary_row(record: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    counts = record_counts(result["record"])
    source_counts = Counter(result["edge_source_type_counts"])
    dependency_types = Counter(text(edge.get("dependency_type")) for edge in result["strong_edges"])
    score = (
        result["strong_edge_count"] * 100
        + counts["distinct_service_count"] * 5
        + counts["distinct_api_count"] * 3
        + counts["step_count"]
    )
    return {
        "source_dataset": text(record.get("source_dataset")) or "ToolBench",
        "source_task_id": text(record.get("source_task_id")),
        "instruction_query_id": text(record.get("instruction_query_id")),
        "source_group": text(record.get("source_group")),
        "query_text": text(record.get("query_text")),
        "source_area": text(record.get("source_area")),
        "source_file": text(record.get("source_file")),
        "source_record_path": text(record.get("source_record_path")) or "$",
        "trace_record_id": text(record.get("trace_record_id")),
        "normalized_line_number": int_value(record.get("_normalized_line_number")),
        **counts,
        "strong_edge_count": result["strong_edge_count"],
        "data_flow_edge_count": dependency_types["data_flow"],
        "entity_flow_edge_count": dependency_types["entity_flow"],
        "control_flow_edge_count": source_counts["upstream_result_to_tool_selection"],
        "conditional_flow_edge_count": source_counts["upstream_result_to_branch_condition"],
        "sequence_edge_count": source_counts["sequence_only"],
        "shared_input_only_count": source_counts["shared_input_only"],
        "query_known_value_reuse_count": source_counts["query_known_value_reuse"],
        "echoed_upstream_input_count": source_counts["echoed_upstream_input"],
        "failed_call_or_error_output_count": source_counts["failed_call_or_error_output"],
        "unsupported_edge_type_count": source_counts["unsupported_edge_type"],
        "failed_call_count": len(result["failed_calls"]),
        "parse_error_count": len(result["parse_errors"]),
        "edge_source_type_distribution_json": json_dumps(result["edge_source_type_counts"]),
        "call_execution_status_distribution_json": json_dumps(result["call_execution_status_counts"]),
        "shared_input_values_json": json_dumps(result["shared_input_values"]),
        "incidental_or_failed_calls_json": json_dumps(result["failed_calls"]),
        "execution_evidence_incomplete": bool_text(result["execution_evidence_incomplete"]),
        "source_trace_complete": bool_text(bool(text(record.get("source_file"))) and counts["step_count"] >= 2),
        "evidence_status": result["evidence_status"],
        "suggested_class": result["suggested_class"],
        "evidence_score": score,
    }


def best_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        STATUS_PRIORITY.get(text(row.get("evidence_status")), -1),
        int_value(row.get("strong_edge_count")),
        int_value(row.get("evidence_score")),
        -int_value(row.get("normalized_line_number")),
        text(row.get("trace_record_id")),
    )


def corpus_reevaluation(
    normalized_path: Path,
    legacy_ranked_rows: list[dict[str, str]],
    patch_dir: Path,
    common_value_threshold: int,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, dict[str, Any]]]:
    parse_counter: Counter[str] = Counter()
    argument_frequency = extractor.collect_argument_frequency(iter_jsonl(normalized_path, parse_counter))
    old_strong_trace_ids = {
        text(row.get("trace_record_id"))
        for row in legacy_ranked_rows if text(row.get("evidence_status")) == "strong_objective_evidence_available"
    }
    edge_path = patch_dir / "corrected_dependency_edge_candidates.jsonl"
    status_path = patch_dir / "corrected_dependency_evidence_status.csv"
    edge_path.parent.mkdir(parents=True, exist_ok=True)
    status_fields = [
        "trace_record_id", "source_dataset", "source_group", "source_task_id", "instruction_query_id",
        "query_text", "source_file", "source_record_path", "step_count", "distinct_service_count",
        "distinct_api_count", "arguments_available", "outputs_available", "observations_available",
        "strong_edge_count", "shared_input_only_count", "query_known_value_reuse_count",
        "echoed_upstream_input_count", "failed_call_or_error_output_count", "sequence_edge_count",
        "failed_call_count", "parse_error_count", "edge_source_type_distribution_json",
        "call_execution_status_distribution_json", "shared_input_values_json",
        "incidental_or_failed_calls_json", "execution_evidence_incomplete", "evidence_status",
        "suggested_class", "parse_status", "parse_error",
    ]
    best_by_task: dict[str, dict[str, Any]] = {}
    old_trace_summary: dict[str, dict[str, Any]] = {}
    trace_count = 0
    edge_count = 0
    with edge_path.open("w", encoding="utf-8") as edge_handle, status_path.open("w", encoding="utf-8-sig", newline="") as status_handle:
        writer = csv.DictWriter(status_handle, fieldnames=status_fields, extrasaction="ignore")
        writer.writeheader()
        for record in iter_jsonl(normalized_path, parse_counter):
            trace_count += 1
            result = extractor.assess_record(
                record,
                common_argument_frequency=argument_frequency,
                common_value_threshold=common_value_threshold,
            )
            for edge in result["edges"]:
                edge_handle.write(json.dumps(edge, ensure_ascii=False, sort_keys=True) + "\n")
                edge_count += 1
            row = task_summary_row(record, result)
            parse_summary = record.get("parse_summary") if isinstance(record.get("parse_summary"), dict) else {}
            row["parse_status"] = text(parse_summary.get("parse_status")) or "ok"
            row["parse_error"] = text(parse_summary.get("parse_error"))
            writer.writerow(row)
            task_id = row["source_task_id"]
            if task_id and (task_id not in best_by_task or best_key(row) > best_key(best_by_task[task_id])):
                best_by_task[task_id] = row
            if row["trace_record_id"] in old_strong_trace_ids:
                old_trace_summary[row["trace_record_id"]] = row

    ranked = sorted(
        best_by_task.values(),
        key=lambda row: (
            -STATUS_PRIORITY.get(text(row.get("evidence_status")), -1),
            -int_value(row.get("strong_edge_count")),
            -int_value(row.get("evidence_score")),
            text(row.get("source_task_id")),
        ),
    )
    legacy_by_task = {text(row.get("source_task_id")): row for row in legacy_ranked_rows}
    for rank, row in enumerate(ranked, start=1):
        row["evidence_rank"] = rank
        legacy = legacy_by_task.get(row["source_task_id"], {})
        row["legacy_evidence_status"] = text(legacy.get("evidence_status"))
        row["legacy_evidence_rank"] = text(legacy.get("evidence_rank"))
        row["current_322_member"] = text(legacy.get("current_322_member"))
        row["current_review_pack_member"] = text(legacy.get("current_review_pack_member"))
        row["selection_note"] = "v0.3.2 role-aware normalized-trace reevaluation; no semantic inference"
    write_csv(patch_dir / "corrected_underlying_task_candidates_ranked.csv", ranked)
    stats = {
        "normalized_trace_record_count": trace_count,
        "corrected_edge_candidate_count": edge_count,
        "unique_underlying_task_count": len(ranked),
        "argument_frequency_unique_value_count": len(argument_frequency),
        "jsonl_parse_failed_count": parse_counter["jsonl_parse_failed"],
        "common_value_threshold": common_value_threshold,
    }
    return ranked, stats, old_trace_summary


def comparison_outputs(
    legacy_ranked_rows: list[dict[str, str]],
    corrected_ranked: list[dict[str, Any]],
    old_trace_summary: dict[str, dict[str, Any]],
    patch_dir: Path,
) -> dict[str, Any]:
    old_strong = [row for row in legacy_ranked_rows if text(row.get("evidence_status")) == "strong_objective_evidence_available"]
    if len(old_strong) != 816:
        raise ValueError(f"Expected legacy strong candidate count 816, found {len(old_strong)}")
    corrected_strong = [row for row in corrected_ranked if text(row.get("evidence_status")) == "strong_objective_evidence_available"]
    corrected_strong_ids = {row["source_task_id"] for row in corrected_strong}
    old_strong_ids = {text(row.get("source_task_id")) for row in old_strong}
    retained: list[dict[str, Any]] = []
    downgraded: list[dict[str, Any]] = []
    for old in old_strong:
        corrected = old_trace_summary.get(text(old.get("trace_record_id")), {})
        merged = {
            **{f"old_{key}": value for key, value in old.items()},
            **{f"corrected_{key}": value for key, value in corrected.items()},
            "source_task_id": text(old.get("source_task_id")),
            "trace_record_id": text(old.get("trace_record_id")),
            "old_evidence_status": text(old.get("evidence_status")),
            "corrected_evidence_status": text(corrected.get("evidence_status")) or "parse_failed",
        }
        if merged["corrected_evidence_status"] == "strong_objective_evidence_available":
            retained.append(merged)
        else:
            reasons = []
            for field, label in (
                ("shared_input_only_count", "shared_input_only"),
                ("query_known_value_reuse_count", "query_known_value_reuse"),
                ("echoed_upstream_input_count", "echoed_upstream_input"),
                ("failed_call_or_error_output_count", "failed_call_or_error_output"),
            ):
                if int_value(corrected.get(field)):
                    reasons.append(label)
            if not reasons:
                reasons.append(text(corrected.get("evidence_status")) or "other_invalid_edge")
            merged["downgrade_reasons"] = "|".join(reasons)
            downgraded.append(merged)
    newly_promoted = [row for row in corrected_strong if row["source_task_id"] not in old_strong_ids]
    write_csv(patch_dir / "downgraded_old_strong_candidates.csv", downgraded)
    write_csv(patch_dir / "newly_promoted_strong_candidates.csv", newly_promoted)

    flag_specs = {
        "shared_input_only_tasks.csv": "shared_input_only_count",
        "query_known_value_tasks.csv": "query_known_value_reuse_count",
        "echoed_input_tasks.csv": "echoed_upstream_input_count",
        "failed_call_evidence_tasks.csv": "failed_call_or_error_output_count",
    }
    for filename, field in flag_specs.items():
        write_csv(patch_dir / filename, [row for row in corrected_ranked if int_value(row.get(field)) > 0])
    summary = {
        "old_strong_candidate_count": len(old_strong),
        "corrected_strong_candidate_count": len(corrected_strong),
        "old_strong_retained_count": len(retained),
        "old_strong_downgraded_count": len(downgraded),
        "newly_promoted_count": len(newly_promoted),
        "shared_input_false_positive_count": sum(int_value(row.get("corrected_shared_input_only_count")) > 0 for row in downgraded),
        "query_known_false_positive_count": sum(int_value(row.get("corrected_query_known_value_reuse_count")) > 0 for row in downgraded),
        "echoed_input_false_positive_count": sum(int_value(row.get("corrected_echoed_upstream_input_count")) > 0 for row in downgraded),
        "failed_call_false_positive_count": sum(int_value(row.get("corrected_failed_call_or_error_output_count")) > 0 for row in downgraded),
        "sequence_only_count": sum(text(row.get("evidence_status")) == "sequence_only" for row in corrected_ranked),
        "no_dependency_count": sum(text(row.get("evidence_status")) == "no_dependency_evidence" for row in corrected_ranked),
        "parse_failed_count": sum(text(row.get("evidence_status")) == "parse_failed" for row in corrected_ranked),
        "corrected_strong_source_task_ids": sorted(corrected_strong_ids),
    }
    return summary


def audit_old_pack(
    old_review_rows: list[dict[str, str]],
    argument_frequency: Counter[str],
    common_value_threshold: int,
    patch_dir: Path,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, Any]]:
    audit_rows: list[dict[str, Any]] = []
    assessments: dict[str, dict[str, Any]] = {}
    for row in old_review_rows:
        record = {
            "trace_record_id": hashlib.sha1(text(row.get("source_trace_path")).encode("utf-8")).hexdigest(),
            "source_dataset": text(row.get("source_dataset")),
            "source_group": text(row.get("source_group")),
            "source_task_id": text(row.get("source_task_id")),
            "instruction_query_id": "",
            "query_text": text(row.get("query_text")),
            "source_file": text(row.get("source_trace_path")),
            "source_record_path": text(row.get("source_record_path")) or "$",
            "parse_summary": {"parse_status": "ok"},
            "steps": parse_json(row.get("ordered_steps_json"), []),
        }
        result = extractor.assess_record(
            record,
            common_argument_frequency=argument_frequency,
            common_value_threshold=common_value_threshold,
        )
        assessments[row["source_task_id"]] = result
        counts = Counter(result["edge_source_type_counts"])
        old_edges = parse_json(row.get("dependency_edges_json"), [])
        corrected_eligible = result["evidence_status"] == "strong_objective_evidence_available"
        reasons = []
        if not corrected_eligible:
            for key, label in (
                ("shared_input_only", "shared_input_only"),
                ("query_known_value_reuse", "query_known_value"),
                ("echoed_upstream_input", "echoed_input"),
                ("failed_call_or_error_output", "failed_call"),
            ):
                if counts[key]:
                    reasons.append(label)
            if not reasons:
                reasons.append("other_invalid_edge")
        first = result["strong_edges"][0] if result["strong_edges"] else (result["edges"][0] if result["edges"] else {})
        audit_rows.append({
            "review_item_id": row.get("review_item_id", ""),
            "underlying_task_id": row.get("underlying_task_id", ""),
            "source_task_id": row.get("source_task_id", ""),
            "old_evidence_status": row.get("evidence_status", ""),
            "corrected_evidence_status": result["evidence_status"],
            "old_edge_source_type": "legacy_exact_scalar_reuse" if old_edges else "none",
            "corrected_edge_source_type": json_dumps(result["edge_source_type_counts"]),
            "upstream_field_role": first.get("upstream_field_role", ""),
            "downstream_field_role": first.get("downstream_field_role", ""),
            "shared_input_only": bool_text(bool(counts["shared_input_only"])),
            "query_known_value": bool_text(bool(counts["query_known_value_reuse"])),
            "echoed_input": bool_text(bool(counts["echoed_upstream_input"])),
            "failed_call_used": bool_text(bool(counts["failed_call_or_error_output"])),
            "old_pack_eligible": "true",
            "corrected_pack_eligible": bool_text(corrected_eligible),
            "old_edge_count": len(old_edges),
            "corrected_strong_edge_count": result["strong_edge_count"],
            "corrected_dependency_edges_json": json_dumps(result["strong_edges"]),
            "corrected_all_edge_candidates_json": json_dumps(result["edges"]),
            "shared_input_values_json": json_dumps(result["shared_input_values"]),
            "incidental_or_failed_calls_json": json_dumps(result["failed_calls"]),
            "invalidation_reason": "|".join(reasons),
            "old_pack_status": "SUPERSEDED_PENDING_V0_3_2",
        })
    false_rows = [row for row in audit_rows if row["corrected_pack_eligible"] != "true"]
    write_csv(patch_dir / "current_v0_3_1_pack_dependency_reaudit.csv", audit_rows)
    write_csv(patch_dir / "current_v0_3_1_pack_false_strong_rows.csv", false_rows)
    summary = {
        "current_pack_rows": len(audit_rows),
        "still_strong_after_patch": len(audit_rows) - len(false_rows),
        "downgraded_due_shared_input": sum("shared_input_only" in row["invalidation_reason"] for row in false_rows),
        "downgraded_due_query_known_value": sum("query_known_value" in row["invalidation_reason"] for row in false_rows),
        "downgraded_due_echoed_input": sum("echoed_input" in row["invalidation_reason"] for row in false_rows),
        "downgraded_due_failed_call": sum("failed_call" in row["invalidation_reason"] for row in false_rows),
        "downgraded_due_other_invalid_edge": sum(row["invalidation_reason"] == "other_invalid_edge" for row in false_rows),
        "unchanged_strong_rows": len(audit_rows) - len(false_rows),
        "pack_status": "SUPERSEDED_PENDING_V0_3_2",
    }
    write_json(patch_dir / "current_v0_3_1_pack_reaudit_summary.json", summary)
    return audit_rows, assessments, summary


def load_selected_normalized(path: Path, trace_ids: set[str]) -> dict[str, dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    for record in iter_jsonl(path):
        trace_id = text(record.get("trace_record_id"))
        if trace_id in trace_ids:
            selected[trace_id] = record
            if len(selected) == len(trace_ids):
                break
    return selected


def make_source_review(record: dict[str, Any], result: dict[str, Any], ranked: dict[str, Any]) -> dict[str, Any]:
    edge_types = Counter(text(edge.get("dependency_type")) or "unknown" for edge in result["strong_edges"])
    return {
        "review_item_id": "v0_3_2_machine_provisional",
        "source_task_id": text(record.get("source_task_id")),
        "source_dataset": text(record.get("source_dataset")) or "ToolBench",
        "source_group": text(record.get("source_group")),
        "query_text": text(record.get("query_text")),
        "ordered_steps_json": json_dumps(result["record"].get("steps", [])),
        "services_json": "[]",
        "apis_json": "[]",
        "dependency_edges_json": json_dumps(result["strong_edges"]),
        "dependency_evidence_json": json_dumps({
            "edge_type_distribution": dict(sorted(edge_types.items())),
            "edge_source_type_distribution": result["edge_source_type_counts"],
            "machine_evidence_only": True,
            "objective_edge_count": result["strong_edge_count"],
            "shared_input_values": result["shared_input_values"],
            "failed_calls": result["failed_calls"],
            "embedded_parse_errors": result["parse_errors"],
            "extractor_version": VERSION,
        }),
        "source_trace_path": text(record.get("source_file")),
        "source_answer_path": text(record.get("source_file")),
        "evidence_status": result["evidence_status"],
        "evidence_score": text(ranked.get("evidence_score")),
        "current_322_member": text(ranked.get("current_322_member")),
    }


def deterministic_order(items: list[dict[str, Any]], source_task_id: str, key_field: str) -> list[dict[str, Any]]:
    return sorted(
        items,
        key=lambda item: (
            hashlib.sha256(f"{ORDER_SEED}|{source_task_id}|{text(item.get(key_field))}".encode("utf-8")).hexdigest(),
            text(item.get(key_field)),
        ),
    )


def enrich_master_row(
    row: dict[str, Any],
    result: dict[str, Any],
    old_review: dict[str, str] | None,
    index: int,
    frozen_at: str,
) -> dict[str, Any]:
    item = dict(row)
    first = result["strong_edges"][0]
    if old_review:
        item["underlying_task_id"] = old_review["underlying_task_id"]
    else:
        item["underlying_task_id"] = f"COMPOSABLE-UNDERLYING-V0.3.2-{index:04d}"
    item["paired_task_group_id"] = f"COMPOSABLE-PAIR-V0.3.2-{index:04d}"
    item["split_group_id"] = f"TOOLBENCH-{item['source_task_id']}"
    services = deterministic_order(parse_json(item.get("candidate_services_json"), []), item["source_task_id"], "service_key")
    apis = deterministic_order(parse_json(item.get("candidate_apis_json"), []), item["source_task_id"], "function_key")
    mapping = parse_json(item.get("service_api_map_json"), [])
    mapping_by_api = {text(entry.get("function_key")): entry for entry in mapping if isinstance(entry, dict)}
    item["candidate_services_json"] = json_dumps(services)
    item["candidate_apis_json"] = json_dumps(apis)
    item["service_api_map_json"] = json_dumps([
        mapping_by_api[text(api.get("function_key"))]
        for api in apis if text(api.get("function_key")) in mapping_by_api
    ])
    item["dependency_edges_json"] = json_dumps(result["strong_edges"])
    item["dependency_edge_candidates_json"] = json_dumps(result["edges"])
    item["ordered_steps_json"] = json_dumps(result["record"].get("steps", []))
    item["dependency_evidence_json"] = json_dumps({
        "machine_evidence_only": True,
        "extractor_version": VERSION,
        "objective_edge_count": result["strong_edge_count"],
        "edge_source_type_distribution": result["edge_source_type_counts"],
        "call_execution_status_distribution": result["call_execution_status_counts"],
        "shared_input_values": result["shared_input_values"],
        "failed_calls": result["failed_calls"],
        "embedded_parse_errors": result["parse_errors"],
    })
    item["dependency_type_distribution_json"] = json_dumps(Counter(
        text(edge.get("dependency_type")) for edge in result["strong_edges"]
    ))
    item.update({field: first.get(field, "") for field in [
        "edge_source_type", "upstream_field_role", "downstream_field_role",
        "upstream_source_path", "downstream_source_path", "evidence_value",
        "value_present_in_original_query", "value_present_in_upstream_arguments",
        "upstream_output_is_novel", "upstream_output_is_echo",
        "upstream_call_execution_status", "downstream_call_execution_status",
        "strong_edge_eligible",
    ]})
    item["strong_edge_count"] = result["strong_edge_count"]
    item["shared_input_values_json"] = json_dumps(result["shared_input_values"])
    item["incidental_or_failed_calls_json"] = json_dumps(result["failed_calls"])
    item["execution_evidence_incomplete"] = bool_text(result["execution_evidence_incomplete"])
    item["evidence_status"] = "strong_objective_evidence_available"
    item["requires_human_dependency_confirmation"] = "true"
    item["candidate_service_count"] = len(services)
    item["candidate_api_count"] = len(apis)
    item["candidate_order_seed"] = ORDER_SEED
    item["pack_version"] = VERSION
    item["pack_frozen_at"] = frozen_at
    item["pack_status"] = "CORRECTED_CONSOLIDATED_REVIEW_PACK_READY"
    item["preparation_script_version"] = "composable_paired_task_preparation_v0_3_2"
    item["review_content_hash"] = prep.review_hash(item)
    return item


def construct_candidate(
    root: Path,
    record: dict[str, Any],
    result: dict[str, Any],
    ranked: dict[str, Any],
    static_services: dict[str, dict[str, Any]],
    static_apis: dict[str, dict[str, Any]],
    service_to_apis: dict[str, list[str]],
) -> tuple[dict[str, Any] | None, str]:
    source_review = make_source_review(record, result, ranked)
    try:
        built, _, _, issues = prep.build_underlying_rows(
            root, {}, [source_review], [ranked], static_services, static_apis, service_to_apis
        )
    except Exception as exc:
        return None, f"construction_exception:{type(exc).__name__}:{exc}"
    fatal = [issue for issue in issues if text(issue.get("severity")) == "fatal"]
    if len(built) != 1 or fatal:
        return None, "construction_failed:" + "|".join(text(issue.get("issue_type")) for issue in fatal)
    row = built[0]
    valid, validation_issues = repair.row_structurally_valid(row)
    if not valid:
        return None, "|".join(validation_issues)
    return row, ""


def build_v032_pack(
    root: Path,
    paths: dict[str, Path],
    corrected_ranked: list[dict[str, Any]],
    old_review_rows: list[dict[str, str]],
    old_assessments: dict[str, dict[str, Any]],
    argument_frequency: Counter[str],
    common_value_threshold: int,
    pack_dir: Path,
    target_rows: int,
    replacement_scan_limit: int,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    frozen_at = now_iso()
    old_by_source = {row["source_task_id"]: row for row in old_review_rows}
    retained_candidates: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, str]]] = []
    ranked_by_source = {row["source_task_id"]: row for row in corrected_ranked}
    for old in old_review_rows:
        result = old_assessments[old["source_task_id"]]
        if result["evidence_status"] != "strong_objective_evidence_available":
            continue
        record = result["record"]
        record["source_file"] = text(old.get("source_trace_path"))
        record["source_record_path"] = text(old.get("source_record_path")) or "$"
        ranked = dict(ranked_by_source.get(old["source_task_id"], {}))
        ranked.update({
            "source_task_id": old["source_task_id"],
            "source_file": record["source_file"],
            "source_record_path": record["source_record_path"],
            "evidence_status": "strong_objective_evidence_available",
        })
        retained_candidates.append((record, result, ranked, old))

    old_source_ids = set(old_by_source)
    reserve_ranked = [
        row for row in corrected_ranked
        if text(row.get("evidence_status")) == "strong_objective_evidence_available"
        and row["source_task_id"] not in old_source_ids
    ][:replacement_scan_limit]
    reserve_trace_ids = {text(row.get("trace_record_id")) for row in reserve_ranked}
    normalized_by_trace = load_selected_normalized(paths["normalized"], reserve_trace_ids)
    static_services, static_apis, service_to_apis, catalog_stats = prep.load_static_catalog(paths["catalog"])

    selected: list[dict[str, Any]] = []
    selection_trace: list[dict[str, Any]] = []
    used_sources: set[str] = set()
    candidate_stream: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, str] | None, str]] = []
    candidate_stream.extend((record, result, ranked, old, "old_pack_retained_candidate") for record, result, ranked, old in retained_candidates)
    for ranked in reserve_ranked:
        record = normalized_by_trace.get(text(ranked.get("trace_record_id")))
        if not record:
            continue
        result = extractor.assess_record(
            record,
            common_argument_frequency=argument_frequency,
            common_value_threshold=common_value_threshold,
        )
        if result["evidence_status"] == "strong_objective_evidence_available":
            candidate_stream.append((record, result, ranked, None, "corrected_strong_reserve"))

    for record, result, ranked, old, origin in candidate_stream:
        if len(selected) >= target_rows:
            break
        source_id = text(record.get("source_task_id"))
        if not source_id or source_id in used_sources:
            continue
        built, reason = construct_candidate(
            root, record, result, ranked, static_services, static_apis, service_to_apis
        )
        trace_row = {
            "source_task_id": source_id,
            "trace_record_id": text(record.get("trace_record_id")),
            "origin": origin,
            "selection_status": "selected" if built else "rejected",
            "rejection_reason": reason,
            "old_underlying_task_id": text(old.get("underlying_task_id")) if old else "",
        }
        if not built:
            selection_trace.append(trace_row)
            continue
        index = len(selected) + 1
        enriched = enrich_master_row(built, result, old, index, frozen_at)
        selected.append(enriched)
        used_sources.add(source_id)
        trace_row["new_underlying_task_id"] = enriched["underlying_task_id"]
        trace_row["review_content_hash"] = enriched["review_content_hash"]
        selection_trace.append(trace_row)

    final_master = selected
    pack_dir.mkdir(parents=True, exist_ok=True)
    master_path = pack_dir / "composable_underlying_tasks_master_v0_3_2.csv"
    write_csv(master_path, final_master)

    review_rows: list[dict[str, Any]] = []
    for index, row in enumerate(final_master, start=1):
        review = {
            "review_item_id": f"COMPOSABLE-PAIRED-REVIEW-V0.3.2-{index:04d}",
            **row,
            "prior_review_content_hash": text(old_by_source.get(row["source_task_id"], {}).get("review_content_hash")),
            "prior_review_credit_status": "invalidated_by_dependency_extractor_patch" if row["source_task_id"] in old_by_source else "new_corrected_strong_candidate",
        }
        for field in prep.REVIEW_HUMAN_FIELDS:
            review[field] = ""
        review_rows.append(review)
    review_path = pack_dir / "composable_paired_task_review_items_v0_3_2.csv"
    write_csv(review_path, review_rows)

    service_rows, api_rows = prep.build_provisional_rows(final_master)
    for index, row in enumerate(service_rows, start=1):
        source = final_master[index - 1]
        row["benchmark_task_id"] = f"CSD-V0.3.2-{index:04d}"
        row.update({field: source.get(field, "") for field in NEW_EDGE_FIELDS})
        row.update({"candidate_order_seed": ORDER_SEED, "pack_version": VERSION, "pack_frozen_at": frozen_at, "pack_status": "CORRECTED_CONSOLIDATED_REVIEW_PACK_READY"})
    for index, row in enumerate(api_rows, start=1):
        source = final_master[index - 1]
        row["benchmark_task_id"] = f"CAR-V0.3.2-{index:04d}"
        row.update({field: source.get(field, "") for field in NEW_EDGE_FIELDS})
        row.update({"candidate_order_seed": ORDER_SEED, "pack_version": VERSION, "pack_frozen_at": frozen_at, "pack_status": "CORRECTED_CONSOLIDATED_REVIEW_PACK_READY"})
    write_csv(pack_dir / "composable_service_discovery_provisional_rows_v0_3_2.csv", service_rows)
    write_csv(pack_dir / "composable_api_recommendation_provisional_rows_v0_3_2.csv", api_rows)
    write_csv(pack_dir / "v0_3_2_candidate_selection_trace.csv", selection_trace)

    final_source_ids = {row["source_task_id"] for row in final_master}
    metrics = {
        "final_review_pack_rows": len(review_rows),
        "final_unique_underlying_tasks": len({row["underlying_task_id"] for row in final_master}),
        "final_query_nonempty_count": sum(bool(text(row.get("query_text"))) for row in final_master),
        "final_dependency_evidence_nonempty_count": sum(bool(parse_json(row.get("dependency_evidence_json"), {})) for row in final_master),
        "final_service_candidate_valid_count": sum(text(row.get("service_candidate_space_status")) == "valid" for row in final_master),
        "final_api_candidate_valid_count": sum(text(row.get("api_candidate_space_status")) == "valid" for row in final_master),
        "old_review_pack_retained_count": len(final_source_ids & old_source_ids),
        "old_review_pack_replaced_count": len(old_source_ids - final_source_ids),
        "catalog_unique_service_count": catalog_stats["service_count"],
        "catalog_unique_api_count": catalog_stats["api_count"],
        "pack_frozen_at": frozen_at,
        "target_rows": target_rows,
    }
    return review_rows, metrics, selection_trace


def find_reviewed_v031(root: Path) -> list[Path]:
    results = []
    for path in (root / "outputs").rglob("composable_paired_task_review_items_v0_3_1*.csv"):
        if "reviewed" in path.name.casefold() and path.is_file():
            results.append(path)
    return sorted(results)


def migration_manifest(
    old_rows: list[dict[str, str]],
    new_rows: list[dict[str, Any]],
    reviewed_files: list[Path],
    pack_dir: Path,
) -> list[dict[str, Any]]:
    reviewed_by_source: dict[str, dict[str, str]] = {}
    for path in reviewed_files:
        for row in read_csv(path):
            if any(text(row.get(field)) for field in prep.REVIEW_HUMAN_FIELDS):
                reviewed_by_source[text(row.get("source_task_id"))] = row
    old_by_source = {row["source_task_id"]: row for row in old_rows}
    new_by_source = {row["source_task_id"]: row for row in new_rows}
    rows: list[dict[str, Any]] = []
    for source_id in sorted(set(old_by_source) | set(new_by_source)):
        old = old_by_source.get(source_id)
        new = new_by_source.get(source_id)
        reviewed = reviewed_by_source.get(source_id)
        exact = bool(old and new) and all(text(old.get(field)) == text(new.get(field)) for field in [
            "underlying_task_id", "query_text", "candidate_services_json", "provisional_gold_services_json",
            "candidate_apis_json", "provisional_gold_apis_json", "dependency_edges_json",
            "dependency_evidence_json", "review_content_hash",
        ])
        if reviewed and exact:
            status = "eligible_for_explicit_manual_migration"
        elif reviewed:
            status = "invalidated_by_dependency_extractor_patch"
        elif old and new and text(old.get("review_content_hash")) != text(new.get("review_content_hash")):
            status = "not_reviewed_content_changed"
        elif old and not new:
            status = "invalidated_by_dependency_extractor_patch"
        else:
            status = "no_prior_human_review"
        rows.append({
            "source_task_id": source_id,
            "old_underlying_task_id": text(old.get("underlying_task_id")) if old else "",
            "new_underlying_task_id": text(new.get("underlying_task_id")) if new else "",
            "old_review_content_hash": text(old.get("review_content_hash")) if old else "",
            "new_review_content_hash": text(new.get("review_content_hash")) if new else "",
            "reviewed_v0_3_1_found": bool_text(bool(reviewed)),
            "all_migration_identity_conditions_match": bool_text(exact),
            "review_credit_status": status,
            "human_labels_copied": "false",
        })
    write_csv(pack_dir / "v0_3_1_to_v0_3_2_review_migration_manifest.csv", rows)
    return rows


def build_double_subset(review_rows: list[dict[str, Any]], size: int = 40) -> list[dict[str, Any]]:
    def stratum(row: dict[str, Any]) -> str:
        types = parse_json(row.get("dependency_type_distribution_json"), {})
        dominant = sorted(types.items(), key=lambda item: (-int(item[1]), item[0]))[0][0] if types else "unknown"
        service_bucket = "s1" if int_value(row.get("gold_service_count")) <= 1 else ("s2" if int_value(row.get("gold_service_count")) == 2 else "s3plus")
        api_bucket = "a2" if int_value(row.get("gold_api_count")) <= 2 else ("a3" if int_value(row.get("gold_api_count")) == 3 else "a4plus")
        return f"{text(row.get('catalog_domain_signature')) or 'unknown'}|{dominant}|{service_bucket}|{api_bucket}"
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in review_rows:
        buckets[stratum(row)].append(row)
    for key in buckets:
        buckets[key].sort(key=lambda row: hashlib.sha256(f"{DOUBLE_SEED}|{row['source_task_id']}".encode("utf-8")).hexdigest())
    selected: list[dict[str, Any]] = []
    keys = sorted(buckets)
    while len(selected) < min(size, len(review_rows)) and any(buckets.values()):
        for key in keys:
            if buckets[key] and len(selected) < size:
                item = dict(buckets[key].pop(0))
                item["double_annotation_stratum"] = key
                item["double_annotation_seed"] = DOUBLE_SEED
                for field in prep.REVIEW_HUMAN_FIELDS:
                    item[f"reviewer_a_{field}"] = ""
                    item[f"reviewer_b_{field}"] = ""
                selected.append(item)
    return selected


def validate_final_pack(review_rows: list[dict[str, Any]], patch_dir: Path) -> tuple[dict[str, Any], list[dict[str, str]]]:
    issues: list[dict[str, str]] = []
    forbidden_counts = Counter()
    for row in review_rows:
        edges = parse_json(row.get("dependency_edges_json"), [])
        for edge in edges:
            if not truthy(edge.get("strong_edge_eligible")):
                forbidden_counts["noneligible_marked_strong"] += 1
            if text(edge.get("upstream_field_role")) in {"argument", "input", "request"}:
                forbidden_counts[f"upstream_{text(edge.get('upstream_field_role'))}"] += 1
            if text(edge.get("edge_source_type")) == "shared_input_only":
                forbidden_counts["shared_input"] += 1
            if text(edge.get("edge_source_type")) == "query_known_value_reuse" or truthy(edge.get("value_present_in_original_query")):
                forbidden_counts["query_known"] += 1
            if text(edge.get("edge_source_type")) == "echoed_upstream_input" or truthy(edge.get("upstream_output_is_echo")):
                forbidden_counts["echoed_input"] += 1
            if text(edge.get("upstream_call_execution_status")) in extractor.FAILED_STATUSES or text(edge.get("downstream_call_execution_status")) in extractor.FAILED_STATUSES:
                forbidden_counts["failed_call"] += 1
        valid, row_issues = repair.row_structurally_valid(row)
        if not valid:
            issues.extend({"review_item_id": row["review_item_id"], "source_task_id": row["source_task_id"], "issue": issue} for issue in row_issues)
        if any(text(row.get(field)) for field in prep.REVIEW_HUMAN_FIELDS):
            issues.append({"review_item_id": row["review_item_id"], "source_task_id": row["source_task_id"], "issue": "human_field_prefilled"})
        if not edges:
            issues.append({"review_item_id": row["review_item_id"], "source_task_id": row["source_task_id"], "issue": "empty_dependency_evidence"})
        if prep.review_hash(row) != text(row.get("review_content_hash")):
            issues.append({"review_item_id": row["review_item_id"], "source_task_id": row["source_task_id"], "issue": "review_hash_mismatch"})
    duplicate_underlying = len(review_rows) - len({text(row.get("underlying_task_id")) for row in review_rows})
    metrics = {
        "final_review_pack_rows": len(review_rows),
        "final_unique_underlying_tasks": len({text(row.get("underlying_task_id")) for row in review_rows}),
        "final_query_nonempty_count": sum(bool(text(row.get("query_text"))) for row in review_rows),
        "final_dependency_evidence_nonempty_count": sum(bool(parse_json(row.get("dependency_edges_json"), [])) for row in review_rows),
        "final_service_candidate_valid_count": sum(text(row.get("service_candidate_space_status")) == "valid" for row in review_rows),
        "final_api_candidate_valid_count": sum(text(row.get("api_candidate_space_status")) == "valid" for row in review_rows),
        "strong_edges_from_upstream_arguments_count": forbidden_counts["upstream_argument"],
        "strong_edges_from_upstream_inputs_count": forbidden_counts["upstream_input"],
        "strong_edges_from_upstream_requests_count": forbidden_counts["upstream_request"],
        "strong_shared_input_edges_count": forbidden_counts["shared_input"],
        "strong_query_known_edges_count": forbidden_counts["query_known"],
        "strong_echoed_input_edges_count": forbidden_counts["echoed_input"],
        "strong_failed_call_edges_count": forbidden_counts["failed_call"],
        "forbidden_strong_edge_count": sum(forbidden_counts.values()),
        "empty_query_count": sum(not text(row.get("query_text")) for row in review_rows),
        "empty_dependency_evidence_count": sum(not parse_json(row.get("dependency_edges_json"), []) for row in review_rows),
        "duplicate_underlying_task_count": duplicate_underlying,
        "human_review_fields_autofilled_count": sum(any(text(row.get(field)) for field in prep.REVIEW_HUMAN_FIELDS) for row in review_rows),
        "fatal_issue_count": len(issues),
    }
    write_json(patch_dir / "final_pack_regression_summary.json", metrics)
    write_csv(patch_dir / "final_pack_validation_issues.csv", issues, ["review_item_id", "source_task_id", "issue"])
    return metrics, issues


def update_master_plan(path: Path, generated_at: str, summary: dict[str, Any]) -> Path:
    backup = path.with_name(path.name + ".pre_v0_3_2_backup")
    if not backup.exists():
        shutil.copy2(path, backup)
    content = path.read_text(encoding="utf-8-sig")
    pattern = r"<!-- BEGIN GATE4 V0\.3\.1 VARIABLE STATUS -->.*?<!-- END GATE4 V0\.3\.1 VARIABLE STATUS -->"
    block = f"""<!-- BEGIN GATE4 V0.3.2 VARIABLE STATUS -->

### v0.3.2 variable status ({generated_at})

- Gate 4 status: `CORRECTED_CONSOLIDATED_REVIEW_PACK_READY`;
- patch transition: `DEPENDENCY_EXTRACTOR_PATCH_REQUIRED -> CORRECTED_CONSOLIDATED_REVIEW_PACK_READY`;
- confirmed bug: `shared_input_was_eligible_as_dependency`;
- corrected strong candidate count: `{summary['corrected_strong_candidate_count']}`;
- v0.3.1 review pack: `SUPERSEDED_PENDING_V0_3_2`;
- authoritative review pack: `composable_paired_task_review_items_v0_3_2.csv`;
- human-confirmed composable count: `0`;
- stopping condition: `both-level eligible underlying tasks >= 100`;
- current next action: `review only the corrected v0.3.2 pack`;
- human-final authority and all frozen benchmark-only constraints remain unchanged.

<!-- END GATE4 V0.3.2 VARIABLE STATUS -->"""
    if re.search(pattern, content, flags=re.DOTALL):
        content = re.sub(pattern, block, content, count=1, flags=re.DOTALL)
    elif "<!-- BEGIN GATE4 V0.3.2 VARIABLE STATUS -->" not in content:
        raise RuntimeError("Gate 4 v0.3.1 variable-status markers not found")
    gate_start = content.find("## Gate 4")
    gate_end = content.find("## Gate 5", gate_start)
    if gate_start >= 0 and gate_end > gate_start:
        section = content[gate_start:gate_end]
        section = re.sub(r"(状态：)`[^`]+`", r"\1`CORRECTED_CONSOLIDATED_REVIEW_PACK_READY`", section, count=1)
        content = content[:gate_start] + section + content[gate_end:]
    changelog = f"""

## v1.4 - {generated_at[:10]}

- Gate 4 entered `DEPENDENCY_EXTRACTOR_PATCH_REQUIRED` after confirming that shared inputs and echoed request values could be promoted as dependency evidence.
- Added the generalized v0.3.2 role-aware extractor; no task ID was hard-coded and no raw ToolBench corpus rescan was performed.
- Re-evaluated existing normalized traces, retained `{summary['old_strong_retained_count']}` of 816 legacy strong candidates, and produced `{summary['corrected_strong_candidate_count']}` corrected strong candidates.
- Marked v0.3.1 superseded for review and designated v0.3.2 as the authoritative corrected review pack; human-confirmed count remains 0.
- Benchmark-only scope, six-task requirement, API-level requirement, human-final authority, no novel-method rule, and stopping condition `both-level eligible underlying tasks >= 100` remain unchanged.
"""
    if "## v1.4 - " not in content:
        content = content.rstrip() + changelog + "\n"
    path.write_text(content, encoding="utf-8")
    return backup


def write_go_no_go(path: Path, summary: dict[str, Any], generated_at: str) -> None:
    can_resume = summary["can_resume_composable_human_review"]
    lines = [
        "# Composable Dependency Extractor Patch Go / No-Go v0.3.2", "",
        f"Generated at: `{generated_at}`", "",
        "## Fixed Decision Fields", "",
        "- confirmed_bug = `shared_input_was_eligible_as_dependency`",
        f"- extractor_patch_tests_pass = `{str(summary['extractor_patch_pass']).lower()}`",
        f"- old_strong_candidate_count = `{summary['old_strong_candidate_count']}`",
        f"- corrected_strong_candidate_count = `{summary['corrected_strong_candidate_count']}`",
        f"- old_strong_retained_count = `{summary['old_strong_retained_count']}`",
        f"- old_strong_downgraded_count = `{summary['old_strong_downgraded_count']}`",
        f"- shared_input_false_positive_count = `{summary['shared_input_false_positive_count']}`",
        f"- query_known_false_positive_count = `{summary['query_known_false_positive_count']}`",
        f"- echoed_input_false_positive_count = `{summary['echoed_input_false_positive_count']}`",
        f"- failed_call_false_positive_count = `{summary['failed_call_false_positive_count']}`",
        f"- old_review_pack_rows = `{summary['old_review_pack_rows']}`",
        f"- old_review_pack_retained_count = `{summary['old_review_pack_retained_count']}`",
        f"- old_review_pack_replaced_count = `{summary['old_review_pack_replaced_count']}`",
        f"- final_review_pack_rows = `{summary['final_review_pack_rows']}`",
        f"- final_unique_underlying_tasks = `{summary['final_unique_underlying_tasks']}`",
        f"- final_service_candidate_valid_count = `{summary['final_service_candidate_valid_count']}`",
        f"- final_api_candidate_valid_count = `{summary['final_api_candidate_valid_count']}`",
        f"- final_query_nonempty_count = `{summary['final_query_nonempty_count']}`",
        f"- final_dependency_evidence_nonempty_count = `{summary['final_dependency_evidence_nonempty_count']}`",
        f"- forbidden_strong_edge_count = `{summary['forbidden_strong_edge_count']}`",
        "- human_confirmed_composable_count = `0`", "",
        "## Decision", "",
        f"- can_resume_composable_human_review = `{str(can_resume).lower()}`",
        "- can_claim_composable_service_benchmark_now = `false`",
        "- can_claim_composable_api_benchmark_now = `false`",
        "- can_start_full_six_task_assembly = `false`",
        "- can_generate_final_dataset = `false`",
        f"- recommended_next_step = `{summary['recommended_next_step']}`", "",
        "v0.3.1 was retained unchanged and is superseded for future review. No human field was automatically filled.",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def archive_outputs(archive_dir: Path, paths: Iterable[Path]) -> None:
    archive_dir.mkdir(parents=True, exist_ok=True)
    for source in paths:
        if source.exists() and source.is_file():
            shutil.copy2(source, archive_dir / source.name)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply the v0.3.2 role-aware composable dependency patch.")
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--patch-dir", type=Path, default=DEFAULT_PATCH_DIR)
    parser.add_argument("--pack-dir", type=Path, default=DEFAULT_PACK_DIR)
    parser.add_argument("--archive-dir", type=Path, default=DEFAULT_ARCHIVE_DIR)
    parser.add_argument("--target-rows", type=int, default=200)
    parser.add_argument("--common-value-threshold", type=int, default=10)
    parser.add_argument("--replacement-scan-limit", type=int, default=5000)
    parser.add_argument("--skip-archive", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.project_root.resolve()
    patch_dir = (root / args.patch_dir).resolve()
    pack_dir = (root / args.pack_dir).resolve()
    archive_dir = (root / args.archive_dir).resolve()
    paths = resolve_inputs(root, patch_dir)
    generated_at = now_iso()
    before_hashes = {
        "v0_3_1_review_pack": sha256(paths["old_review_pack"]),
        "master_plan": sha256(paths["master_plan"]),
        "legacy_extractor": sha256(root / "scripts/validation/extract_toolbench_composable_objective_evidence_v0_1.py"),
        "legacy_corpus_runner": sha256(root / "scripts/validation/run_composable_corpus_mining_v0_2.py"),
        "new_extractor_before_run": sha256(paths["extractor"]),
        "new_runner_before_run": sha256(Path(__file__).resolve()),
    }
    test_summary = run_tests(root, paths["unit_tests"], patch_dir)
    legacy_ranked_rows = read_csv(paths["legacy_ranked"])
    old_review_rows = read_csv(paths["old_review_pack"])
    if len(old_review_rows) != 200:
        raise ValueError(f"Expected v0.3.1 review pack rows 200, found {len(old_review_rows)}")

    corrected_ranked, corpus_stats, old_trace_summary = corpus_reevaluation(
        paths["normalized"], legacy_ranked_rows, patch_dir, args.common_value_threshold
    )
    comparison = comparison_outputs(legacy_ranked_rows, corrected_ranked, old_trace_summary, patch_dir)
    argument_frequency = extractor.collect_argument_frequency(iter_jsonl(paths["normalized"]))
    _, old_assessments, old_pack_audit = audit_old_pack(
        old_review_rows, argument_frequency, args.common_value_threshold, patch_dir
    )
    review_rows, pack_metrics, selection_trace = build_v032_pack(
        root, paths, corrected_ranked, old_review_rows, old_assessments,
        argument_frequency, args.common_value_threshold, pack_dir,
        args.target_rows, args.replacement_scan_limit,
    )
    reviewed_files = find_reviewed_v031(root)
    migration_rows = migration_manifest(old_review_rows, review_rows, reviewed_files, pack_dir)
    double_rows = build_double_subset(review_rows, 40)
    double_path = pack_dir / "composable_double_annotation_subset_40_v0_3_2.csv"
    write_csv(double_path, double_rows)
    (pack_dir / "v0_3_1_double_annotation_subset_superseded_notice.md").write_text(
        "# Superseded subset notice\n\nThe v0.3.1 subset is retained for provenance. Use `composable_double_annotation_subset_40_v0_3_2.csv`.\n",
        encoding="utf-8",
    )
    regression, issues = validate_final_pack(review_rows, patch_dir)
    old_pack_hash_after = sha256(paths["old_review_pack"])
    if old_pack_hash_after != before_hashes["v0_3_1_review_pack"]:
        raise RuntimeError("v0.3.1 review pack changed unexpectedly")

    summary = {
        "generated_at": generated_at,
        "extractor_patch_pass": test_summary["tests_failed"] == 0 and regression["forbidden_strong_edge_count"] == 0 and not issues,
        **test_summary,
        **corpus_stats,
        **comparison,
        "old_review_pack_rows": len(old_review_rows),
        **old_pack_audit,
        **pack_metrics,
        **regression,
        "double_annotation_subset_rows": len(double_rows),
        "reviewed_v0_3_1_files_found": [str(path) for path in reviewed_files],
        "review_migration_manifest_rows": len(migration_rows),
        "human_confirmed_composable_count": 0,
        "human_review_fields_autofilled_count": regression["human_review_fields_autofilled_count"],
        "v0_3_1_overwritten": False,
        "v0_3_2_review_app_generated": False,
        "can_claim_composable_service_benchmark_now": False,
        "can_claim_composable_api_benchmark_now": False,
        "can_start_full_six_task_assembly": False,
        "can_generate_final_dataset": False,
    }
    structural = (
        regression["forbidden_strong_edge_count"] == 0
        and regression["fatal_issue_count"] == 0
        and regression["human_review_fields_autofilled_count"] == 0
        and regression["final_review_pack_rows"] >= 100
        and regression["final_review_pack_rows"] == regression["final_unique_underlying_tasks"]
        and regression["final_review_pack_rows"] == regression["final_query_nonempty_count"]
        and regression["final_review_pack_rows"] == regression["final_dependency_evidence_nonempty_count"]
        and regression["final_review_pack_rows"] == regression["final_service_candidate_valid_count"]
        and regression["final_review_pack_rows"] == regression["final_api_candidate_valid_count"]
    )
    summary["can_resume_composable_human_review"] = bool(test_summary["tests_failed"] == 0 and structural)
    summary["recommended_next_step"] = (
        "review only the corrected composable_paired_task_review_items_v0_3_2.csv; do not continue reviewing v0.3.1."
        if summary["can_resume_composable_human_review"] else
        "report evidence shortage; do not relax dependency definition; consider StableToolBench trace/schema-grounded evidence as a bounded branch."
    )
    summary["extractor_patch_pass"] = summary["can_resume_composable_human_review"]

    mining_summary_path = patch_dir / "corrected_dependency_mining_summary.json"
    write_json(mining_summary_path, summary)
    before_hashes["master_plan_pre_patch_backup"] = before_hashes["master_plan"]
    backup = update_master_plan(paths["master_plan"], generated_at, summary)
    after_hashes = {
        "v0_3_1_review_pack": sha256(paths["old_review_pack"]),
        "master_plan": sha256(paths["master_plan"]),
        "master_plan_backup": sha256(backup),
        "new_extractor": sha256(paths["extractor"]),
        "new_runner": sha256(Path(__file__).resolve()),
        "unit_tests": sha256(paths["unit_tests"]),
    }
    hash_path = patch_dir / "before_after_sha256_v0_3_2.json"
    write_json(hash_path, {"generated_at": generated_at, "before": before_hashes, "after": after_hashes})

    go_no_go = root / "docs/phase1/composable_dependency_extractor_patch_go_no_go_v0_3_2.md"
    write_go_no_go(go_no_go, summary, generated_at)
    change_log = root / "docs/phase1/composable_dependency_extractor_patch_master_plan_change_log_v0_3_2.md"
    change_log.write_text(
        "# Composable v0.3.2 Master Plan Change Log\n\n"
        f"Generated at: `{generated_at}`\n\n"
        "- Gate 4 moved through `DEPENDENCY_EXTRACTOR_PATCH_REQUIRED` to `CORRECTED_CONSOLIDATED_REVIEW_PACK_READY`.\n"
        f"- Corrected strong candidates: `{summary['corrected_strong_candidate_count']}`.\n"
        "- v0.3.1 is superseded for review but retained unchanged.\n"
        "- v0.3.2 is the authoritative corrected review pack.\n"
        "- Human-confirmed count remains `0`; the stopping condition remains `both-level eligible underlying tasks >= 100`.\n"
        "- No frozen scope, six-task, API-level, human-final, or no-novel-method rule changed.\n",
        encoding="utf-8",
    )

    if not args.skip_archive:
        archive_outputs(archive_dir, [
            paths["inventory"], hash_path, paths["extractor"], Path(__file__).resolve(), paths["unit_tests"],
            patch_dir / "composable_dependency_extractor_test_results_v0_3_2.txt",
            patch_dir / "corrected_dependency_edge_candidates.jsonl",
            patch_dir / "corrected_dependency_evidence_status.csv",
            patch_dir / "corrected_underlying_task_candidates_ranked.csv",
            patch_dir / "downgraded_old_strong_candidates.csv",
            patch_dir / "newly_promoted_strong_candidates.csv",
            patch_dir / "current_v0_3_1_pack_dependency_reaudit.csv",
            patch_dir / "current_v0_3_1_pack_false_strong_rows.csv",
            patch_dir / "current_v0_3_1_pack_reaudit_summary.json",
            patch_dir / "final_pack_regression_summary.json",
            patch_dir / "final_pack_validation_issues.csv",
            mining_summary_path,
            pack_dir / "composable_paired_task_review_items_v0_3_2.csv",
            pack_dir / "composable_underlying_tasks_master_v0_3_2.csv",
            pack_dir / "composable_service_discovery_provisional_rows_v0_3_2.csv",
            pack_dir / "composable_api_recommendation_provisional_rows_v0_3_2.csv",
            pack_dir / "v0_3_1_to_v0_3_2_review_migration_manifest.csv",
            double_path, go_no_go, change_log, paths["master_plan"], backup,
        ])

    terminal_fields = [
        "extractor_patch_pass", "tests_run", "tests_passed", "tests_failed",
        "old_strong_candidate_count", "corrected_strong_candidate_count",
        "old_strong_retained_count", "old_strong_downgraded_count",
        "shared_input_false_positive_count", "query_known_false_positive_count",
        "echoed_input_false_positive_count", "failed_call_false_positive_count",
        "old_review_pack_rows", "old_review_pack_retained_count", "old_review_pack_replaced_count",
        "final_review_pack_rows", "final_unique_underlying_tasks", "final_query_nonempty_count",
        "final_dependency_evidence_nonempty_count", "final_service_candidate_valid_count",
        "final_api_candidate_valid_count", "strong_edges_from_upstream_arguments_count",
        "strong_shared_input_edges_count", "strong_query_known_edges_count",
        "strong_echoed_input_edges_count", "strong_failed_call_edges_count",
        "forbidden_strong_edge_count", "human_review_fields_autofilled_count",
    ]
    for field in terminal_fields:
        print(f"{field}={summary[field]}")
    print("v0_3_1_overwritten=false")
    print(f"v0_3_2_review_app_generated={str(summary['v0_3_2_review_app_generated']).lower()}")
    print(f"can_resume_composable_human_review={str(summary['can_resume_composable_human_review']).lower()}")
    print("can_claim_composable_service_benchmark_now=false")
    print("can_claim_composable_api_benchmark_now=false")
    print("can_start_full_six_task_assembly=false")
    print("can_generate_final_dataset=false")
    print(f"recommended_next_step={summary['recommended_next_step']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
