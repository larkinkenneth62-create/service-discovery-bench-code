#!/usr/bin/env python3
"""Deterministic helpers for bounded composable candidate recovery v1.0.1."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable

import composable_task_necessity_gate_v0_3_3 as gate
import prepare_composable_paired_tasks_v0_3 as prep


VERSION = "v1.0.1"
MACHINE_RULE_SPEC_VERSION = "v1.0"

LEAK_REASONS = {
    "exact_blocking_service_name_leak",
    "exact_blocking_api_name_leak",
}
CANDIDATE_REASONS = {
    "service_candidate_space_invalid",
    "api_candidate_space_invalid",
}
SHARED_OR_SEQUENCE_REASONS = {
    "shared_input_only",
    "shared_input_only_dependency",
    "sequence_only",
    "sequence_only_dependency",
    "no_objective_dependency_evidence",
}

HUMAN_FIELDS = [
    "dependency_required_for_query",
    "upstream_already_satisfies_subgoal",
    "full_query_subgoals_covered_by_gold_chain",
    "disconnected_parallel_subgoals_present",
    "cross_service_dependency_valid",
    "dependency_edge_valid",
    "dependency_evidence_sufficient",
    "composition_final_label",
    "query_gold_chain_alignment",
    "service_gold_complete",
    "service_candidate_space_valid",
    "service_leakage_final",
    "service_level_eligible",
    "api_gold_complete",
    "api_candidate_space_valid",
    "api_parent_mapping_valid",
    "api_leakage_final",
    "api_level_eligible",
    "composable_release_action",
    "adjudicator_id",
    "adjudicator_type",
    "adjudicated_at",
    "adjudication_notes",
]

SEMANTIC_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "could", "for",
    "from", "give", "help", "i", "in", "is", "it", "me", "my", "of", "on",
    "please", "provide", "the", "to", "use", "using", "via", "want", "with",
    "would", "you", "api", "service", "tool", "endpoint", "call",
}


def text(value: Any) -> str:
    return str(value or "").strip()


def truthy(value: Any) -> bool:
    return text(value).casefold() in {"1", "true", "yes", "y"}


def int_value(value: Any) -> int:
    try:
        return int(float(text(value) or "0"))
    except ValueError:
        return 0


def parse_json(value: Any, default: Any) -> Any:
    if isinstance(value, (list, dict)):
        return value
    if not text(value):
        return default
    try:
        return json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return default


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def canonical_reason(reason: str) -> str:
    mapping = {
        "endpoint_service_count_lt_2": "gold_service_count_lt_2",
        "endpoint_api_count_lt_2": "gold_api_count_lt_2",
        "no_cross_service_edge": "no_cross_service_strong_edge",
        "same_service_only": "same_service_only_dependency",
        "failed_dependency_edge": "failed_or_error_dependency_edge",
        "failed_call_dependency": "failed_or_error_dependency_edge",
        "normalized_record_missing": "source_unavailable",
        "unresolved_service_mapping": "source_mapping_unresolved",
    }
    return mapping.get(text(reason), text(reason))


def classify_recovery(
    reasons: Iterable[str], *, authoritative_existing: bool = False, source_available: bool = True
) -> str:
    canonical = {canonical_reason(item) for item in reasons if text(item)}
    if authoritative_existing:
        return "AUTHORITATIVE_VALID_EXISTING"
    if not source_available or "source_unavailable" in canonical:
        return "SOURCE_UNAVAILABLE_HOLD"
    if "failed_or_error_dependency_edge" in canonical or "failed_or_error_gold_call" in canonical:
        return "HARD_UNRECOVERABLE_FAILED_DEPENDENCY"
    if "gold_service_count_lt_2" in canonical:
        return "HARD_UNRECOVERABLE_GOLD_SERVICE_COUNT_LT_2"
    if "same_service_only_dependency" in canonical:
        return "HARD_UNRECOVERABLE_SAME_SERVICE_ONLY"
    if "no_cross_service_strong_edge" in canonical:
        return "HARD_UNRECOVERABLE_NO_CROSS_SERVICE_EDGE"
    if "only_redundant_recomputation_dependency" in canonical:
        return "HARD_UNRECOVERABLE_REDUNDANCY_ONLY"
    if canonical & SHARED_OR_SEQUENCE_REASONS:
        return "HARD_UNRECOVERABLE_SHARED_INPUT_OR_SEQUENCE_ONLY"
    has_leak = bool(canonical & LEAK_REASONS)
    has_candidate = bool(canonical & CANDIDATE_REASONS)
    if canonical and canonical <= LEAK_REASONS:
        return "REPAIRABLE_EXACT_LEAK_ONLY"
    if canonical and canonical <= CANDIDATE_REASONS:
        return "REPAIRABLE_CANDIDATE_SPACE_ONLY"
    if has_leak and has_candidate and canonical <= (LEAK_REASONS | CANDIDATE_REASONS):
        return "REPAIRABLE_LEAK_AND_CANDIDATE_SPACE"
    return "OTHER_HOLD"


def semantic_tokens(value: str) -> list[str]:
    return sorted(
        {
            token.casefold()
            for token in re.findall(r"[A-Za-z0-9]+", value)
            if token.casefold() not in SEMANTIC_STOPWORDS and len(token) > 1
        }
    )


def exact_leak_names(
    query: str, gold_services: list[dict[str, Any]], gold_apis: list[dict[str, Any]]
) -> list[dict[str, str]]:
    _, service_signals = prep.leak_status(query, gold_services, "service")
    _, api_signals = prep.leak_status(query, gold_apis, "api")
    names: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for level, signals in (("service", service_signals), ("api", api_signals)):
        for signal in signals:
            if text(signal.get("signal")) != "exact_unique_name_leak":
                continue
            key = (level, text(signal.get("value")).casefold())
            if key in seen:
                continue
            seen.add(key)
            names.append({"level": level, "name": text(signal.get("value"))})
    return names


def _name_pattern(name: str) -> str:
    parts = [re.escape(part) for part in re.split(r"\s+", name.strip()) if part]
    return r"\s+".join(parts)


def _cleanup_query(value: str) -> str:
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"\s+([,.;:!?])", r"\1", value)
    value = re.sub(r"([,;:])\1+", r"\1", value)
    value = re.sub(r"\b(?:and|also)\s*([?.!,])", r"\1", value, flags=re.IGNORECASE)
    value = re.sub(r"([.!?])\s*([.!?])+", r"\1", value)
    return value.strip(" ,;:")


def deterministic_leakage_rewrite(
    query: str, gold_services: list[dict[str, Any]], gold_apis: list[dict[str, Any]]
) -> dict[str, Any]:
    original = text(query)
    names = exact_leak_names(original, gold_services, gold_apis)
    before_tokens = semantic_tokens(original)
    if not names:
        return {
            "original_query_text": original,
            "proposed_rewritten_query_text": original,
            "removed_exact_names": [],
            "rewrite_patterns": [],
            "semantic_tokens_before": before_tokens,
            "semantic_tokens_after": before_tokens,
            "query_still_nonempty": bool(original),
            "query_action_object_preserved": True,
            "exact_service_leak_after": False,
            "exact_api_leak_after": False,
            "deterministic_rewrite_status": "REWRITE_NOT_APPLICABLE",
        }

    rewritten = original
    removed: list[dict[str, str]] = []
    patterns: list[str] = []
    unmatched: list[dict[str, str]] = []
    for item in sorted(names, key=lambda value: (-len(value["name"]), value["level"], value["name"].casefold())):
        escaped = _name_pattern(item["name"])
        if item["level"] == "service":
            # Service-name removal is authorized only in explicit
            # tool-selection constructions.
            expression = rf"(?:using|with|via|from)\s+(?:the\s+)?{escaped}"
        else:
            # API names are often ordinary capability nouns (for example,
            # WHOIS). Requiring use/call or 'using the' avoids deleting the
            # requested operation from a natural-language phrase.
            expression = rf"(?:(?:use|call)\s+(?:the\s+)?{escaped}|using\s+the\s+{escaped})"
        pattern = re.compile(
            rf"(?<![A-Za-z0-9]){expression}(?:\s+(?:api|tool|service|endpoint))?(?![A-Za-z0-9])",
            flags=re.IGNORECASE,
        )
        rewritten, count = pattern.subn("", rewritten)
        if count:
            removed.append(item)
            patterns.append(f"connector_exact_{item['level']}_name")
        else:
            unmatched.append(item)

    rewritten = _cleanup_query(rewritten)
    after_tokens = semantic_tokens(rewritten)
    removed_name_tokens = {
        token.casefold()
        for item in removed
        for token in re.findall(r"[A-Za-z0-9]+", item["name"])
    }
    expected = set(before_tokens) - removed_name_tokens
    preserved = expected <= set(after_tokens)
    grammar_unsafe = bool(
        re.search(r"\b(?:using|with|via|from|use|call)\s+(?:the\s*)?(?:[,.!?]|$)", rewritten, re.IGNORECASE)
        or re.search(r"\b(?:and|also)\s+(?:and|also)\b", rewritten, re.IGNORECASE)
    )
    service_after, _ = prep.leak_status(rewritten, gold_services, "service")
    api_after, _ = prep.leak_status(rewritten, gold_apis, "api")
    service_leak_after = service_after == gate.EXACT_LEAK_STATUS
    api_leak_after = api_after == gate.EXACT_LEAK_STATUS

    if unmatched:
        status = "REWRITE_NOT_APPLICABLE"
    elif not rewritten or len(after_tokens) < 2:
        status = "REWRITE_UNSAFE_MEANING_LOSS"
    elif not preserved:
        status = "REWRITE_UNSAFE_MEANING_LOSS"
    elif grammar_unsafe:
        status = "REWRITE_UNSAFE_GRAMMAR"
    elif service_leak_after or api_leak_after:
        status = "REWRITE_STILL_LEAKING"
    else:
        status = "REWRITE_VALID"
    return {
        "original_query_text": original,
        "proposed_rewritten_query_text": rewritten,
        "removed_exact_names": removed,
        "unmatched_exact_names": unmatched,
        "rewrite_patterns": patterns,
        "semantic_tokens_before": before_tokens,
        "semantic_tokens_after": after_tokens,
        "query_still_nonempty": bool(rewritten),
        "query_action_object_preserved": preserved,
        "exact_service_leak_after": service_leak_after,
        "exact_api_leak_after": api_leak_after,
        "deterministic_rewrite_status": status,
    }


def alias_fingerprint(item: dict[str, Any], level: str) -> str:
    if level == "service":
        return gate.normalize_identifier(item.get("service_name") or item.get("service_key"))
    return "::".join(
        [
            gate.normalize_identifier(item.get("service_key") or item.get("service_name")),
            gate.normalize_identifier(item.get("api_name") or item.get("function_name") or item.get("function_key")),
        ]
    )


def has_alias_conflict(items: list[dict[str, Any]], level: str) -> bool:
    fingerprints = [alias_fingerprint(item, level) for item in items]
    fingerprints = [value for value in fingerprints if value]
    return len(fingerprints) != len(set(fingerprints))


def is_forbidden_negative(item: dict[str, Any]) -> bool:
    if truthy(item.get("is_test_like")):
        return True
    label = " ".join(
        text(item.get(key)).casefold()
        for key in ("service_name", "api_name", "function_name", "api_description")
    )
    return bool(re.search(r"\b(?:demo|test|healthcheck|health check|ping endpoint)\b", label))


def filtered_catalog(
    static_services: dict[str, dict[str, Any]], static_apis: dict[str, dict[str, Any]]
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, list[str]]]:
    services = {
        key: item for key, item in static_services.items() if not is_forbidden_negative(item)
    }
    apis = {key: item for key, item in static_apis.items() if not is_forbidden_negative(item)}
    service_to_apis: dict[str, list[str]] = {}
    for key, item in apis.items():
        service_key = text(item.get("service_key"))
        if service_key:
            service_to_apis.setdefault(service_key, []).append(key)
    for key in service_to_apis:
        service_to_apis[key].sort()
    return services, apis, service_to_apis


def reconstruct_candidate_space(
    row: dict[str, Any],
    static_services: dict[str, dict[str, Any]],
    static_apis: dict[str, dict[str, Any]],
    service_to_apis: dict[str, list[str]],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    query = text(row.get("query_text"))
    gold_services = parse_json(row.get("provisional_gold_services_json"), [])
    gold_apis = parse_json(row.get("provisional_gold_apis_json"), [])
    gold_service_keys = {text(item.get("service_key")) for item in gold_services}
    gold_api_keys = {text(item.get("function_key")) for item in gold_apis}
    trace: dict[str, Any] = {
        "source_task_id": text(row.get("source_task_id")),
        "original_candidate_services_json": text(row.get("candidate_services_json")),
        "original_candidate_apis_json": text(row.get("candidate_apis_json")),
        "repaired_candidate_services_json": "[]",
        "repaired_candidate_apis_json": "[]",
        "negative_source": "static_catalog_sibling_domain_easy",
        "parent_mapping": "",
        "alias_check": "",
        "post_repair_service_candidate_validity": False,
        "post_repair_api_candidate_validity": False,
        "reconstruction_status": "OTHER_FAILURE",
    }
    if not gold_service_keys <= set(static_services) or not gold_api_keys <= set(static_apis):
        trace["reconstruction_status"] = "GOLD_NOT_IN_CATALOG"
        return None, trace
    if has_alias_conflict(gold_services, "service") or has_alias_conflict(gold_apis, "api"):
        trace["reconstruction_status"] = "ALIAS_CONFLICT"
        return None, trace

    service_candidates, service_meta = prep.build_service_candidates(
        query, gold_services, [], static_services
    )
    api_candidates, api_meta = prep.build_api_candidates(
        query, gold_apis, [], static_apis, service_to_apis
    )
    parent_missing = [
        item for item in api_candidates if not text(item.get("service_key")) or not text(item.get("service_name"))
    ]
    alias_conflict = has_alias_conflict(service_candidates, "service") or has_alias_conflict(api_candidates, "api")
    service_valid = (
        service_meta["status"] == "valid"
        and gold_service_keys < {text(item.get("service_key")) for item in service_candidates}
        and service_meta["negative_count"] > 0
    )
    api_valid = (
        api_meta["status"] == "valid"
        and gold_api_keys < {text(item.get("function_key")) for item in api_candidates}
        and api_meta["negative_count"] > 0
        and not parent_missing
    )
    trace.update(
        {
            "repaired_candidate_services_json": json_dumps(service_candidates),
            "repaired_candidate_apis_json": json_dumps(api_candidates),
            "parent_mapping": json_dumps(
                [
                    {
                        "function_key": text(item.get("function_key")),
                        "service_key": text(item.get("service_key")),
                        "mapping_source": text(item.get("catalog_source_path")),
                    }
                    for item in api_candidates
                ]
            ),
            "alias_check": "FAIL" if alias_conflict else "PASS",
            "post_repair_service_candidate_validity": service_valid,
            "post_repair_api_candidate_validity": api_valid,
            "service_negative_distractor_count": service_meta["negative_count"],
            "api_negative_distractor_count": api_meta["negative_count"],
        }
    )
    if parent_missing:
        trace["reconstruction_status"] = "PARENT_MAPPING_MISSING"
        return None, trace
    if alias_conflict:
        trace["reconstruction_status"] = "ALIAS_CONFLICT"
        return None, trace
    if not service_valid or not api_valid:
        trace["reconstruction_status"] = "CATALOG_INSUFFICIENT"
        return None, trace

    updated = dict(row)
    updated["candidate_services_json"] = json_dumps(service_candidates)
    updated["candidate_service_count"] = len(service_candidates)
    updated["service_negative_distractor_count"] = service_meta["negative_count"]
    updated["same_domain_service_negative_count"] = service_meta["same_domain_count"]
    updated["easy_service_negative_count"] = service_meta["easy_count"]
    updated["service_candidate_space_status"] = service_meta["status"]
    updated["service_candidate_construction_evidence_json"] = json_dumps(service_meta["evidence"])
    updated["candidate_apis_json"] = json_dumps(api_candidates)
    updated["candidate_api_count"] = len(api_candidates)
    updated["api_negative_distractor_count"] = api_meta["negative_count"]
    updated["same_service_sibling_negative_count"] = api_meta["same_service_count"]
    updated["same_domain_api_negative_count"] = api_meta["same_domain_count"]
    updated["easy_api_negative_count"] = api_meta["easy_count"]
    updated["api_candidate_space_status"] = api_meta["status"]
    updated["api_candidate_construction_evidence_json"] = json_dumps(api_meta["evidence"])
    updated["service_api_map_json"] = json_dumps(
        [
            {
                "function_name": text(item.get("function_name")),
                "api_name": text(item.get("api_name")),
                "service_name": text(item.get("service_name")),
                "function_key": text(item.get("function_key")),
                "service_key": text(item.get("service_key")),
                "mapping_source": text(item.get("catalog_source_path")),
            }
            for item in api_candidates
        ]
    )
    trace["reconstruction_status"] = "RECONSTRUCTED_VALID"
    return updated, trace


def complete_machine_revalidation(row: dict[str, Any]) -> dict[str, Any]:
    assessment = gate.assess_task(row)
    edges = parse_json(row.get("dependency_edges_json"), [])
    blocking = list(assessment["structural_ineligibility_reasons"])
    if not truthy(row.get("dependency_graph_is_dag")):
        blocking.append("dependency_graph_not_dag")
    for edge in edges:
        source_type = text(edge.get("edge_source_type"))
        if source_type in {"shared_input_only", "sequence_only"}:
            blocking.append("shared_input_or_sequence_edge_in_gold_dependency")
        if truthy(edge.get("value_present_in_original_query")):
            blocking.append("query_known_dependency_edge")
        if truthy(edge.get("upstream_output_is_echo")):
            blocking.append("echoed_input_dependency_edge")
        if text(edge.get("upstream_field_role")).casefold() in {"argument", "input", "request"}:
            blocking.append("invalid_upstream_field_role")
    if not text(row.get("source_trace_path")) or not text(row.get("dependency_evidence_json")):
        blocking.append("source_trace_or_evidence_unavailable")
    blocking = sorted(set(blocking))
    if "source_trace_or_evidence_unavailable" in blocking:
        status = "SOURCE_UNAVAILABLE_HOLD"
    elif blocking:
        status = "STRUCTURALLY_INELIGIBLE_AFTER_REPAIR"
    elif assessment["requires_human_semantic_review"] or assessment["requires_human_necessity_review"]:
        status = "STRUCTURALLY_ELIGIBLE_WITH_RISK"
    else:
        status = "AUTHORITATIVE_VALID"
    return {
        "machine_revalidation_status": status,
        "machine_blocking_rules": blocking,
        "machine_risk_flags": assessment["necessity_risk_reason"],
        "assessment": assessment,
    }


def recovery_review_hash(row: dict[str, Any]) -> str:
    payload = {
        "final_query": text(row.get("final_model_facing_query_text") or row.get("query_text")),
        "candidate_services": parse_json(row.get("candidate_services_json"), []),
        "provisional_gold_services": parse_json(row.get("provisional_gold_services_json"), []),
        "candidate_apis": parse_json(row.get("candidate_apis_json"), []),
        "provisional_gold_apis": parse_json(row.get("provisional_gold_apis_json"), []),
        "service_api_map": parse_json(row.get("service_api_map_json"), []),
        "dependency_edges": parse_json(row.get("dependency_edges_json"), []),
        "dependency_evidence": parse_json(row.get("dependency_evidence_json"), {}),
        "display_order": {
            "services": [text(item.get("service_key")) for item in parse_json(row.get("candidate_services_json"), [])],
            "apis": [text(item.get("function_key")) for item in parse_json(row.get("candidate_apis_json"), [])],
        },
        "machine_rule_spec_version": MACHINE_RULE_SPEC_VERSION,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
