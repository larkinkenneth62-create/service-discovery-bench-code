from __future__ import annotations

import hashlib
import json

from .normalize import normalize_text


SIGNATURE_VERSION = "sha256_nfkc_json_v1"
REVIEW_FIELDS = (
    "prediction_target",
    "task_type",
    "query_text",
    "user_visible_context_json",
    "candidate_services_json",
    "candidate_apis_json",
    "gold_services_json",
    "gold_apis_json",
    "acceptable_gold_service_sets_json",
    "acceptable_gold_api_sets_json",
    "service_api_map_json",
    "dependency_graph_json",
    "dependency_evidence_json",
)


def _json_value(value):
    if isinstance(value, str):
        text = value.strip()
        if text and text[0] in "[{":
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                pass
        return normalize_text(value)
    return value


def stable_hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def query_signature(query_text: str) -> str:
    return stable_hash({"query_text": normalize_text(query_text, casefold=True)})


def task_signature(row: dict) -> str:
    return stable_hash({field: _json_value(row.get(field, "")) for field in REVIEW_FIELDS if field != "query_text"})


def review_content_fingerprint(row: dict) -> str:
    return stable_hash({field: _json_value(row.get(field, "")) for field in REVIEW_FIELDS})
