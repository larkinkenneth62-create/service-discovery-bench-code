#!/usr/bin/env python
"""Role-aware dependency extraction for normalized ToolBench traces.

This module is deliberately conservative. It records shared inputs, query-known
values, echoed inputs, failed calls, and sequence evidence, but only marks a
genuine upstream result/observation/output to downstream input/control edge as
strong. It does not use semantic models or task-ID-specific rules.
"""

from __future__ import annotations

import ast
import json
import re
import unicodedata
from collections import Counter
from typing import Any, Iterable, Iterator


VERSION = "v0.3.2"

STRONG_EDGE_SOURCE_TYPES = {
    "upstream_output_to_downstream_input",
    "upstream_observation_to_downstream_input",
    "upstream_result_to_tool_selection",
    "upstream_result_to_branch_condition",
}
FAILED_STATUSES = {"failed", "error_only"}
VALID_UPSTREAM_ROLES = {"output", "observation", "result", "response_payload"}
VALID_DOWNSTREAM_ROLES = {"argument", "input", "request", "tool_selection", "branch_condition"}

STOP_VALUES = {
    "true", "false", "null", "none", "yes", "no", "ok", "success",
    "error", "result", "response", "data", "get", "post", "put",
    "delete", "en", "us",
}
SECRET_PATH_TERMS = {
    "api_key", "apikey", "token", "secret", "credential", "password",
    "authorization", "access_key",
}
ENTITY_PATH_TERMS = {
    "id", "name", "url", "uri", "address", "date", "time", "latitude",
    "longitude", "lat", "lon", "lng", "coordinate", "coordinates",
    "location", "city", "country", "title", "symbol", "code",
}
ERROR_PATTERNS = (
    r"invalid\s+api\s*key",
    r"unauthori[sz]ed",
    r"authentication\s+failed",
    r"forbidden",
    r"permission\s+denied",
    r"timeout(?:\s+error)?",
    r"timed\s+out",
    r"traceback",
    r"exception",
    r"application\s+error",
    r"internal\s+server\s+error",
    r"bad\s+request",
    r"not\s+found",
    r"invalid\s+date",
    r"http\s*(?:4\d\d|5\d\d)",
)
ERROR_KEY_TERMS = {"error", "errors", "exception", "traceback", "failure", "failed"}
STATUS_KEYS = {"status", "status_code", "http_status", "httpstatus", "code"}


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
    return not bool(re.fullmatch(r"[\W_]+", normalized))


def decode_embedded_structure(
    value: Any,
    path: str = "$",
    parse_errors: list[dict[str, str]] | None = None,
) -> Any:
    """Safely decode JSON/Python-literal payloads without executing code."""
    if not isinstance(value, str):
        return value
    raw = value.strip()
    if not raw or len(raw) > 2_000_000:
        return value
    if not ((raw.startswith("{") and raw.endswith("}")) or (raw.startswith("[") and raw.endswith("]"))):
        return value
    try:
        return json.loads(raw)
    except json.JSONDecodeError as json_exc:
        try:
            parsed = ast.literal_eval(raw)
        except (ValueError, SyntaxError, MemoryError, RecursionError) as literal_exc:
            if parse_errors is not None:
                parse_errors.append({
                    "path": path,
                    "error_type": "embedded_structure_parse_failed",
                    "error_message": f"json={json_exc}; literal={literal_exc}",
                })
            return value
        return parsed if isinstance(parsed, (dict, list, tuple, str, int, float, bool, type(None))) else value


def iter_scalars(
    value: Any,
    path: str,
    parse_errors: list[dict[str, str]] | None = None,
) -> Iterator[tuple[str, Any]]:
    decoded = decode_embedded_structure(value, path, parse_errors)
    if decoded is not value:
        yield from iter_scalars(decoded, path + ".decoded", parse_errors)
        return
    if isinstance(value, dict):
        for key, child in value.items():
            yield from iter_scalars(child, f"{path}.{key}", parse_errors)
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            yield from iter_scalars(child, f"{path}[{index}]", parse_errors)
    elif allowed_scalar(path, value):
        yield path, value


def value_present_in_query(value: Any, query: str) -> bool:
    normalized_value = normalize_scalar(value)
    normalized_query = normalize_scalar(query)
    return bool(normalized_value and normalized_value in normalized_query)


