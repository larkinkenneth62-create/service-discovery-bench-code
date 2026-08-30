from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


TOP5_RANKING_V1 = "TOP5_RANKING_V1"
SELECTED_SET_V1 = "SELECTED_SET_V1"
RANKING_AND_SELECTED_SET_V1_10 = "RANKING_AND_SELECTED_SET_V1_10"


@dataclass(frozen=True)
class ParseResult:
    valid: bool
    data: dict[str, list[str]] | None = None
    error_code: str | None = None
    error_message: str | None = None
    reasoning_present: bool = False


def _content(response: Any) -> tuple[str | None, bool, ParseResult | None]:
    choices = response.get("choices") if isinstance(response, dict) else None
    if not isinstance(choices, list) or len(choices) != 1:
        return None, False, ParseResult(False, error_code="CHOICE_COUNT", error_message="exactly one choice required")
    choice = choices[0]
    message = choice.get("message") if isinstance(choice, dict) else None
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        return None, False, ParseResult(False, error_code="MISSING_ANSWER_CONTENT", error_message="message.content must be text")
    reasoning_present = "reasoning_content" in message or "reasoning" in message
    content = message["content"]
    if not content.strip():
        return None, reasoning_present, ParseResult(
            False,
            error_code="EMPTY_ANSWER_CONTENT",
            error_message="message.content must contain one complete JSON document",
            reasoning_present=reasoning_present,
        )
    if content != content.strip():
        return None, reasoning_present, ParseResult(
            False,
            error_code="NON_JSON_WRAPPER",
            error_message="message.content must be the complete JSON document without surrounding text",
            reasoning_present=reasoning_present,
        )
    return content, reasoning_present, None


def _strict_object(
    response: Any, required_fields: set[str]
) -> tuple[dict[str, list[str]] | None, bool, ParseResult | None]:
    content, reasoning_present, error = _content(response)
    if error is not None:
        return None, reasoning_present, error
    try:
        value = json.loads(content)
    except json.JSONDecodeError as exc:
        return None, reasoning_present, ParseResult(
            False,
            error_code="INVALID_JSON",
            error_message=f"line {exc.lineno} column {exc.colno}: {exc.msg}",
            reasoning_present=reasoning_present,
        )
    if not isinstance(value, dict):
        return None, reasoning_present, ParseResult(False, error_code="TOP_LEVEL_NOT_OBJECT", error_message="top level must be an object", reasoning_present=reasoning_present)
    if set(value) != required_fields:
        return None, reasoning_present, ParseResult(
            False,
            error_code="UNEXPECTED_TOP_LEVEL_FIELD",
            error_message=f"top-level fields must equal {sorted(required_fields)!r}",
            reasoning_present=reasoning_present,
        )
    result: dict[str, list[str]] = {}
    for field in sorted(required_fields):
        items = value[field]
        if not isinstance(items, list):
            return None, reasoning_present, ParseResult(False, error_code="FIELD_NOT_ARRAY", error_message=f"{field} must be an array", reasoning_present=reasoning_present)
        if any(not isinstance(item, str) for item in items):
            return None, reasoning_present, ParseResult(False, error_code="NON_STRING_CANDIDATE_ID", error_message=f"all {field} entries must be strings", reasoning_present=reasoning_present)
        if len(items) != len(set(items)):
            return None, reasoning_present, ParseResult(False, error_code="DUPLICATE_CANDIDATE_ID", error_message=f"{field} candidate IDs must be unique", reasoning_present=reasoning_present)
        result[field] = items
    return result, reasoning_present, None


def _validate_pool(items: list[str], candidate_ids: list[str], reasoning_present: bool) -> ParseResult | None:
    pool = set(candidate_ids)
    outside = [item for item in items if item not in pool]
    if outside:
        return ParseResult(False, error_code="UNKNOWN_CANDIDATE_ID", error_message=f"candidate IDs outside pool: {outside[:3]}", reasoning_present=reasoning_present)
    return None


def parse_topk_response(response: Any, candidate_ids: list[str], expected_k: int) -> ParseResult:
    if expected_k != min(5, len(candidate_ids)) or expected_k < 1:
        return ParseResult(False, error_code="INVALID_EXPECTED_K", error_message="expected_k must equal min(5, candidate_count)")
    value, reasoning_present, error = _strict_object(response, {"ranked_candidate_ids"})
    if error is not None:
        return error
    assert value is not None
    items = value["ranked_candidate_ids"]
    if len(items) != expected_k:
        return ParseResult(False, error_code="TOPK_LENGTH_MISMATCH", error_message=f"expected {expected_k} IDs, got {len(items)}", reasoning_present=reasoning_present)
    pool_error = _validate_pool(items, candidate_ids, reasoning_present)
    if pool_error is not None:
        return pool_error
    return ParseResult(True, value, reasoning_present=reasoning_present)


def parse_selected_set_response(response: Any, candidate_ids: list[str]) -> ParseResult:
    value, reasoning_present, error = _strict_object(response, {"selected_candidate_ids"})
    if error is not None:
        return error
    assert value is not None
    pool_error = _validate_pool(value["selected_candidate_ids"], candidate_ids, reasoning_present)
    if pool_error is not None:
        return pool_error
    return ParseResult(True, value, reasoning_present=reasoning_present)


def parse_ranking_and_selected_set_response(response: Any, candidate_ids: list[str]) -> ParseResult:
    value, reasoning_present, error = _strict_object(
        response, {"ranked_candidate_ids", "selected_candidate_ids"}
    )
    if error is not None:
        return error
    assert value is not None
    expected_k = min(5, len(candidate_ids))
    if len(value["ranked_candidate_ids"]) != expected_k:
        return ParseResult(
            False,
            error_code="TOPK_LENGTH_MISMATCH",
            error_message=f"expected {expected_k} ranked IDs, got {len(value['ranked_candidate_ids'])}",
            reasoning_present=reasoning_present,
        )
    for field in ("ranked_candidate_ids", "selected_candidate_ids"):
        pool_error = _validate_pool(value[field], candidate_ids, reasoning_present)
        if pool_error is not None:
            return pool_error
    return ParseResult(True, value, reasoning_present=reasoning_present)


def contract_for(track: str, task_type: str) -> str:
    if track == "machine":
        return TOP5_RANKING_V1
    if track not in {"native", "smoke"}:
        raise ValueError(f"unsupported track: {track}")
    if task_type == "single_service_discovery":
        return TOP5_RANKING_V1
    if task_type == "single_api_recommendation":
        return RANKING_AND_SELECTED_SET_V1_10
    if task_type.startswith("multi_") or task_type.startswith("composable_"):
        return SELECTED_SET_V1
    raise ValueError(f"unregistered task_type: {task_type}")
