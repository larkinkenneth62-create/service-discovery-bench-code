#!/usr/bin/env python
"""Extract objective ToolBench dependency evidence from normalized trace steps.

The extractor uses exact scalar reuse from an earlier output/observation into a
later argument. Query-known constants, trivial constants, repeated templates,
and credential-like values are filtered. Results describe evidence availability
only and never become final human composable labels.
"""

from __future__ import annotations

import argparse
import ast
import csv
import json
import re
import shutil
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator


EVIDENCE_STATUS_FIELDS = [
    "inventory_row_id",
    "source_dataset",
    "source_group",
    "source_task_id",
    "task_id",
    "source_query_id",
    "current_policy_status",
    "evidence_availability_status",
    "joined",
    "ordered_steps_found",
    "step_count",
    "distinct_service_count",
    "distinct_api_count",
    "arguments_found",
    "outputs_found",
    "observations_found",
    "non_query_known_dependency_edge_count",
    "query_known_edge_count",
    "evidence_source_files_json",
    "evidence_notes",
]

STOP_VALUES = {
    "true",
    "false",
    "null",
    "none",
    "yes",
    "no",
    "ok",
    "success",
    "error",
    "result",
    "response",
    "data",
    "get",
    "post",
    "put",
    "delete",
    "en",
    "us",
}

SECRET_PATH_TERMS = {"api_key", "apikey", "token", "secret", "credential", "password", "authorization", "access_key"}
ENTITY_PATH_TERMS = {
    "id",
    "name",
    "url",
    "uri",
    "address",
    "date",
    "time",
    "latitude",
    "longitude",
    "lat",
    "lon",
    "lng",
    "coordinate",
    "coordinates",
    "location",
    "city",
    "country",
    "title",
    "symbol",
    "code",
}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def truthy(value: Any) -> bool:
    return text(value).casefold() in {"1", "true", "yes"}


