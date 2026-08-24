"""Strict JSON parsers for ServiceDiscoveryBench LLM outputs."""
from __future__ import annotations

import json
from typing import Iterable


class OutputValidationError(ValueError):
    pass


def _loads(payload: str | dict) -> dict:
    if isinstance(payload, dict):
        return payload
    try:
        value = json.loads(payload)
    except Exception as exc:
        raise OutputValidationError(f"invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise OutputValidationError("top-level output must be a JSON object")
    return value


def _validate_ranking(values: object, candidate_ids: list[str]) -> list[str]:
    if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
        raise OutputValidationError("ranked_candidate_ids must be an array of strings")
    if len(values) != len(candidate_ids):
        raise OutputValidationError("ranking length must equal candidate count")
    if len(values) != len(set(values)):
        raise OutputValidationError("ranking contains duplicate candidate IDs")
    if set(values) != set(candidate_ids):
        missing = sorted(set(candidate_ids) - set(values))
        extra = sorted(set(values) - set(candidate_ids))
        raise OutputValidationError(f"ranking must be an exact permutation; missing={missing[:5]}, extra={extra[:5]}")
    return list(values)


def parse_ranking_only(payload: str | dict, candidate_ids: Iterable[str]) -> dict[str, list[str]]:
    ids = list(candidate_ids)
    obj = _loads(payload)
    if set(obj) != {"ranked_candidate_ids"}:
        raise OutputValidationError("ranking-only output must contain exactly ranked_candidate_ids")
    ranking = _validate_ranking(obj["ranked_candidate_ids"], ids)
    return {"ranked_candidate_ids": ranking}


def parse_ranking_and_selected_set(payload: str | dict, candidate_ids: Iterable[str]) -> dict[str, list[str]]:
    ids = list(candidate_ids)
    obj = _loads(payload)
    required = {"ranked_candidate_ids", "selected_candidate_ids"}
    if set(obj) != required:
        raise OutputValidationError("ranking+selection output must contain exactly ranked_candidate_ids and selected_candidate_ids")
    ranking = _validate_ranking(obj["ranked_candidate_ids"], ids)
    selected = obj["selected_candidate_ids"]
    if not isinstance(selected, list) or not all(isinstance(item, str) for item in selected):
        raise OutputValidationError("selected_candidate_ids must be an array of strings")
    if len(selected) != len(set(selected)):
        raise OutputValidationError("selected_candidate_ids contains duplicates")
    if not set(selected).issubset(set(ids)):
        raise OutputValidationError("selected_candidate_ids contains IDs outside the candidate list")
    return {"ranked_candidate_ids": ranking, "selected_candidate_ids": list(selected)}
