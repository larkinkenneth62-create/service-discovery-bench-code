#!/usr/bin/env python3
"""Structural task-necessity gate for paired composable review candidates."""

from __future__ import annotations

import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import composable_dependency_extractor_v0_3_2 as extractor  # noqa: E402


VERSION = "v0.3.3"
MACHINE_RULE_SPEC_VERSION = "v1.0"
FAILED_STATUSES = {"failed", "error_only"}
SUCCESS_STATUSES = {"success", "partial_success"}
EXACT_LEAK_STATUS = "exact_unique_name_leak"

HUMAN_NECESSITY_FIELDS = [
    "dependency_required_for_query",
    "upstream_already_satisfies_subgoal",
    "full_query_subgoals_covered_by_gold_chain",
    "disconnected_parallel_subgoals_present",
    "cross_service_dependency_valid",
]

RESULT_TYPE_PATTERNS = {
    "distance": ("distance", "mileage", "kilometer", "kilometre", "mile"),
    "price": ("price", "cost", "amount", "fare", "quote", "rate"),
    "status": ("status", "state", "condition"),
    "weather": ("weather", "temperature", "forecast", "humidity", "rain"),
    "time": ("time", "timezone", "utc", "date", "hour"),
    "address": ("address", "street", "postcode", "postal", "zip"),
    "location": ("location", "coordinate", "latitude", "longitude", "city", "country"),
    "identifier": ("identifier", "place_id", "product_id", "hotel_id", "restaurant_id"),
}

QUERY_STOPWORDS = {
    "a", "an", "and", "api", "are", "as", "at", "be", "by", "can", "for",
    "from", "get", "i", "in", "is", "it", "me", "my", "of", "on", "or",
    "please", "provide", "service", "that", "the", "this", "to", "tool", "use",
    "want", "with", "you",
}


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