def normalize_scalar(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    normalized = unicodedata.normalize("NFKC", text(value)).casefold()
    return " ".join(normalized.split())


def path_has_secret_term(path: str) -> bool:
    lowered = path.casefold()
    return any(term in lowered for term in SECRET_PATH_TERMS)


def allowed_scalar(path: str, value: Any) -> bool:
    if path_has_secret_term(path) or isinstance(value, bool) or value is None:
        return False
    normalized = normalize_scalar(value)
    if not normalized or normalized in STOP_VALUES or normalized in {"0", "1", "0.0", "1.0"}:
        return False
    if isinstance(value, (int, float)):
        return True
    if len(normalized) < 4:
        return False
    if re.fullmatch(r"[\W_]+", normalized):
        return False
    return True


def decode_embedded_structure(value: Any) -> Any:
    """Safely decode JSON/Python-literal response strings without executing code."""
    if not isinstance(value, str):
        return value
    raw = value.strip()
    if not raw or len(raw) > 2_000_000:
        return value
    if not ((raw.startswith("{") and raw.endswith("}")) or (raw.startswith("[") and raw.endswith("]"))):
        return value
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        try:
            parsed = ast.literal_eval(raw)
        except (ValueError, SyntaxError, MemoryError, RecursionError):
            return value
        return parsed if isinstance(parsed, (dict, list, tuple, str, int, float, bool, type(None))) else value


def iter_scalars(value: Any, path: str) -> Iterator[tuple[str, Any]]:
    decoded = decode_embedded_structure(value)
    if decoded is not value:
        yield from iter_scalars(decoded, path + ".decoded")
        return
    if isinstance(value, dict):
        for key, child in value.items():
            yield from iter_scalars(child, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            yield from iter_scalars(child, f"{path}[{index}]")
    elif allowed_scalar(path, value):
        yield path, value


def dependency_type_for_paths(upstream_path: str, downstream_path: str) -> str:
    tokens = set(re.findall(r"[a-z0-9_]+", f"{upstream_path} {downstream_path}".casefold()))
    return "entity_flow" if tokens & ENTITY_PATH_TERMS else "data_flow"


def value_present_in_query(value: Any, query: str) -> bool:
    normalized_value = normalize_scalar(value)
    normalized_query = normalize_scalar(query)
    return bool(normalized_value and normalized_value in normalized_query)


def collect_output_frequency(records: Iterable[dict[str, Any]]) -> Counter[str]:
    frequency: Counter[str] = Counter()
    for record in records:
        task_values: set[str] = set()
        for step_index, step in enumerate(record.get("steps", []), start=1):
            if not isinstance(step, dict):
                continue
            for path, value in iter_scalars(step.get("outputs"), f"steps[{step_index}].outputs"):
                normalized = normalize_scalar(value)
                if normalized:
                    task_values.add(normalized)
            for path, value in iter_scalars(step.get("observation"), f"steps[{step_index}].observation"):
                normalized = normalize_scalar(value)
                if normalized:
                    task_values.add(normalized)
        frequency.update(task_values)
    return frequency


def extract_dependency_edges(
    record: dict[str, Any],
    *,
    common_value_frequency: Counter[str] | None = None,
    common_value_threshold: int = 10,
) -> list[dict[str, Any]]:
    """Extract exact earlier-output to later-input candidate edges."""
    steps = [step for step in record.get("steps", []) if isinstance(step, dict)]
    query = text(record.get("query_text"))
    task_id = text(record.get("source_task_id"))
    edges: list[dict[str, Any]] = []
    for later_index in range(1, len(steps)):
        downstream_values = list(iter_scalars(steps[later_index].get("arguments"), f"steps[{later_index + 1}].arguments"))
        pair_nonfiltered = False
        for earlier_index in range(later_index):
            upstream_output = steps[earlier_index].get("outputs")
            upstream_values = list(iter_scalars(upstream_output, f"steps[{earlier_index + 1}].outputs"))
            # The normalized trace stores the same function response as both
            # parsed outputs and raw observation. Fall back to observation only
            # when parsed outputs expose no usable scalar, avoiding duplicate
            # evidence edges for one physical response.
            if not upstream_values:
                upstream_values += list(iter_scalars(steps[earlier_index].get("observation"), f"steps[{earlier_index + 1}].observation"))
            seen_pairs: set[tuple[str, str, str]] = set()
            for upstream_path, upstream_value in upstream_values:
                normalized_upstream = normalize_scalar(upstream_value)
                if not normalized_upstream:
                    continue
                if common_value_frequency and common_value_frequency[normalized_upstream] >= common_value_threshold:
                    continue
                for downstream_path, downstream_value in downstream_values:
                    normalized_downstream = normalize_scalar(downstream_value)
                    if normalized_upstream != normalized_downstream:
                        continue
                    pair_key = (upstream_path, downstream_path, normalized_upstream)
                    if pair_key in seen_pairs:
                        continue
                    seen_pairs.add(pair_key)
                    query_known = value_present_in_query(upstream_value, query)
                    if not query_known:
                        pair_nonfiltered = True
                    edges.append(
                        {
                            "source_task_id": task_id,
                            "from_step": earlier_index + 1,
                            "to_step": later_index + 1,
                            "dependency_type": dependency_type_for_paths(upstream_path, downstream_path),
                            "upstream_source_path": upstream_path,
                            "downstream_source_path": downstream_path,
                            "upstream_value": text(upstream_value),
                            "downstream_value": text(downstream_value),
                            "normalized_match": normalized_upstream,
                            "value_present_in_original_query": query_known,
                            "query_known_value_filtered": query_known,
                            "source_file": text(steps[earlier_index].get("source_file")),
                            "evidence_strength": "filtered_query_known" if query_known else "exact_scalar_reuse",
                            "extraction_notes": "Exact normalized scalar match; no semantic or LLM inference.",
                        }
                    )
        if not pair_nonfiltered:
            edges.append(
                {
                    "source_task_id": task_id,
                    "from_step": later_index,
                    "to_step": later_index + 1,
                    "dependency_type": "sequence_only",
                    "upstream_source_path": text(steps[later_index - 1].get("source_json_path")),
                    "downstream_source_path": text(steps[later_index].get("source_json_path")),
                    "upstream_value": "",
                    "downstream_value": "",
                    "normalized_match": "",
                    "value_present_in_original_query": False,
                    "query_known_value_filtered": False,
                    "source_file": text(steps[later_index - 1].get("source_file")),
                    "evidence_strength": "order_only",
                    "extraction_notes": "Explicit call-array order only; not objective composable dependency evidence.",
                }
            )
    return edges


def classify_evidence(
    parse_summary: dict[str, Any],
    edges: list[dict[str, Any]],
) -> str:
    parse_status = text(parse_summary.get("parse_status"))
    if parse_status == "source_unavailable":
        return "source_unavailable"
    if parse_status == "join_ambiguous":
        return "join_ambiguous"
    if parse_status != "ok":
        return "parse_failed"
    nonfiltered = [
        edge
        for edge in edges
        if edge.get("dependency_type") not in {"sequence_only", "none"}
        and not bool(edge.get("query_known_value_filtered"))
    ]
    step_count = int(parse_summary.get("step_count") or 0)
    distinct_services = int(parse_summary.get("distinct_service_count") or 0)
    distinct_apis = int(parse_summary.get("distinct_api_count") or 0)
    if nonfiltered and step_count >= 2 and (distinct_services >= 2 or distinct_apis >= 2):
        if all(text(edge.get("upstream_source_path")) and text(edge.get("downstream_source_path")) and text(edge.get("source_file")) for edge in nonfiltered):
            return "strong_objective_evidence_available"
        return "partial_objective_evidence"
    if nonfiltered:
        return "partial_objective_evidence"
    ordered = truthy(parse_summary.get("ordered_steps_found"))
    arguments = truthy(parse_summary.get("arguments_found"))
    outputs = truthy(parse_summary.get("outputs_found")) or truthy(parse_summary.get("observations_found"))
    if ordered and not arguments and not outputs:
        return "sequence_only"
    if ordered:
        return "no_dependency_evidence"
    return "parse_failed"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, fieldnames: list[str], rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at {path}:{line_number}: {exc}") from exc
            if isinstance(record, dict):
                records.append(record)
    return records


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def format_rate(value: float) -> str:
    return f"{value:.4f}"


def determine_failure_and_decision(summary: dict[str, Any]) -> tuple[str, str]:
    if not summary["answer_path_exists"] or not summary["reproduction_data_path_exists"]:
        return "PATH_MISSING", "TOOLBENCH_EVIDENCE_PATHS_MISSING"
    if summary["answer_file_count"] == 0 and summary["reproduction_file_count"] == 0:
        return "PATH_EXISTS_BUT_EMPTY_OR_UNREADABLE", "TOOLBENCH_EVIDENCE_PATHS_PRESENT_BUT_UNREADABLE"
    if summary["strong_objective_evidence_available_count"] + summary["partial_objective_evidence_count"] > 0:
        if summary["unmatched_count"] > 0 or summary["parse_failed_count"] > 0:
            return "MIXED_CAUSES", "TOOLBENCH_USABLE_OBJECTIVE_DEPENDENCY_EVIDENCE_FOUND"
        return "OBJECTIVE_DEPENDENCY_FOUND", "TOOLBENCH_USABLE_OBJECTIVE_DEPENDENCY_EVIDENCE_FOUND"
    if summary["exact_joined_count"] == 0 or summary["join_rate"] < 0.25:
        return "JOIN_KEY_FAILURE", "TOOLBENCH_TRACE_JOIN_FAILURE"
    if summary["parse_failed_count"] > max(5, summary["exact_joined_count"] // 4):
        return "PARSER_COVERAGE_FAILURE", "TOOLBENCH_TRACE_PARSER_INCOMPLETE"
    if summary["arguments_found_count"] == 0 or summary["outputs_found_count"] == 0:
        return "SOURCE_LACKS_ARGUMENT_OUTPUT_EVIDENCE", "TOOLBENCH_SOURCE_LACKS_ARGUMENT_OUTPUT_EVIDENCE"
    return "CANDIDATES_HAVE_NO_OBJECTIVE_DEPENDENCY", "TOOLBENCH_PATHS_USABLE_BUT_CURRENT_CANDIDATES_HAVE_NO_DEPENDENCY"


def build_report(summary: dict[str, Any], stage: dict[str, Any], status_counts: Counter[str], top_parse_errors: list[tuple[str, int]]) -> str:
    worth_fixing = summary["exact_joined_count"] > 0 and (
        summary["parse_failed_count"] > 0 or summary["strong_objective_evidence_available_count"] + summary["partial_objective_evidence_count"] > 0
    )
    lines = [
        "# ToolBench Composable Path and Trace Audit v0.1",
        "",
        f"Generated at: {summary['generated_at']}",
        f"Project root: `{summary['project_root']}`",
        f"Inventory input: `{summary['inventory_path']}`",
        f"ToolBench root selected: `{summary['toolbench_root_actual_path']}`",
        "",
        "## Scope and decision boundary",
        "",
        "This is a read-only evidence-availability audit. It does not assign final composable labels, update human QA, freeze a source, assemble tasks, create a final dataset, split data, run baselines, train models, call Qwen, or access the web.",
        "",
        "## Direct answers",
        "",
        f"1. `ToolBench/data/answer` exists: **{summary['answer_path_exists']}**.",
        f"2. `ToolBench/reproduction_data` exists: **{summary['reproduction_data_path_exists']}**.",
        f"3. `data/answer`: **{summary['answer_file_count']:,} files**, **{summary['answer_total_size_bytes']:,} bytes**, formats `{json.dumps(stage.get('answer_extensions_distribution', {}), ensure_ascii=False)}`. `reproduction_data`: **{summary['reproduction_file_count']:,} files**, **{summary['reproduction_total_size_bytes']:,} bytes**, formats `{json.dumps(stage.get('reproduction_extensions_distribution', {}), ensure_ascii=False)}`.",
        f"4. Task/query IDs are available through filename stems and converted JSON object keys; the lightweight ID index contains **{stage.get('id_index_row_count', 0):,} rows**.",
        f"5. Explicitly ordered calls were found for **{summary['ordered_steps_found_count']:,} inventory rows**.",
        f"6. Call arguments were found for **{summary['arguments_found_count']:,} rows**.",
        f"7. Outputs/observations were found for **{summary['outputs_found_count']:,}/{summary['observations_found_count']:,} rows**.",
        f"8. Of **{summary['inventory_unique_count']:,} unique inventory candidates**, **{summary['exact_joined_count']:,}** exact-joined, **{summary['ambiguous_join_count']:,}** were ambiguous, and **{summary['unmatched_count']:,}** were unmatched. Overall join rate: **{summary['join_rate']:.2%}**. ToolBench-only join rate: **{summary['toolbench_exact_joined_count']:,}/{summary['toolbench_inventory_count']:,} = {summary['toolbench_join_rate']:.2%}**; the denominator of 322 also contains **{summary['stabletoolbench_out_of_scope_count']} StableToolBench rows**, which are intentionally not joined to ToolBench files.",
        f"9. Evidence availability: strong **{summary['strong_objective_evidence_available_count']}**, partial **{summary['partial_objective_evidence_count']}**, sequence-only **{summary['sequence_only_count']}**, no dependency **{summary['no_dependency_evidence_count']}**, source unavailable **{summary['source_unavailable_count']}**, parse failed **{summary['parse_failed_count']}**.",
        f"10. Primary failure mode: **{summary['primary_failure_mode']}**.",
        f"11. Final diagnostic decision: **{summary['final_decision']}**. This is an evidence-path decision, not a human composable label.",
        f"12. Continue fixing the extractor: **{'yes, targeted fixes are evidence-supported' if worth_fixing else 'no broad parser rewrite is justified by this audit'}**.",
        "",
        "## Join and parser interpretation",
        "",
        "- Joins use exact source IDs only. No embeddings, fuzzy matching, query similarity, LLMs, or task-ID label hard-coding are used.",
        "- `data/answer` is treated as the authoritative first-priority evidence class. Converted model predictions are considered only when no answer record matches.",
        "- Multiple exact records within the same highest-priority source class are marked `join_ambiguous`; none is selected automatically.",
        "- Message-array order is accepted as call order. Query wording, candidate order, and JSON dictionary traversal order are never used to infer execution order.",
        "",
        "## Dependency extraction",
        "",
        f"- Non-query-known objective dependency edges: **{summary['candidate_dependency_edges_extracted']:,}**.",
        f"- Query-known reuse edges filtered: **{summary['query_known_edges_filtered']:,}**.",
        "- Exact scalar reuse is required. Common constants, short values, credential-like fields, and values already stated in the query are excluded from strong evidence.",
        "- Evidence status is not a final human composable decision. Any strong/partial row still requires human dependency confirmation.",
        "",
        "## Why the earlier inventory reported strong = 0",
        "",
        "The earlier inventory inspected 11 converted G3 aggregate prediction files and did not inspect the per-task `data/answer` records. Exact candidate IDs were mostly absent from those converted aggregates. This audit reached `data/answer`, selected the final explicit prefix chain by exact final-answer/finish-type agreement, and recovered ordered calls plus arguments and outputs. Therefore the earlier zero was primarily a path-coverage and parser-coverage result, not proof that the local repository lacked execution evidence.",
        "",
        f"The current audit still has mixed limitations: **{summary['toolbench_unmatched_count']} ToolBench rows** remain unmatched, **{summary['stabletoolbench_out_of_scope_count']} StableToolBench rows** are outside this source join, **{summary['ambiguous_join_count']} row** is ambiguous, and **{summary['parse_failed_count']} row** lacks a usable ordered call sequence. Model-generated trajectories are not automatically treated as benchmark gold.",
        "",
        "## Evidence status distribution",
        "",
    ]
    for status in [
        "strong_objective_evidence_available",
        "partial_objective_evidence",
        "sequence_only",
        "no_dependency_evidence",
        "source_unavailable",
        "join_ambiguous",
        "parse_failed",
    ]:
        lines.append(f"- `{status}`: {status_counts.get(status, 0)}")
    lines.extend(["", "## Parse-error profile", ""])
    if top_parse_errors:
        for error, count in top_parse_errors:
            lines.append(f"- `{error}`: {count}")
    else:
        lines.append("- No parser errors recorded.")
    lines.extend(
        [
            "",
            "## Fixed outputs",
            "",
            f"- `primary_failure_mode = {summary['primary_failure_mode']}`",
            f"- `final_decision = {summary['final_decision']}`",
            "- `can_claim_confirmed_composable_now = false`",
            "- `can_generate_composable_dataset_now = false`",
            f"- `can_start_human_dependency_confirmation = {str(summary['can_start_human_dependency_confirmation']).lower()}`",
            f"- `recommended_next_step = {summary['recommended_next_step']}`",
            "",
        ]
    )
    return "\n".join(lines)


def archive_outputs(project_root: Path, output_dir: Path, report_path: Path, archive_dir: Path, script_paths: list[Path]) -> list[str]:
    archive_dir.mkdir(parents=True, exist_ok=True)
    archived: list[str] = []
    for source in sorted(output_dir.iterdir(), key=lambda path: path.name.casefold()):
        if source.is_file():
            target = archive_dir / source.name
            shutil.copy2(source, target)
            archived.append(str(target))
    for source in [report_path, *script_paths]:
        if source.is_file():
            target = archive_dir / source.name
            shutil.copy2(source, target)
            archived.append(str(target))
    return archived


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract exact objective dependency evidence from normalized ToolBench traces.")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--inventory", default="outputs/composable_recovery_inventory_v0_1/candidate_inventory.csv")
    parser.add_argument("--output-dir", default="outputs/toolbench_composable_trace_audit_v0_1")
    parser.add_argument("--report", default="docs/phase1/toolbench_composable_path_and_trace_audit_v0_1.md")
    parser.add_argument("--archive-dir", default="outputs/run_archives/2026-07-13_toolbench_composable_path_and_trace_audit_v0_1")
    parser.add_argument("--skip-archive", action="store_true")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    output_dir = (project_root / args.output_dir).resolve()
    inventory_path = (project_root / args.inventory).resolve()
    report_path = (project_root / args.report).resolve()
    archive_dir = (project_root / args.archive_dir).resolve()
    required = [
        inventory_path,
        output_dir / "audit_stage_summary.json",
        output_dir / "composable_candidate_trace_join_manifest.csv",
        output_dir / "toolbench_composable_step_parse_summary.csv",
        output_dir / "toolbench_composable_normalized_steps.jsonl",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise SystemExit("Required audit-stage inputs are missing:\n- " + "\n- ".join(missing))
    csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

    stage = json.loads((output_dir / "audit_stage_summary.json").read_text(encoding="utf-8"))
    inventory_rows = read_csv(inventory_path)
    join_rows = read_csv(output_dir / "composable_candidate_trace_join_manifest.csv")
    parse_rows = read_csv(output_dir / "toolbench_composable_step_parse_summary.csv")
    normalized_records = read_jsonl(output_dir / "toolbench_composable_normalized_steps.jsonl")
    parse_by_id = {text(row.get("inventory_row_id")): row for row in parse_rows}
    normalized_by_id = {text(row.get("inventory_row_id")): row for row in normalized_records}

    frequency = collect_output_frequency(normalized_records)
    threshold = max(10, int(len(normalized_records) * 0.05))
    all_edges: list[dict[str, Any]] = []
    edges_by_inventory: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for inventory_id, record in normalized_by_id.items():
        edges = extract_dependency_edges(record, common_value_frequency=frequency, common_value_threshold=threshold)
        for edge in edges:
            edge["inventory_row_id"] = inventory_id
        all_edges.extend(edges)
        edges_by_inventory[inventory_id].extend(edges)
    write_jsonl(output_dir / "toolbench_dependency_edge_candidates.jsonl", all_edges)

    inventory_by_id = {text(row.get("inventory_id")): row for row in inventory_rows}
    evidence_rows: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    for inventory_id, inventory_row in inventory_by_id.items():
        parse_row = parse_by_id.get(inventory_id, {"parse_status": "source_unavailable"})
        edges = edges_by_inventory.get(inventory_id, [])
        status = classify_evidence(parse_row, edges)
        status_counts[status] += 1
        nonfiltered = [
            edge for edge in edges
            if edge.get("dependency_type") not in {"sequence_only", "none"}
            and not bool(edge.get("query_known_value_filtered"))
        ]
        query_known = [edge for edge in edges if bool(edge.get("query_known_value_filtered"))]
        source_files = sorted({text(edge.get("source_file")) for edge in edges if text(edge.get("source_file"))})
        evidence_rows.append(
            {
                "inventory_row_id": inventory_id,
                "source_dataset": text(inventory_row.get("source_dataset")),
                "source_group": text(inventory_row.get("source_group")),
                "source_task_id": text(inventory_row.get("source_task_id")),
                "task_id": text(inventory_row.get("task_id")),
                "source_query_id": text(inventory_row.get("source_query_id")),
                "current_policy_status": text(inventory_row.get("current_policy_status")),
                "evidence_availability_status": status,
                "joined": text(parse_row.get("joined")),
                "ordered_steps_found": text(parse_row.get("ordered_steps_found")),
                "step_count": text(parse_row.get("step_count")) or 0,
                "distinct_service_count": text(parse_row.get("distinct_service_count")) or 0,
                "distinct_api_count": text(parse_row.get("distinct_api_count")) or 0,
                "arguments_found": text(parse_row.get("arguments_found")),
                "outputs_found": text(parse_row.get("outputs_found")),
                "observations_found": text(parse_row.get("observations_found")),
                "non_query_known_dependency_edge_count": len(nonfiltered),
                "query_known_edge_count": len(query_known),
                "evidence_source_files_json": json.dumps(source_files, ensure_ascii=False),
                "evidence_notes": "Evidence availability only; final human composable label is unchanged.",
            }
        )
    write_csv(output_dir / "toolbench_composable_evidence_status.csv", EVIDENCE_STATUS_FIELDS, evidence_rows)

    nonfiltered_edges = [
        edge for edge in all_edges
        if edge.get("dependency_type") not in {"sequence_only", "none"}
        and not bool(edge.get("query_known_value_filtered"))
    ]
    query_known_edges = [edge for edge in all_edges if bool(edge.get("query_known_value_filtered"))]
    toolbench_inventory_count = sum(text(row.get("source_dataset")).casefold() == "toolbench" for row in inventory_rows)
    stabletoolbench_out_of_scope_count = sum(text(row.get("source_dataset")).casefold() != "toolbench" for row in inventory_rows)
    join_by_inventory = {text(row.get("inventory_row_id")): row for row in join_rows}
    toolbench_exact_joined_count = sum(
        text(row.get("source_dataset")).casefold() == "toolbench"
        and text(join_by_inventory.get(text(row.get("inventory_id")), {}).get("join_status")) == "joined"
        for row in inventory_rows
    )
    toolbench_unmatched_count = sum(
        text(row.get("source_dataset")).casefold() == "toolbench"
        and text(join_by_inventory.get(text(row.get("inventory_id")), {}).get("join_status")) == "unmatched"
        for row in inventory_rows
    )
    summary = {
        **stage,
        "generated_at": now_iso(),
        "inventory_unique_count": stage.get("inventory_unique_count", len(inventory_rows)),
        "exact_joined_count": stage.get("exact_joined_count", 0),
        "join_rate": stage.get("join_rate", 0.0),
        "ambiguous_join_count": stage.get("ambiguous_join_count", 0),
        "unmatched_count": stage.get("unmatched_count", 0),
        "ordered_steps_found_count": stage.get("ordered_steps_found_count", 0),
        "arguments_found_count": stage.get("arguments_found_count", 0),
        "outputs_found_count": stage.get("outputs_found_count", 0),
        "observations_found_count": stage.get("observations_found_count", 0),
        "candidate_dependency_edges_extracted": len(nonfiltered_edges),
        "query_known_edges_filtered": len(query_known_edges),
        "toolbench_inventory_count": toolbench_inventory_count,
        "toolbench_exact_joined_count": toolbench_exact_joined_count,
        "toolbench_join_rate": toolbench_exact_joined_count / toolbench_inventory_count if toolbench_inventory_count else 0.0,
        "toolbench_unmatched_count": toolbench_unmatched_count,
        "stabletoolbench_out_of_scope_count": stabletoolbench_out_of_scope_count,
        "strong_objective_evidence_available_count": status_counts["strong_objective_evidence_available"],
        "partial_objective_evidence_count": status_counts["partial_objective_evidence"],
        "sequence_only_count": status_counts["sequence_only"],
        "no_dependency_evidence_count": status_counts["no_dependency_evidence"],
        "source_unavailable_count": status_counts["source_unavailable"],
        "join_ambiguous_count": status_counts["join_ambiguous"],
        "parse_failed_count": status_counts["parse_failed"],
        "evidence_status_distribution": dict(sorted(status_counts.items())),
        "automatic_composable_labels_generated": False,
        "human_review_fields_modified": False,
        "source_files_modified": False,
        "web_or_external_api_used": False,
        "can_claim_confirmed_composable_now": False,
        "can_generate_composable_dataset_now": False,
    }
    primary_failure, final_decision = determine_failure_and_decision(summary)
    summary["primary_failure_mode"] = primary_failure
    summary["final_decision"] = final_decision
    summary["can_start_human_dependency_confirmation"] = (
        summary["strong_objective_evidence_available_count"] + summary["partial_objective_evidence_count"] > 0
    )
    if summary["can_start_human_dependency_confirmation"]:
        summary["recommended_next_step"] = "Human-confirm only rows with strong or partial objective evidence; do not auto-promote them."
    elif final_decision == "TOOLBENCH_TRACE_JOIN_FAILURE":
        summary["recommended_next_step"] = "Fix exact ID mapping only, then rerun this audit."
    elif final_decision == "TOOLBENCH_TRACE_PARSER_INCOMPLETE":
        summary["recommended_next_step"] = "Fix only the unsupported trace schemas identified in parse_errors.csv, then rerun."
    elif final_decision == "TOOLBENCH_SOURCE_LACKS_ARGUMENT_OUTPUT_EVIDENCE":
        summary["recommended_next_step"] = "Do not claim execution-grounded composable from these ToolBench traces."
    else:
        summary["recommended_next_step"] = "Retain candidates as unresolved evidence inventory; do not generate a composable dataset."

    parse_error_profile = Counter(text(row.get("parse_status")) for row in parse_rows if text(row.get("parse_status")) not in {"ok", "source_unavailable", "join_ambiguous"})
    report = build_report(summary, stage, status_counts, parse_error_profile.most_common(10))
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    (output_dir / "toolbench_composable_trace_audit_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if not args.skip_archive:
        script_paths = [
            project_root / "scripts" / "validation" / "audit_toolbench_composable_trace_availability_v0_1.py",
            project_root / "scripts" / "validation" / "extract_toolbench_composable_objective_evidence_v0_1.py",
            project_root / "scripts" / "validation" / "test_toolbench_composable_trace_audit_v0_1.py",
        ]
        summary["archive_files"] = archive_outputs(project_root, output_dir, report_path, archive_dir, script_paths)
        (output_dir / "toolbench_composable_trace_audit_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        shutil.copy2(output_dir / "toolbench_composable_trace_audit_summary.json", archive_dir / "toolbench_composable_trace_audit_summary.json")

    fixed_output = {
        key: summary[key]
        for key in [
            "toolbench_root_found",
            "toolbench_root_actual_path",
            "answer_path_exists",
            "answer_path_actual",
            "answer_file_count",
            "answer_total_size_bytes",
            "reproduction_data_path_exists",
            "reproduction_data_path_actual",
            "reproduction_file_count",
            "reproduction_total_size_bytes",
            "alternate_trace_paths_found_count",
            "inventory_unique_count",
            "exact_joined_count",
            "join_rate",
            "ambiguous_join_count",
            "unmatched_count",
            "ordered_steps_found_count",
            "arguments_found_count",
            "outputs_found_count",
            "observations_found_count",
            "candidate_dependency_edges_extracted",
            "query_known_edges_filtered",
            "strong_objective_evidence_available_count",
            "partial_objective_evidence_count",
            "sequence_only_count",
            "no_dependency_evidence_count",
            "source_unavailable_count",
            "parse_failed_count",
            "primary_failure_mode",
            "final_decision",
            "can_claim_confirmed_composable_now",
            "can_generate_composable_dataset_now",
            "can_start_human_dependency_confirmation",
            "recommended_next_step",
        ]
    }
    print(json.dumps(fixed_output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