def dependency_type_for_paths(upstream_path: str, downstream_path: str) -> str:
    tokens = set(re.findall(r"[a-z0-9_]+", f"{upstream_path} {downstream_path}".casefold()))
    return "entity_flow" if tokens & ENTITY_PATH_TERMS else "data_flow"


def _walk_values(value: Any) -> Iterator[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield key.casefold(), child
            yield from _walk_values(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _walk_values(child)


def _is_error_text(value: Any) -> bool:
    raw = text(value).casefold()
    return bool(raw and any(re.search(pattern, raw, flags=re.IGNORECASE) for pattern in ERROR_PATTERNS))


def _payload_has_useful_value(value: Any) -> bool:
    decoded = decode_embedded_structure(value)
    if decoded is not value:
        return _payload_has_useful_value(decoded)
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = text(key).casefold()
            if lowered in ERROR_KEY_TERMS or lowered in STATUS_KEYS or lowered in {"message", "detail"}:
                continue
            if _payload_has_useful_value(child):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(_payload_has_useful_value(child) for child in value)
    if value is None or isinstance(value, bool):
        return False
    raw = text(value)
    if not raw or _is_error_text(raw) or "<html" in raw.casefold() or "<!doctype html" in raw.casefold():
        return False
    return normalize_scalar(value) not in STOP_VALUES


def classify_call_execution_status(step: dict[str, Any]) -> str:
    outputs = step.get("outputs")
    observation = step.get("observation")
    payload = outputs if outputs not in (None, "", {}, []) else observation
    if payload in (None, "", {}, []):
        return "unknown"

    decoded = decode_embedded_structure(payload)
    error_signal = False
    explicit_failed = False
    if _is_error_text(payload):
        error_signal = True
    for key, value in _walk_values(decoded):
        if key in ERROR_KEY_TERMS and text(value):
            error_signal = True
        if key in STATUS_KEYS:
            normalized = normalize_scalar(value)
            if normalized in {"failed", "failure", "error", "error_only"}:
                explicit_failed = True
                error_signal = True
            try:
                numeric = int(float(text(value)))
            except ValueError:
                numeric = 0
            if 400 <= numeric <= 599:
                error_signal = True
                explicit_failed = True

    useful = _payload_has_useful_value(decoded)
    if error_signal and useful:
        return "partial_success"
    if error_signal:
        return "error_only"
    if explicit_failed:
        return "failed"
    if useful:
        return "success"
    return "unknown"


def annotate_steps(steps: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    parse_errors: list[dict[str, str]] = []
    annotated: list[dict[str, Any]] = []
    for index, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            continue
        item = dict(step)
        item.setdefault("step_index", index)
        item["call_execution_status"] = classify_call_execution_status(item)
        list(iter_scalars(item.get("outputs"), f"steps[{index}].outputs", parse_errors))
        if item.get("outputs") in (None, "", {}, []):
            list(iter_scalars(item.get("observation"), f"steps[{index}].observation", parse_errors))
        annotated.append(item)
    return annotated, parse_errors


def _upstream_values(
    step: dict[str, Any],
    step_index: int,
    parse_errors: list[dict[str, str]],
) -> list[tuple[str, Any, str]]:
    values: list[tuple[str, Any, str]] = []
    outputs = list(iter_scalars(step.get("outputs"), f"steps[{step_index}].outputs", parse_errors))
    if outputs:
        for path, value in outputs:
            role = "response_payload" if ".response" in path.casefold() else "output"
            if not any(term in path.casefold() for term in (".error", ".errors", ".exception", ".traceback")):
                values.append((path, value, role))
    else:
        values.extend((path, value, "observation") for path, value in iter_scalars(
            step.get("observation"), f"steps[{step_index}].observation", parse_errors
        ))
    values.extend((path, value, "result") for path, value in iter_scalars(
        step.get("result"), f"steps[{step_index}].result", parse_errors
    ))
    values.extend((path, value, "response_payload") for path, value in iter_scalars(
        step.get("response_payload"), f"steps[{step_index}].response_payload", parse_errors
    ))
    return values


def _downstream_values(
    step: dict[str, Any],
    step_index: int,
    parse_errors: list[dict[str, str]],
) -> list[tuple[str, Any, str]]:
    fields = (
        ("arguments", "argument"),
        ("input", "input"),
        ("request", "request"),
        ("tool_selection", "tool_selection"),
        ("branch_condition", "branch_condition"),
    )
    result: list[tuple[str, Any, str]] = []
    for key, role in fields:
        result.extend((path, value, role) for path, value in iter_scalars(
            step.get(key), f"steps[{step_index}].{key}", parse_errors
        ))
    return result


def _argument_values(step: dict[str, Any], step_index: int) -> list[tuple[str, Any]]:
    values: list[tuple[str, Any]] = []
    for key in ("arguments", "input", "request"):
        values.extend(iter_scalars(step.get(key), f"steps[{step_index}].{key}"))
    return values


def _edge_source_type(upstream_role: str, downstream_role: str) -> str:
    if downstream_role == "tool_selection":
        return "upstream_result_to_tool_selection"
    if downstream_role == "branch_condition":
        return "upstream_result_to_branch_condition"
    if upstream_role == "observation":
        return "upstream_observation_to_downstream_input"
    if upstream_role in {"output", "result", "response_payload"} and downstream_role in {"argument", "input", "request"}:
        return "upstream_output_to_downstream_input"
    return "unsupported_edge_type"


def _edge_record(
    record: dict[str, Any],
    from_step: int,
    to_step: int,
    upstream_path: str,
    downstream_path: str,
    upstream_value: Any,
    downstream_value: Any,
    upstream_role: str,
    downstream_role: str,
    edge_source_type: str,
    upstream_status: str,
    downstream_status: str,
    query_known: bool,
    in_upstream_arguments: bool,
    strong: bool,
    notes: str,
) -> dict[str, Any]:
    normalized = normalize_scalar(upstream_value or downstream_value)
    return {
        "trace_record_id": text(record.get("trace_record_id")),
        "source_dataset": text(record.get("source_dataset")),
        "source_group": text(record.get("source_group")),
        "source_task_id": text(record.get("source_task_id")),
        "instruction_query_id": text(record.get("instruction_query_id")),
        "source_file": text(record.get("source_file")),
        "source_record_path": text(record.get("source_record_path")),
        "from_step": from_step,
        "to_step": to_step,
        "dependency_type": dependency_type_for_paths(upstream_path, downstream_path) if strong else edge_source_type,
        "edge_source_type": edge_source_type,
        "upstream_field_role": upstream_role,
        "downstream_field_role": downstream_role,
        "upstream_source_path": upstream_path,
        "downstream_source_path": downstream_path,
        "upstream_value": text(upstream_value),
        "downstream_value": text(downstream_value),
        "evidence_value": text(upstream_value or downstream_value),
        "normalized_evidence_value": normalized,
        "normalized_match": normalized,
        "value_present_in_original_query": query_known,
        "query_known_value_filtered": query_known,
        "value_present_in_upstream_arguments": in_upstream_arguments,
        "upstream_output_is_novel": not query_known and not in_upstream_arguments,
        "upstream_output_is_echo": in_upstream_arguments,
        "upstream_call_execution_status": upstream_status,
        "downstream_call_execution_status": downstream_status,
        "strong_edge_eligible": strong,
        "evidence_strength": "strong_exact_role_validated" if strong else "filtered_non_strong",
        "extraction_notes": notes,
    }


def extract_dependency_edges(
    record: dict[str, Any],
    *,
    common_argument_frequency: Counter[str] | None = None,
    common_value_threshold: int = 10,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, str]]]:
    """Return candidate edges, execution-annotated steps, and parse errors."""
    steps, parse_errors = annotate_steps(record.get("steps", []))
    query = text(record.get("query_text"))
    edges: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()

    for later_pos in range(1, len(steps)):
        later_step_no = int(steps[later_pos].get("step_index") or later_pos + 1)
        downstream_values = _downstream_values(steps[later_pos], later_step_no, parse_errors)
        pair_has_nonsequence = False
        for earlier_pos in range(later_pos):
            earlier_step_no = int(steps[earlier_pos].get("step_index") or earlier_pos + 1)
            upstream_values = _upstream_values(steps[earlier_pos], earlier_step_no, parse_errors)
            upstream_arguments = _argument_values(steps[earlier_pos], earlier_step_no)
            upstream_argument_norms = {normalize_scalar(value) for _, value in upstream_arguments if normalize_scalar(value)}
            earlier_status = text(steps[earlier_pos].get("call_execution_status")) or "unknown"
            later_status = text(steps[later_pos].get("call_execution_status")) or "unknown"

            for upstream_path, upstream_value, upstream_role in upstream_values:
                normalized_upstream = normalize_scalar(upstream_value)
                if not normalized_upstream:
                    continue
                for downstream_path, downstream_value, downstream_role in downstream_values:
                    if normalized_upstream != normalize_scalar(downstream_value):
                        continue
                    query_known = value_present_in_query(upstream_value, query)
                    echoed = normalized_upstream in upstream_argument_norms
                    common = bool(
                        common_argument_frequency
                        and common_argument_frequency[normalized_upstream] >= common_value_threshold
                    )
                    if earlier_status in FAILED_STATUSES or later_status in FAILED_STATUSES:
                        source_type = "failed_call_or_error_output"
                        notes = "A failed/error-only endpoint cannot supply or consume strong dependency evidence."
                    elif query_known:
                        source_type = "query_known_value_reuse"
                        notes = "The matched value already appears in the original query."
                    elif echoed:
                        source_type = "echoed_upstream_input"
                        notes = "The response repeats a value already present in the same upstream call input."
                    elif common:
                        source_type = "unsupported_edge_type"
                        notes = "The matched scalar is a corpus-common argument value and is conservatively filtered."
                    else:
                        source_type = _edge_source_type(upstream_role, downstream_role)
                        notes = "Exact role-valid upstream result to downstream input/control match."
                    strong = (
                        source_type in STRONG_EDGE_SOURCE_TYPES
                        and upstream_role in VALID_UPSTREAM_ROLES
                        and downstream_role in VALID_DOWNSTREAM_ROLES
                        and earlier_status not in FAILED_STATUSES
                        and later_status not in FAILED_STATUSES
                        and not query_known
                        and not echoed
                        and not common
                    )
                    key = (earlier_step_no, later_step_no, upstream_path, downstream_path, normalized_upstream, source_type)
                    if key in seen:
                        continue
                    seen.add(key)
                    pair_has_nonsequence = True
                    edges.append(_edge_record(
                        record, earlier_step_no, later_step_no, upstream_path, downstream_path,
                        upstream_value, downstream_value, upstream_role, downstream_role,
                        source_type, earlier_status, later_status, query_known, echoed, strong, notes,
                    ))

            earlier_args = _argument_values(steps[earlier_pos], earlier_step_no)
            later_args = _argument_values(steps[later_pos], later_step_no)
            for upstream_path, upstream_value in earlier_args:
                normalized = normalize_scalar(upstream_value)
                if not normalized:
                    continue
                for downstream_path, downstream_value in later_args:
                    if normalized != normalize_scalar(downstream_value):
                        continue
                    key = (earlier_step_no, later_step_no, upstream_path, downstream_path, normalized, "shared_input_only")
                    if key in seen:
                        continue
                    seen.add(key)
                    pair_has_nonsequence = True
                    query_known = value_present_in_query(upstream_value, query)
                    edges.append(_edge_record(
                        record, earlier_step_no, later_step_no, upstream_path, downstream_path,
                        upstream_value, downstream_value, "argument", "argument",
                        "shared_input_only", earlier_status, later_status, query_known, True, False,
                        "The same request value is shared by two calls; argument-to-argument reuse is not dependency evidence.",
                    ))

        if not pair_has_nonsequence:
            previous = steps[later_pos - 1]
            previous_step_no = int(previous.get("step_index") or later_pos)
            key = (previous_step_no, later_step_no, "", "", "", "sequence_only")
            if key not in seen:
                seen.add(key)
                edges.append(_edge_record(
                    record, previous_step_no, later_step_no,
                    text(previous.get("source_json_path")), text(steps[later_pos].get("source_json_path")),
                    "", "", "unknown", "unknown", "sequence_only",
                    text(previous.get("call_execution_status")) or "unknown",
                    text(steps[later_pos].get("call_execution_status")) or "unknown",
                    False, False, False,
                    "Call order alone is not dependency evidence.",
                ))
    return edges, steps, parse_errors


def classify_evidence(record: dict[str, Any], edges: list[dict[str, Any]]) -> str:
    parse_summary = record.get("parse_summary") if isinstance(record.get("parse_summary"), dict) else {}
    parse_status = text(parse_summary.get("parse_status") or record.get("parse_status") or "ok")
    if parse_status in {"source_unavailable", "join_ambiguous"}:
        return parse_status
    if parse_status not in {"", "ok"}:
        return "parse_failed"
    strong = [edge for edge in edges if truthy(edge.get("strong_edge_eligible"))]
    steps = [step for step in record.get("steps", []) if isinstance(step, dict)]
    services = {text(step.get("service_name")) for step in steps if text(step.get("service_name"))}
    apis = {text(step.get("function_name") or step.get("api_name")) for step in steps if text(step.get("function_name") or step.get("api_name"))}
    if strong and len(steps) >= 2 and (len(services) >= 2 or len(apis) >= 2):
        return "strong_objective_evidence_available"
    nonsequence = [edge for edge in edges if text(edge.get("edge_source_type")) != "sequence_only"]
    if nonsequence:
        return "no_dependency_evidence"
    if len(steps) >= 2:
        return "sequence_only"
    return "no_dependency_evidence"


def assess_record(
    record: dict[str, Any],
    *,
    common_argument_frequency: Counter[str] | None = None,
    common_value_threshold: int = 10,
) -> dict[str, Any]:
    edges, steps, parse_errors = extract_dependency_edges(
        record,
        common_argument_frequency=common_argument_frequency,
        common_value_threshold=common_value_threshold,
    )
    enriched = dict(record)
    enriched["steps"] = steps
    status = classify_evidence(enriched, edges)
    source_counts = Counter(text(edge.get("edge_source_type")) for edge in edges)
    strong_edges = [edge for edge in edges if truthy(edge.get("strong_edge_eligible"))]
    failed_calls = [
        {
            "step_index": step.get("step_index"),
            "service_name": text(step.get("service_name")),
            "api_name": text(step.get("api_name") or step.get("function_name")),
            "call_execution_status": text(step.get("call_execution_status")),
            "source_json_path": text(step.get("source_json_path")),
        }
        for step in steps if text(step.get("call_execution_status")) in FAILED_STATUSES
    ]
    shared_values = sorted({
        text(edge.get("evidence_value")) for edge in edges
        if text(edge.get("edge_source_type")) == "shared_input_only" and text(edge.get("evidence_value"))
    })
    distinct_services = len({text(step.get("service_name")) for step in steps if text(step.get("service_name"))})
    distinct_apis = len({text(step.get("function_name") or step.get("api_name")) for step in steps if text(step.get("function_name") or step.get("api_name"))})
    if status == "strong_objective_evidence_available":
        suggested = "true_composable_candidate"
    elif len(steps) >= 2 and (distinct_services >= 2 or distinct_apis >= 2):
        suggested = "parallel_multi" if source_counts["shared_input_only"] or source_counts["sequence_only"] else "no_dependency"
    else:
        suggested = "no_dependency"
    return {
        "record": enriched,
        "edges": edges,
        "strong_edges": strong_edges,
        "parse_errors": parse_errors,
        "evidence_status": status,
        "suggested_class": suggested,
        "edge_source_type_counts": dict(sorted(source_counts.items())),
        "strong_edge_count": len(strong_edges),
        "shared_input_values": shared_values,
        "failed_calls": failed_calls,
        "execution_evidence_incomplete": bool(failed_calls),
        "call_execution_status_counts": dict(sorted(Counter(
            text(step.get("call_execution_status")) or "unknown" for step in steps
        ).items())),
    }


def collect_argument_frequency(records: Iterable[dict[str, Any]]) -> Counter[str]:
    frequency: Counter[str] = Counter()
    for record in records:
        values: set[str] = set()
        for position, step in enumerate(record.get("steps", []), start=1):
            if not isinstance(step, dict):
                continue
            for _, value in _argument_values(step, int(step.get("step_index") or position)):
                normalized = normalize_scalar(value)
                if normalized:
                    values.add(normalized)
        frequency.update(values)
    return frequency
