"""V9.0.1 key-only provider validation and deterministic no-network mock."""
from __future__ import annotations

import json
from typing import Any

TOP_LEVEL_KEYS = {"request_id", "prompt", "candidate_ids", "decoding_config", "timeout_seconds"}
MODEL_VISIBLE_KEYS = {"query", "task_type", "prediction_target", "candidate_documents", "instructions"}
CANDIDATE_DOCUMENT_KEYS = {"candidate_id", "document"}
FORBIDDEN_EXACT = {
    "reference_gold_ids", "acceptable_solutions", "retrieval_gold_recall",
    "retrieval_gold_completeness", "gold_count", "evaluation_truth", "truth",
    "source_path", "source_pointer", "identity_decision",
}
FORBIDDEN_PREFIXES = ("qa_", "reviewer", "split_membership")


def _forbidden_key(key: str) -> bool:
    value = key.casefold()
    return value in FORBIDDEN_EXACT or value.startswith(FORBIDDEN_PREFIXES)


def _scan_keys(value: Any, path: str = "$") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if _forbidden_key(str(key)):
                found.append(child_path)
            found.extend(_scan_keys(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_scan_keys(child, f"{path}[{index}]"))
    return found


def parse_prompt_input(prompt: str) -> dict[str, Any]:
    lines = prompt.splitlines()
    input_lines = [line for line in lines if line.startswith("INPUT_JSON=")]
    if len(input_lines) != 1:
        raise ValueError("prompt must contain exactly one INPUT_JSON line")
    try:
        payload = json.loads(input_lines[0][len("INPUT_JSON="):])
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid INPUT_JSON: {exc}") from exc
    if not isinstance(payload, dict) or set(payload) != MODEL_VISIBLE_KEYS:
        raise ValueError(f"invalid model-visible keys: {sorted(payload) if isinstance(payload, dict) else type(payload).__name__}")
    documents = payload.get("candidate_documents")
    if not isinstance(documents, list):
        raise ValueError("candidate_documents must be a list")
    for index, document in enumerate(documents):
        if not isinstance(document, dict) or set(document) != CANDIDATE_DOCUMENT_KEYS:
            raise ValueError(f"invalid candidate document keys at {index}")
    forbidden = _scan_keys(payload)
    if forbidden:
        raise ValueError(f"forbidden structured key paths: {forbidden}")
    return payload


def validate_provider_request(request: dict[str, Any]) -> list[str]:
    if not isinstance(request, dict) or set(request) != TOP_LEVEL_KEYS:
        extra = sorted(set(request) - TOP_LEVEL_KEYS) if isinstance(request, dict) else []
        missing = sorted(TOP_LEVEL_KEYS - set(request)) if isinstance(request, dict) else sorted(TOP_LEVEL_KEYS)
        raise ValueError(f"invalid provider top-level contract: extra={extra}, missing={missing}")
    forbidden = _scan_keys(request)
    if forbidden:
        raise ValueError(f"forbidden structured key paths: {forbidden}")
    payload = parse_prompt_input(str(request["prompt"]))
    candidate_ids = request["candidate_ids"]
    if not isinstance(candidate_ids, list) or not all(isinstance(value, str) for value in candidate_ids):
        raise ValueError("candidate_ids must be list[str]")
    prompt_ids = [document["candidate_id"] for document in payload["candidate_documents"]]
    if candidate_ids != prompt_ids:
        raise ValueError("candidate_ids do not exactly match prompt candidate order")
    return sorted(request)


def mock_generate(*, request_id: str, prompt: str, candidate_ids: list[str],
                  decoding_config: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
    request = {"request_id": request_id, "prompt": prompt, "candidate_ids": candidate_ids,
               "decoding_config": decoding_config, "timeout_seconds": timeout_seconds}
    validate_provider_request(request)
    return {"ranked_candidate_ids": list(candidate_ids), "selected_candidate_ids": list(candidate_ids[:1])}