def normalize_identifier(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", text(value)).casefold()
    normalized = re.sub(r"[^a-z0-9]+", "_", normalized)
    return re.sub(r"_+", "_", normalized).strip("_")


def normalized_tokens(value: Any) -> set[str]:
    normalized = unicodedata.normalize("NFKC", text(value)).casefold()
    return {
        token
        for token in re.findall(r"[a-z0-9]+", normalized)
        if len(token) > 1 and token not in QUERY_STOPWORDS
    }


def item_key(item: dict[str, Any], fields: tuple[str, ...]) -> str:
    for field in fields:
        value = normalize_identifier(item.get(field))
        if value:
            return value
    return ""


def distinct_item_keys(value: Any, fields: tuple[str, ...]) -> set[str]:
    items = parse_json(value, [])
    if not isinstance(items, list):
        return set()
    return {key for item in items if isinstance(item, dict) if (key := item_key(item, fields))}


def step_service_key(step: dict[str, Any]) -> str:
    direct = normalize_identifier(step.get("service_name"))
    if direct:
        return direct
    function_name = text(step.get("function_name"))
    if "_for_" in function_name:
        return normalize_identifier(function_name.rsplit("_for_", 1)[1])
    return ""


def step_api_key(step: dict[str, Any]) -> str:
    return normalize_identifier(
        step.get("function_name") or step.get("api_name") or step.get("function_key")
    )


def step_status(step: dict[str, Any]) -> str:
    status = text(step.get("call_execution_status")).casefold()
    return status or extractor.classify_call_execution_status(step)


def result_types(value: Any) -> set[str]:
    rendered = text(value).casefold().replace("-", "_")
    found = set()
    for result_type, terms in RESULT_TYPE_PATTERNS.items():
        if any(re.search(rf"(?:^|[^a-z0-9]){re.escape(term)}(?:$|[^a-z0-9])", rendered) for term in terms):
            found.add(result_type)
    return found


def scalar_map(value: Any) -> dict[str, set[str]]:
    mapped: dict[str, set[str]] = {}
    for path, scalar in extractor.iter_scalars(value, "$.outputs", []):
        normalized = extractor.normalize_scalar(scalar)
        if not normalized:
            continue
        mapped.setdefault(normalized, set()).update(result_types(path))
    return mapped


def step_by_index(steps: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    result = {}
    for position, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            continue
        index = int_value(step.get("step_index")) or position
        result[index] = step
    return result


def classify_edge_services(
    edges: list[dict[str, Any]], steps_by_index: dict[int, dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    cross_service = []
    same_service = []
    unresolved = []
    for edge in edges:
        upstream = steps_by_index.get(int_value(edge.get("from_step")), {})
        downstream = steps_by_index.get(int_value(edge.get("to_step")), {})
        upstream_service = step_service_key(upstream)
        downstream_service = step_service_key(downstream)
        enriched = {
            **edge,
            "upstream_service_key": upstream_service,
            "downstream_service_key": downstream_service,
            "upstream_api_key": step_api_key(upstream),
            "downstream_api_key": step_api_key(downstream),
        }
        if not upstream_service or not downstream_service:
            unresolved.append(enriched)
        elif upstream_service == downstream_service:
            same_service.append(enriched)
        else:
            cross_service.append(enriched)
    return cross_service, same_service, unresolved


def candidate_space_is_valid(row: dict[str, Any], level: str) -> bool:
    if level == "service":
        candidates = distinct_item_keys(row.get("candidate_services_json"), ("service_key", "service_name"))
        gold = distinct_item_keys(row.get("provisional_gold_services_json"), ("service_key", "service_name"))
        status = text(row.get("service_candidate_space_status"))
        negative_count = int_value(row.get("service_negative_distractor_count"))
    else:
        candidates = distinct_item_keys(row.get("candidate_apis_json"), ("function_key", "function_name", "api_name"))
        gold = distinct_item_keys(row.get("provisional_gold_apis_json"), ("function_key", "function_name", "api_name"))
        status = text(row.get("api_candidate_space_status"))
        negative_count = int_value(row.get("api_negative_distractor_count"))
    return bool(status == "valid" and gold and gold < candidates and negative_count > 0)


def disconnected_query_relevant_calls(
    query: str, disconnected: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    query_tokens = normalized_tokens(query)
    relevant = []
    for step in disconnected:
        if not isinstance(step, dict) or step_status(step) not in SUCCESS_STATUSES:
            continue
        call_tokens = normalized_tokens(
            " ".join(
                [
                    text(step.get("service_name")),
                    text(step.get("api_name")),
                    text(step.get("function_name")),
                ]
            )
        )
        overlap = sorted(query_tokens & call_tokens)
        if overlap:
            relevant.append(
                {
                    "step_index": int_value(step.get("step_index")),
                    "service_name": text(step.get("service_name")),
                    "api_name": text(step.get("api_name")),
                    "function_name": text(step.get("function_name")),
                    "query_token_overlap": overlap,
                }
            )
    return relevant


def redundancy_assessment(
    query: str,
    cross_service_edges: list[dict[str, Any]],
    same_service_edges: list[dict[str, Any]],
    steps_by_index: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    query_types = result_types(query)
    pair_results: list[dict[str, Any]] = []
    repeated_types: set[str] = set()
    high_confidence_pairs: set[tuple[int, int]] = set()
    all_pairs: set[tuple[int, int]] = set()

    for edge in [*cross_service_edges, *same_service_edges]:
        from_step = int_value(edge.get("from_step"))
        to_step = int_value(edge.get("to_step"))
        pair = (from_step, to_step)
        if pair in all_pairs:
            continue
        all_pairs.add(pair)
        upstream = steps_by_index.get(from_step, {})
        downstream = steps_by_index.get(to_step, {})
        upstream_payload = upstream.get("outputs") or upstream.get("observation")
        downstream_payload = downstream.get("outputs") or downstream.get("observation")
        upstream_types = result_types(upstream_payload)
        downstream_operation_types = result_types(
            " ".join(
                [
                    text(downstream.get("api_name")),
                    text(downstream.get("function_name")),
                ]
            )
        )
        downstream_output_types = result_types(downstream_payload)
        matched_types = upstream_types & (downstream_operation_types | downstream_output_types)
        repeated_types.update(matched_types)
        upstream_scalars = scalar_map(upstream_payload)
        downstream_scalars = scalar_map(downstream_payload)
        exact_values = sorted(set(upstream_scalars) & set(downstream_scalars))
        exact_typed_values = [
            value
            for value in exact_values
            if (upstream_scalars[value] | downstream_scalars[value]) & matched_types
        ]
        same_operation = bool(
            step_api_key(upstream)
            and step_api_key(upstream) == step_api_key(downstream)
        )
        upstream_already = bool(matched_types & query_types)
        new_types = downstream_output_types - upstream_types
        high_confidence = bool(
            (upstream_already and matched_types & downstream_operation_types and not new_types)
            or exact_typed_values
            or same_operation
        )
        if high_confidence:
            high_confidence_pairs.add(pair)
        pair_results.append(
            {
                "from_step": from_step,
                "to_step": to_step,
                "matched_result_types": sorted(matched_types),
                "query_result_types": sorted(query_types),
                "exact_repeated_output_values": exact_typed_values[:12],
                "same_operation_retry": same_operation,
                "upstream_already_returns_requested_result": upstream_already,
                "downstream_new_result_types": sorted(new_types),
                "high_confidence_redundant_pair": high_confidence,
            }
        )

    cross_pairs = {
        (int_value(edge.get("from_step")), int_value(edge.get("to_step")))
        for edge in cross_service_edges
    }
    only_redundant_cross_service = bool(
        cross_pairs and cross_pairs <= high_confidence_pairs
    )
    upstream_already_any = any(
        item["upstream_already_returns_requested_result"] for item in pair_results
    )
    downstream_adds = "uncertain"
    if pair_results and all(item["downstream_new_result_types"] for item in pair_results):
        downstream_adds = "true"
    elif pair_results and all(
        item["high_confidence_redundant_pair"] and not item["downstream_new_result_types"]
        for item in pair_results
    ):
        downstream_adds = "false"
    reasons = []
    if upstream_already_any:
        reasons.append("upstream_output_contains_query_requested_result_type")
    if any(item["exact_repeated_output_values"] for item in pair_results):
        reasons.append("upstream_and_downstream_repeat_same_typed_output_value")
    if any(item["same_operation_retry"] for item in pair_results):
        reasons.append("same_operation_retry_or_alternative_call")
    if only_redundant_cross_service:
        reasons.append("all_cross_service_dependency_pairs_look_redundant")
    return {
        "possible_redundant_recomputation": bool(pair_results and reasons),
        "upstream_already_returns_requested_result": upstream_already_any,
        "repeated_result_type": sorted(repeated_types),
        "downstream_adds_new_required_information": downstream_adds,
        "necessity_risk_reason": reasons,
        "only_redundant_recomputation_dependency": only_redundant_cross_service,
        "redundancy_evidence": pair_results,
    }


def assess_task(row: dict[str, Any]) -> dict[str, Any]:
    query = text(row.get("query_text"))
    steps = parse_json(row.get("ordered_steps_json"), [])
    edges = parse_json(row.get("dependency_edges_json"), [])
    disconnected = parse_json(row.get("disconnected_calls_json"), [])
    steps = steps if isinstance(steps, list) else []
    edges = [edge for edge in edges if isinstance(edge, dict) and truthy(edge.get("strong_edge_eligible"))]
    disconnected = disconnected if isinstance(disconnected, list) else []
    steps_by_index = step_by_index(steps)
    cross_edges, same_edges, unresolved_edges = classify_edge_services(edges, steps_by_index)

    gold_services = distinct_item_keys(
        row.get("provisional_gold_services_json"), ("service_key", "service_name")
    )
    gold_apis = distinct_item_keys(
        row.get("provisional_gold_apis_json"), ("function_key", "function_name", "api_name")
    )
    gold_steps = {
        int_value(edge.get(key))
        for edge in edges
        for key in ("from_step", "to_step")
        if int_value(edge.get(key))
    }
    gold_statuses = {index: step_status(steps_by_index.get(index, {})) for index in gold_steps}
    successful_gold_call_count = sum(status in SUCCESS_STATUSES for status in gold_statuses.values())
    failed_or_error_gold_call_count = sum(status in FAILED_STATUSES for status in gold_statuses.values())
    failed_dependency_count = sum(
        text(edge.get("upstream_call_execution_status")).casefold() in FAILED_STATUSES
        or text(edge.get("downstream_call_execution_status")).casefold() in FAILED_STATUSES
        or step_status(steps_by_index.get(int_value(edge.get("from_step")), {})) in FAILED_STATUSES
        or step_status(steps_by_index.get(int_value(edge.get("to_step")), {})) in FAILED_STATUSES
        for edge in edges
    )
    exact_service_leak = text(row.get("service_leak_status")) == EXACT_LEAK_STATUS
    exact_api_leak = text(row.get("api_leak_status")) == EXACT_LEAK_STATUS
    service_space_valid = candidate_space_is_valid(row, "service")
    api_space_valid = candidate_space_is_valid(row, "api")
    relevant_disconnected = disconnected_query_relevant_calls(query, disconnected)
    component_count = int_value(row.get("connected_dependency_component_count"))
    conjunction_signal = bool(
        re.search(r"\b(?:also|additionally|as well as|furthermore|moreover)\b", query, flags=re.IGNORECASE)
    )
    parallel_subgoal_risk = bool(
        len(relevant_disconnected) > 0
        or component_count > 1
        or (conjunction_signal and disconnected)
    )
    redundancy = redundancy_assessment(query, cross_edges, same_edges, steps_by_index)

    reasons = []
    if not query:
        reasons.append("empty_query")
    if not edges:
        reasons.append("empty_dependency_evidence")
    if len(gold_services) < 2:
        reasons.append("gold_service_count_lt_2")
    if len(gold_apis) < 2:
        reasons.append("gold_api_count_lt_2")
    if not cross_edges:
        reasons.append("no_cross_service_strong_edge")
    if same_edges and not cross_edges:
        reasons.append("same_service_only_dependency")
    if unresolved_edges:
        reasons.append("unresolved_edge_service_mapping")
    if failed_dependency_count:
        reasons.append("failed_or_error_dependency_edge")
    if failed_or_error_gold_call_count:
        reasons.append("failed_or_error_gold_call")
    if gold_steps and successful_gold_call_count != len(gold_steps):
        reasons.append("gold_dependency_core_not_fully_successful")
    if exact_service_leak:
        reasons.append("exact_blocking_service_name_leak")
    if exact_api_leak:
        reasons.append("exact_blocking_api_name_leak")
    if not service_space_valid:
        reasons.append("service_candidate_space_invalid")
    if not api_space_valid:
        reasons.append("api_candidate_space_invalid")
    if text(row.get("dependency_graph_is_dag")) and not truthy(row.get("dependency_graph_is_dag")):
        reasons.append("dependency_graph_not_dag")
    if redundancy["only_redundant_recomputation_dependency"]:
        reasons.append("only_redundant_recomputation_dependency")

    hard_pass = not reasons
    api_only = bool(edges and same_edges and not cross_edges and len(gold_apis) >= 2)
    risk_flags = {
        "possible_redundant_recomputation": redundancy["possible_redundant_recomputation"],
        "parallel_subgoal_risk": parallel_subgoal_risk,
        "hybrid_composable_multi_risk": bool(cross_edges and parallel_subgoal_risk),
        "possible_incomplete_gold_chain": bool(relevant_disconnected),
        "possible_incidental_call": bool(disconnected),
    }
    if not query or not steps:
        machine_status = "SOURCE_UNAVAILABLE_HOLD"
    elif api_only:
        machine_status = "API_ONLY_WORKFLOW_CANDIDATE"
    elif not hard_pass:
        machine_status = "STRUCTURALLY_INELIGIBLE"
    elif any(risk_flags.values()):
        machine_status = "STRUCTURALLY_ELIGIBLE_WITH_RISK"
    else:
        machine_status = "STRUCTURALLY_ELIGIBLE_FOR_REVIEW"

    result = {
        "machine_review_status": machine_status,
        "machine_rule_spec_version": MACHINE_RULE_SPEC_VERSION,
        "structural_hard_gate_pass": hard_pass,
        "structural_ineligibility_reasons": reasons,
        "distinct_gold_service_count": len(gold_services),
        "distinct_gold_api_count": len(gold_apis),
        "strong_edge_count": len(edges),
        "cross_service_strong_edge_count": len(cross_edges),
        "same_service_strong_edge_count": len(same_edges),
        "unresolved_service_edge_count": len(unresolved_edges),
        "successful_gold_call_count": successful_gold_call_count,
        "failed_or_error_gold_call_count": failed_or_error_gold_call_count,
        "failed_call_dependency_count": failed_dependency_count,
        "disconnected_query_relevant_call_count": len(relevant_disconnected),
        "exact_gold_service_name_leak": exact_service_leak,
        "exact_gold_api_name_leak": exact_api_leak,
        "service_level_structurally_eligible": bool(hard_pass and not exact_service_leak),
        "api_level_structurally_eligible": bool(hard_pass and not exact_api_leak),
        "service_candidate_space_structurally_valid": service_space_valid,
        "api_candidate_space_structurally_valid": api_space_valid,
        "api_only_workflow_candidate": api_only,
        "disconnected_query_relevant_calls": relevant_disconnected,
        "cross_service_edges": cross_edges,
        "same_service_edges": same_edges,
        "requires_human_semantic_review": hard_pass,
        "requires_human_necessity_review": bool(
            hard_pass or redundancy["possible_redundant_recomputation"] or parallel_subgoal_risk
        ),
        **redundancy,
        **risk_flags,
    }
    return result


def assessment_csv_fields(result: dict[str, Any]) -> dict[str, str]:
    json_fields = {
        "structural_ineligibility_reasons": "structural_ineligibility_reasons_json",
        "repeated_result_type": "repeated_result_type_json",
        "necessity_risk_reason": "necessity_risk_reason_json",
        "redundancy_evidence": "redundancy_evidence_json",
        "disconnected_query_relevant_calls": "disconnected_query_relevant_calls_json",
        "cross_service_edges": "cross_service_edges_json",
        "same_service_edges": "same_service_edges_json",
    }
    output: dict[str, str] = {}
    for key, value in result.items():
        if key in json_fields:
            output[json_fields[key]] = json_dumps(value)
        elif isinstance(value, bool):
            output[key] = bool_text(value)
        elif isinstance(value, (list, dict)):
            output[f"{key}_json"] = json_dumps(value)
        else:
            output[key] = text(value)
    return output
