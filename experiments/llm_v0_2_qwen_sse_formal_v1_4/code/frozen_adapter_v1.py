from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import threading
import time
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import httpx


MODEL = "Qwen3.6-35B-A3B-APEX-I-Compact.gguf"
DEFAULT_BASE_URL = "https://protect-told-tab-resistant.trycloudflare.com/v1"
KEY_ENV_NAMES = [
    "QWEN_C6_API_KEY_STUDENT01",
    "QWEN_C6_API_KEY_STUDENT02",
    "QWEN_C6_API_KEY_STUDENT03",
    "QWEN_C6_API_KEY_STUDENT04",
]
SYSTEM_PROMPT = "You are a deterministic candidate-ranking engine. Return only JSON matching the supplied schema."
COMPACT_SYSTEM_PROMPT = (
    "You are a deterministic candidate-ranking engine. Candidate aliases are arbitrary identifiers. "
    "Decode field codes using the supplied legend and return only the required JSON object."
)
INFRA_STATUS = {408, 425, 429}
TERMINAL = {"succeeded", "parse_failure", "api_error"}
FENCE_RE = re.compile(r"^```(?:json)?[ \t]*\r?\n(?P<body>.*)\r?\n```$", re.DOTALL | re.IGNORECASE)
COMPACT_FIELD_NAMES = (
    "level", "parent_service_name", "canonical_name", "provider_or_host", "operation_id",
    "http_method", "endpoint_path", "capability_description", "input_summary", "output_summary",
    "api_version", "constraints_or_limitations", "source_dataset_label",
)
COMPACT_FIELD_CODES = {name: format(index, "x") for index, name in enumerate(COMPACT_FIELD_NAMES)}
COMPACT_FIELD_LEGEND = {code: name for name, code in COMPACT_FIELD_CODES.items()}
CORE_V3_KEEP_FIELDS = {
    "level", "parent_service_name", "canonical_name", "provider_or_host", "operation_id",
    "http_method", "endpoint_path", "capability_description", "input_summary", "api_version",
}
CORE_V3_CHAR_LIMITS = {"capability_description": 256, "input_summary": 128}
IDENTITY_V4_KEEP_FIELDS = {
    "parent_service_name", "canonical_name", "provider_or_host", "operation_id",
    "http_method", "endpoint_path", "capability_description",
}
IDENTITY_V4_CHAR_LIMITS = {"capability_description": 128}
BOUNDED_V5_KEEP_FIELDS = CORE_V3_KEEP_FIELDS
BOUNDED_V5_CHAR_LIMITS = {"capability_description": 256, "input_summary": 128}
SERIALIZED_FIELD_RE = re.compile(
    r"(?<!\S)(" + "|".join(re.escape(name) for name in COMPACT_FIELD_NAMES) + r"): "
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)[:100] + "-" + sha256_text(value)[:16]


def load_keys() -> list[str]:
    keys = [os.environ.get(name, "") for name in KEY_ENV_NAMES]
    keys = [key for key in keys if key]
    fallback = os.environ.get("SDB_QWEN_API_KEY", "")
    if fallback and fallback not in keys:
        keys.append(fallback)
    if not keys:
        raise SystemExit("NO_QWEN_KEY: set QWEN_C6_API_KEY_STUDENT01..04 or SDB_QWEN_API_KEY")
    if len(keys) != len(set(keys)):
        raise SystemExit("duplicate Qwen API keys are not allowed")
    return keys


@dataclass(frozen=True)
class ParseResult:
    valid: bool
    data: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    reasoning_present: bool = False


def parse_response(response: Any, candidate_ids: list[str], require_selected: bool) -> ParseResult:
    try:
        choices = response.get("choices") if isinstance(response, dict) else None
        if not isinstance(choices, list) or len(choices) != 1:
            return ParseResult(False, error_code="MISSING_ANSWER_CONTENT", error_message="exactly one choice required")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            return ParseResult(False, error_code="MISSING_ANSWER_CONTENT", error_message="message.content must be text")
        content = message["content"].strip()
        reasoning_present = "reasoning_content" in message or "reasoning" in message
        if "```" in content:
            match = FENCE_RE.fullmatch(content)
            if not match:
                return ParseResult(False, error_code="INVALID_JSON", error_message="code fence must wrap the complete JSON answer")
            content = match.group("body").strip()
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            return ParseResult(False, error_code="INVALID_JSON", error_message=f"line {exc.lineno} column {exc.colno}: {exc.msg}", reasoning_present=reasoning_present)
        if not isinstance(data, dict):
            return ParseResult(False, error_code="SCHEMA_VALIDATION_FAILED", error_message="top level must be an object", reasoning_present=reasoning_present)
        expected_keys = {"ranked_candidate_ids"}
        if require_selected:
            expected_keys.add("selected_candidate_ids")
        if "selected_candidate_ids" in data and not require_selected:
            return ParseResult(False, error_code="UNEXPECTED_SELECTED_FIELD", error_message="selected_candidate_ids is forbidden for this request", reasoning_present=reasoning_present)
        if set(data) != expected_keys:
            return ParseResult(False, error_code="UNEXPECTED_TOP_LEVEL_FIELD", error_message=f"expected fields {sorted(expected_keys)}", reasoning_present=reasoning_present)
        ranking = data.get("ranked_candidate_ids")
        if not isinstance(ranking, list):
            return ParseResult(False, error_code="RANKING_NOT_ARRAY", error_message="ranked_candidate_ids must be an array", reasoning_present=reasoning_present)
        if any(not isinstance(item, str) for item in ranking):
            return ParseResult(False, error_code="NON_STRING_CANDIDATE_ID", error_message="ranking entries must be strings", reasoning_present=reasoning_present)
        if len(ranking) < len(candidate_ids):
            return ParseResult(False, error_code="INCOMPLETE_RANKING", error_message=f"expected {len(candidate_ids)} ids, got {len(ranking)}", reasoning_present=reasoning_present)
        if len(ranking) > len(candidate_ids):
            return ParseResult(False, error_code="RANKING_LENGTH_MISMATCH", error_message=f"expected {len(candidate_ids)} ids, got {len(ranking)}", reasoning_present=reasoning_present)
        if len(ranking) != len(set(ranking)):
            return ParseResult(False, error_code="DUPLICATE_CANDIDATE_ID", error_message="ranking contains duplicate IDs", reasoning_present=reasoning_present)
        pool = set(candidate_ids); ranked = set(ranking)
        unknown = sorted(ranked - pool)
        if unknown:
            return ParseResult(False, error_code="UNKNOWN_CANDIDATE_ID", error_message=f"unknown IDs: {unknown[:3]}", reasoning_present=reasoning_present)
        missing = sorted(pool - ranked)
        if missing:
            return ParseResult(False, error_code="MISSING_CANDIDATE_ID", error_message=f"missing IDs: {missing[:3]}", reasoning_present=reasoning_present)
        if require_selected:
            selected = data.get("selected_candidate_ids")
            if not isinstance(selected, list):
                return ParseResult(False, error_code="SELECTED_NOT_ARRAY", error_message="selected_candidate_ids must be an array", reasoning_present=reasoning_present)
            if any(not isinstance(item, str) for item in selected):
                return ParseResult(False, error_code="NON_STRING_SELECTED_CANDIDATE_ID", error_message="selected entries must be strings", reasoning_present=reasoning_present)
            if len(selected) != len(set(selected)):
                return ParseResult(False, error_code="DUPLICATE_SELECTED_CANDIDATE_ID", error_message="selected set contains duplicates", reasoning_present=reasoning_present)
            outside = sorted(set(selected) - pool)
            if outside:
                return ParseResult(False, error_code="UNKNOWN_SELECTED_CANDIDATE_ID", error_message=f"selected IDs outside pool: {outside[:3]}", reasoning_present=reasoning_present)
        return ParseResult(True, data=data, reasoning_present=reasoning_present)
    except Exception as exc:
        return ParseResult(False, error_code="PARSER_INTERNAL_ERROR", error_message=f"{type(exc).__name__}: {exc}")


@dataclass
class RequestItem:
    request_id: str
    track: str
    task_type: str
    prediction_target: str
    candidate_ids: list[str]
    require_selected: bool
    payload: dict[str, Any]
    source_row_sha256: str
    candidate_order_sha256: str
    alias_to_candidate_id: dict[str, str] | None = None
    transport_adapter: str = "DIRECT_IDS_V1"
    parser_contract: str = "STRICT_CONTEXTUAL_FULL_PERMUTATION_V1"

    @property
    def request_sha256(self) -> str:
        return sha256_text(stable_json({
            "payload": self.payload,
            "candidate_ids": self.candidate_ids,
            "require_selected": self.require_selected,
            "source_row_sha256": self.source_row_sha256,
            "parser_contract": self.parser_contract,
            "transport_adapter": self.transport_adapter,
        }))


@dataclass
class ProviderHTTPResult:
    status_code: int
    headers: dict[str, str]
    body: Any
    stream_event_count: int | None = None
    stream_events_sha256: str | None = None


def unified_payload(row: dict[str, Any], model: str) -> dict[str, Any]:
    visible = {
        "query": row["query_text"],
        "task_type": row["task_type"],
        "prediction_target": row["prediction_target"],
        "candidate_documents": row["candidate_documents"],
        "instructions": "Rank all supplied candidate IDs from most to least relevant. Return only the required JSON object.",
        "output_schema": row["output_schema"],
    }
    prompt = "SETTING=unified_v0_2_formal_v2\nINPUT_JSON=" + stable_json(visible) + "\n"
    return {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"}, "temperature": 0, "top_p": 1,
        "n": 1, "seed": 0, "max_tokens": 4096, "stream": False,
    }


def compact_encode_document(document: str) -> list[str]:
    """Losslessly replace repeated serializer field names with short codes."""
    encoded: list[str] = []
    for line in document.split("\n"):
        matched = False
        for field_name, field_code in COMPACT_FIELD_CODES.items():
            prefix = field_name + ": "
            if line.startswith(prefix):
                encoded.append(field_code + "=" + line[len(prefix):])
                matched = True
                break
        if not matched:
            encoded.append("=" + line)
    return encoded


def compact_decode_document(encoded_lines: list[str]) -> str:
    decoded: list[str] = []
    for encoded in encoded_lines:
        if not isinstance(encoded, str) or "=" not in encoded:
            raise ValueError("invalid compact document line")
        field_code, value = encoded.split("=", 1)
        if field_code:
            if field_code not in COMPACT_FIELD_LEGEND:
                raise ValueError(f"unknown compact field code: {field_code}")
            decoded.append(COMPACT_FIELD_LEGEND[field_code] + ": " + value)
        else:
            decoded.append(value)
    return "\n".join(decoded)


def compact_core_v3_document(document: str) -> str:
    """Deterministic tunnel-safe model-visible serializer revision.

    It keeps identity, operation and capability fields, with pre-registered character
    limits on the two verbose semantic fields. The original document remains in the
    frozen manifest and is never overwritten.
    """
    kept: list[str] = []
    for line in document.split("\n"):
        if ": " not in line:
            continue
        field_name, value = line.split(": ", 1)
        if field_name not in CORE_V3_KEEP_FIELDS:
            continue
        limit = CORE_V3_CHAR_LIMITS.get(field_name)
        if limit is not None and len(value) > limit:
            value = value[:limit]
        kept.append(field_name + ": " + value)
    return "\n".join(kept)


def compact_identity_v4_document(document: str) -> str:
    """Deterministic identity-and-core-capability serializer for the 120 s tunnel."""
    kept: list[str] = []
    for line in document.split("\n"):
        if ": " not in line:
            continue
        field_name, value = line.split(": ", 1)
        if field_name not in IDENTITY_V4_KEEP_FIELDS:
            continue
        limit = IDENTITY_V4_CHAR_LIMITS.get(field_name)
        if limit is not None and len(value) > limit:
            value = value[:limit]
        kept.append(field_name + ": " + value)
    return "\n".join(kept)


def compact_bounded_v5_document(document: str) -> str:
    """Parse newline- or space-separated serializer fields and apply registered bounds."""
    matches = list(SERIALIZED_FIELD_RE.finditer(document))
    if not matches:
        return ""
    kept: list[str] = []
    for index, match in enumerate(matches):
        field_name = match.group(1)
        if field_name not in BOUNDED_V5_KEEP_FIELDS:
            continue
        value_start = match.end()
        value_end = matches[index + 1].start() if index + 1 < len(matches) else len(document)
        value = document[value_start:value_end].strip()
        limit = BOUNDED_V5_CHAR_LIMITS.get(field_name)
        if limit is not None and len(value) > limit:
            value = value[:limit]
        kept.append(field_name + ": " + value)
    return "\n".join(kept)


def compact_payload(
    *, query: str, task_type: str, prediction_target: str,
    candidate_documents: list[dict[str, Any]], candidate_ids: list[str],
    require_selected: bool, model: str, core_v3: bool = False, identity_v4: bool = False,
    bounded_v5: bool = False,
) -> tuple[dict[str, Any], dict[str, str]]:
    if len(candidate_documents) != len(candidate_ids):
        raise ValueError("candidate document count differs from candidate ID count")
    alias_to_candidate_id: dict[str, str] = {}
    candidate_rows: list[list[Any]] = []
    for index, (document_row, candidate_id) in enumerate(zip(candidate_documents, candidate_ids, strict=True)):
        if document_row.get("candidate_id") != candidate_id:
            raise ValueError(f"candidate document order mismatch at index {index}")
        document = document_row.get("document")
        if not isinstance(document, str):
            raise ValueError(f"candidate document is not text at index {index}")
        if core_v3:
            document = compact_core_v3_document(document)
        elif identity_v4:
            document = compact_identity_v4_document(document)
        elif bounded_v5:
            document = compact_bounded_v5_document(document)
        alias = f"C{index:03d}"
        alias_to_candidate_id[alias] = candidate_id
        candidate_rows.append([alias, compact_encode_document(document)])
    required = ["ranked_aliases"] + (["selected_aliases"] if require_selected else [])
    visible = {
        "query": query,
        "task_type": task_type,
        "prediction_target": prediction_target,
        "field_code_legend": COMPACT_FIELD_LEGEND,
        "candidate_rows": candidate_rows,
        "instructions": (
            "Rank every candidate alias from most to least relevant. Use every alias exactly once. "
            + ("Return selected_aliases as a duplicate-free subset. " if require_selected else "Do not return a selected set. ")
            + "Aliases are positional and must not be rewritten."
        ),
        "output_schema": {
            "type": "object", "additionalProperties": False, "required": required,
            "properties": {
                "ranked_aliases": {"type": "array", "items": {"type": "string"},
                                   "minItems": len(candidate_ids), "maxItems": len(candidate_ids), "uniqueItems": True},
                **({"selected_aliases": {"type": "array", "items": {"type": "string"}, "uniqueItems": True}}
                   if require_selected else {}),
            },
        },
    }
    setting = (
        "tunnel_compact_alias_core_v3" if core_v3
        else "tunnel_compact_alias_identity_v4" if identity_v4
        else "tunnel_compact_alias_inline_bounded_v5" if bounded_v5
        else "tunnel_compact_alias_v2"
    )
    prompt = f"SETTING={setting}\nINPUT_JSON=" + stable_json(visible) + "\n"
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": COMPACT_SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"}, "temperature": 0, "top_p": 1,
        "n": 1, "seed": 0, "max_tokens": 4096, "stream": False,
    }
    return payload, alias_to_candidate_id


def parse_compact_response(
    response: Any, alias_to_candidate_id: dict[str, str], candidate_ids: list[str], require_selected: bool,
) -> ParseResult:
    try:
        choices = response.get("choices") if isinstance(response, dict) else None
        if not isinstance(choices, list) or len(choices) != 1:
            return ParseResult(False, error_code="MISSING_ANSWER_CONTENT", error_message="exactly one choice required")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            return ParseResult(False, error_code="MISSING_ANSWER_CONTENT", error_message="message.content must be text")
        content = message["content"].strip()
        reasoning_present = "reasoning_content" in message or "reasoning" in message
        if "```" in content:
            match = FENCE_RE.fullmatch(content)
            if not match:
                return ParseResult(False, error_code="INVALID_JSON", error_message="code fence must wrap the complete JSON answer")
            content = match.group("body").strip()
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            return ParseResult(False, error_code="INVALID_JSON", error_message=f"line {exc.lineno} column {exc.colno}: {exc.msg}", reasoning_present=reasoning_present)
        if not isinstance(data, dict):
            return ParseResult(False, error_code="SCHEMA_VALIDATION_FAILED", error_message="top level must be an object", reasoning_present=reasoning_present)
        expected = {"ranked_aliases"} | ({"selected_aliases"} if require_selected else set())
        if "selected_aliases" in data and not require_selected:
            return ParseResult(False, error_code="UNEXPECTED_SELECTED_FIELD", error_message="selected_aliases is forbidden", reasoning_present=reasoning_present)
        if set(data) != expected:
            return ParseResult(False, error_code="UNEXPECTED_TOP_LEVEL_FIELD", error_message=f"expected fields {sorted(expected)}", reasoning_present=reasoning_present)
        aliases = data.get("ranked_aliases")
        if not isinstance(aliases, list):
            return ParseResult(False, error_code="RANKING_NOT_ARRAY", error_message="ranked_aliases must be an array", reasoning_present=reasoning_present)
        if any(not isinstance(alias, str) for alias in aliases):
            return ParseResult(False, error_code="NON_STRING_CANDIDATE_ID", error_message="ranking aliases must be strings", reasoning_present=reasoning_present)
        if len(aliases) < len(alias_to_candidate_id):
            return ParseResult(False, error_code="INCOMPLETE_RANKING", error_message=f"expected {len(alias_to_candidate_id)} aliases, got {len(aliases)}", reasoning_present=reasoning_present)
        if len(aliases) > len(alias_to_candidate_id):
            return ParseResult(False, error_code="RANKING_LENGTH_MISMATCH", error_message=f"expected {len(alias_to_candidate_id)} aliases, got {len(aliases)}", reasoning_present=reasoning_present)
        if len(aliases) != len(set(aliases)):
            return ParseResult(False, error_code="DUPLICATE_CANDIDATE_ID", error_message="ranking contains duplicate aliases", reasoning_present=reasoning_present)
        unknown = sorted(set(aliases) - set(alias_to_candidate_id))
        if unknown:
            return ParseResult(False, error_code="UNKNOWN_CANDIDATE_ID", error_message=f"unknown aliases: {unknown[:3]}", reasoning_present=reasoning_present)
        canonical: dict[str, Any] = {"ranked_candidate_ids": [alias_to_candidate_id[alias] for alias in aliases]}
        if require_selected:
            selected = data.get("selected_aliases")
            if not isinstance(selected, list):
                return ParseResult(False, error_code="SELECTED_NOT_ARRAY", error_message="selected_aliases must be an array", reasoning_present=reasoning_present)
            if any(not isinstance(alias, str) for alias in selected):
                return ParseResult(False, error_code="NON_STRING_SELECTED_CANDIDATE_ID", error_message="selected aliases must be strings", reasoning_present=reasoning_present)
            if len(selected) != len(set(selected)):
                return ParseResult(False, error_code="DUPLICATE_SELECTED_CANDIDATE_ID", error_message="selected aliases contain duplicates", reasoning_present=reasoning_present)
            unknown_selected = sorted(set(selected) - set(alias_to_candidate_id))
            if unknown_selected:
                return ParseResult(False, error_code="UNKNOWN_SELECTED_CANDIDATE_ID", error_message=f"unknown selected aliases: {unknown_selected[:3]}", reasoning_present=reasoning_present)
            canonical["selected_candidate_ids"] = [alias_to_candidate_id[alias] for alias in selected]
        canonical_response = {
            "choices": [{"message": {"content": stable_json(canonical), **({"reasoning_content": "present"} if reasoning_present else {})}}]
        }
        parsed = parse_response(canonical_response, candidate_ids, require_selected)
        return ParseResult(parsed.valid, parsed.data, parsed.error_code, parsed.error_message, reasoning_present)
    except Exception as exc:
        return ParseResult(False, error_code="PARSER_INTERNAL_ERROR", error_message=f"{type(exc).__name__}: {exc}")


def legacy_payload(row: dict[str, Any], model: str) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": row["prompt"]}],
        "response_format": {"type": "json_object"}, "temperature": 0, "top_p": 1,
        "n": 1, "seed": 0, "max_tokens": 4096, "stream": False,
    }


def iter_formal(
    path: Path, track: str, model: str, compact_alias_v2: bool = False, compact_core_v3: bool = False,
    compact_identity_v4: bool = False, compact_bounded_v5: bool = False,
) -> Iterable[RequestItem]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            candidate_ids = row["candidate_ids"]
            if not isinstance(candidate_ids, list) or not candidate_ids or len(candidate_ids) != len(set(candidate_ids)):
                raise ValueError(f"invalid candidate pool in {track}/{row.get('benchmark_task_id')}")
            alias_to_candidate_id = None
            transport_adapter = "DIRECT_IDS_V1"
            if compact_alias_v2 or compact_core_v3 or compact_identity_v4 or compact_bounded_v5:
                visible = row if track == "unified" else row["model_visible_input"]
                payload, alias_to_candidate_id = compact_payload(
                    query=visible["query_text"] if track == "unified" else visible["query"],
                    task_type=row["task_type"], prediction_target=row["prediction_target"],
                    candidate_documents=visible["candidate_documents"], candidate_ids=candidate_ids,
                    require_selected=False if track == "unified" else bool(row["require_selected"]), model=model,
                    core_v3=compact_core_v3, identity_v4=compact_identity_v4,
                    bounded_v5=compact_bounded_v5,
                )
                require_selected = False if track == "unified" else bool(row["require_selected"])
                transport_adapter = (
                    "TUNNEL_COMPACT_ALIAS_CORE_V3" if compact_core_v3
                    else "TUNNEL_COMPACT_ALIAS_IDENTITY_V4" if compact_identity_v4
                    else "TUNNEL_COMPACT_ALIAS_INLINE_BOUNDED_V5" if compact_bounded_v5
                    else "TUNNEL_COMPACT_ALIAS_V2"
                )
            elif track == "unified":
                if "selected_candidate_ids" in line:
                    raise ValueError("Unified V2 input contains selected_candidate_ids")
                payload = unified_payload(row, model); require_selected = False
            else:
                payload = legacy_payload(row, model); require_selected = bool(row["require_selected"])
            if len(stable_json(payload).encode("utf-8")) > 8 * 1024 * 1024:
                raise ValueError(f"request body exceeds 8MB: {row['benchmark_task_id']}")
            yield RequestItem(
                request_id=row["benchmark_task_id"], track=track, task_type=row["task_type"],
                prediction_target=row["prediction_target"], candidate_ids=candidate_ids,
                require_selected=require_selected, payload=payload, source_row_sha256=sha256_text(stable_json(row)),
                candidate_order_sha256=row.get("candidate_order_hash") or sha256_text("\n".join(candidate_ids)),
                alias_to_candidate_id=alias_to_candidate_id, transport_adapter=transport_adapter,
            )


def iter_smoke(
    path: Path, model: str, compact_alias_v2: bool = False, compact_core_v3: bool = False,
    compact_identity_v4: bool = False, compact_bounded_v5: bool = False,
) -> Iterable[RequestItem]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip(): continue
            row = json.loads(line)
            messages = row["messages"]
            user = next(message["content"] for message in messages if message.get("role") == "user")
            if "INPUT_JSON=" not in user:
                raise ValueError(f"smoke row lacks INPUT_JSON: {row['request_id']}")
            visible = json.loads(user.split("INPUT_JSON=", 1)[1])
            documents = visible["candidate_documents"]
            candidate_ids = [item["candidate_id"] for item in documents]
            schema = visible.get("output_schema", {})
            require_selected = "selected_candidate_ids" in schema.get("required", [])
            alias_to_candidate_id = None
            transport_adapter = "DIRECT_IDS_V1"
            if compact_alias_v2 or compact_core_v3 or compact_identity_v4 or compact_bounded_v5:
                payload, alias_to_candidate_id = compact_payload(
                    query=visible["query"], task_type=row["task_type"], prediction_target=row["prediction_target"],
                    candidate_documents=documents, candidate_ids=candidate_ids,
                    require_selected=require_selected, model=model, core_v3=compact_core_v3,
                    identity_v4=compact_identity_v4,
                    bounded_v5=compact_bounded_v5,
                )
                transport_adapter = (
                    "TUNNEL_COMPACT_ALIAS_CORE_V3" if compact_core_v3
                    else "TUNNEL_COMPACT_ALIAS_IDENTITY_V4" if compact_identity_v4
                    else "TUNNEL_COMPACT_ALIAS_INLINE_BOUNDED_V5" if compact_bounded_v5
                    else "TUNNEL_COMPACT_ALIAS_V2"
                )
            else:
                payload = {key: row[key] for key in (
                    "messages", "response_format", "temperature", "top_p", "n", "seed", "max_tokens", "stream"
                ) if key in row}
                payload["model"] = model; payload["stream"] = False
            yield RequestItem(
                request_id=row["request_id"], track="smoke", task_type=row["task_type"],
                prediction_target=row["prediction_target"], candidate_ids=candidate_ids,
                require_selected=require_selected, payload=payload, source_row_sha256=sha256_text(stable_json(row)),
                candidate_order_sha256=sha256_text("\n".join(candidate_ids)),
                alias_to_candidate_id=alias_to_candidate_id, transport_adapter=transport_adapter,
            )


class KeySlot:
    def __init__(self, key: str, per_key_concurrency: int = 4) -> None:
        self.key = key
        self.semaphore = threading.BoundedSemaphore(per_key_concurrency)
        self.rate_lock = threading.Lock()
        self.starts: deque[float] = deque()
        self.client = httpx.Client(timeout=None, trust_env=False, http2=False, follow_redirects=True)

    def rate_wait(self) -> None:
        while True:
            with self.rate_lock:
                now = time.monotonic()
                while self.starts and now - self.starts[0] >= 60:
                    self.starts.popleft()
                if len(self.starts) < 60:
                    self.starts.append(now)
                    return
                wait = max(0.05, 60 - (now - self.starts[0]))
            time.sleep(wait)

    def close(self) -> None:
        self.client.close()


class FormalRunner:
    def __init__(self, base_url: str, keys: list[str], output_dir: Path, concurrency: int, timeout_seconds: float, max_retries: int) -> None:
        self.base_url = base_url.rstrip("/")
        self.slots = [KeySlot(key) for key in keys]
        self.output_dir = output_dir
        self.raw_dir = output_dir / "raw"; self.parsed_dir = output_dir / "parsed"
        self.status_path = output_dir / "REQUEST_STATUS.jsonl"
        self.summary_path = output_dir / "RUN_SUMMARY.json"
        self.concurrency = min(concurrency, len(keys) * 4)
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.write_lock = threading.Lock()
        self.completed_counter = 0
        self.started_at = time.perf_counter()

    def close(self) -> None:
        for slot in self.slots: slot.close()

    def existing(self) -> dict[str, dict[str, Any]]:
        index = {}
        if not self.status_path.exists(): return index
        with self.status_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try: row = json.loads(line)
                except json.JSONDecodeError: continue
                if isinstance(row, dict) and isinstance(row.get("request_id"), str): index[row["request_id"]] = row
        return index

    def write_artifact(self, directory: Path, item: RequestItem, value: Any, suffix: str = "") -> str:
        path = directory / (safe_name(item.request_id) + suffix + ".json")
        atomic_json(path, value)
        return path.relative_to(self.output_dir).as_posix()

    def append_status(self, row: dict[str, Any]) -> None:
        encoded = stable_json(row)
        for slot in self.slots:
            if slot.key in encoded: raise RuntimeError("secret leakage detected before status write")
        with self.write_lock:
            self.status_path.parent.mkdir(parents=True, exist_ok=True)
            with self.status_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(encoded + "\n"); handle.flush()
            self.completed_counter += 1
            if self.completed_counter % 20 == 0:
                elapsed = max(0.001, time.perf_counter() - self.started_at)
                print(stable_json({"progress": self.completed_counter, "elapsed_seconds": round(elapsed, 1),
                                   "requests_per_second": round(self.completed_counter / elapsed, 4)}), flush=True)

    def send_request(self, slot: KeySlot, item: RequestItem) -> ProviderHTTPResult:
        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {slot.key}", "Content-Type": "application/json", "Accept": "application/json"}
        content = stable_json(item.payload).encode("utf-8")
        timeout = httpx.Timeout(self.timeout_seconds)
        if not item.payload.get("stream"):
            response = slot.client.post(url, headers=headers, content=content, timeout=timeout)
            try:
                body = response.json()
            except Exception:
                body = {"non_json_body": response.text[:2000]}
            return ProviderHTTPResult(response.status_code, dict(response.headers), body)

        with slot.client.stream("POST", url, headers=headers, content=content, timeout=timeout) as response:
            if response.status_code >= 400:
                raw = response.read()
                try:
                    body = json.loads(raw.decode("utf-8"))
                except Exception:
                    body = {"non_json_body": raw.decode("utf-8", errors="replace")[:2000]}
                return ProviderHTTPResult(response.status_code, dict(response.headers), body)
            event_count = 0
            event_hasher = hashlib.sha256()
            content_parts: list[str] = []
            reasoning_parts: list[str] = []
            finish_reason = None; response_id = None; response_model = None; created = None; usage = None
            for line in response.iter_lines():
                stripped = line.strip()
                if not stripped or not stripped.startswith("data:"):
                    continue
                data = stripped[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    event = {"unparsed_sse_data": data[:2000]}
                    event_count += 1; event_hasher.update((stable_json(event) + "\n").encode("utf-8"))
                    continue
                event_count += 1; event_hasher.update((stable_json(event) + "\n").encode("utf-8"))
                response_id = event.get("id", response_id); response_model = event.get("model", response_model)
                created = event.get("created", created); usage = event.get("usage", usage)
                choices = event.get("choices")
                if isinstance(choices, list) and choices:
                    choice = choices[0] if isinstance(choices[0], dict) else {}
                    delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
                    piece = delta.get("content")
                    if isinstance(piece, str): content_parts.append(piece)
                    reasoning = delta.get("reasoning_content", delta.get("reasoning"))
                    if isinstance(reasoning, str): reasoning_parts.append(reasoning)
                    if choice.get("finish_reason") is not None: finish_reason = choice.get("finish_reason")
            message: dict[str, Any] = {"role": "assistant", "content": "".join(content_parts)}
            if reasoning_parts: message["reasoning_content"] = "".join(reasoning_parts)
            body = {"id": response_id, "object": "chat.completion", "created": created, "model": response_model,
                    "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}], "usage": usage}
            return ProviderHTTPResult(response.status_code, dict(response.headers), body, event_count, event_hasher.hexdigest())

    def run_one(self, item: RequestItem, sequence: int) -> dict[str, Any]:
        started = time.perf_counter(); attempt_records = []
        for attempt in range(1, self.max_retries + 2):
            slot = self.slots[(sequence + attempt - 1) % len(self.slots)]
            slot.rate_wait()
            try:
                with slot.semaphore:
                    response = self.send_request(slot, item)
                body = response.body
                raw_value = {
                    "status_code": response.status_code,
                    "headers": {k.lower(): v for k, v in response.headers.items() if k.lower() not in {"authorization", "set-cookie"}},
                    "body": body,
                    "streaming": bool(item.payload.get("stream")),
                }
                if response.stream_event_count is not None:
                    raw_value["stream_event_count"] = response.stream_event_count
                    raw_value["stream_events_sha256"] = response.stream_events_sha256
                raw_path = self.write_artifact(self.raw_dir, item, raw_value,
                                               suffix=f".attempt-{attempt}" if attempt > 1 else "")
                if response.status_code >= 400:
                    retryable = response.status_code in INFRA_STATUS or 500 <= response.status_code <= 599
                    attempt_records.append({"attempt": attempt, "status_code": response.status_code, "retryable": retryable, "raw_path": raw_path})
                    if retryable and attempt <= self.max_retries:
                        retry_after = response.headers.get("retry-after", "")
                        try:
                            wait_seconds = float(retry_after)
                        except (TypeError, ValueError):
                            wait_seconds = 0.0
                        if isinstance(body, dict):
                            try:
                                wait_seconds = max(wait_seconds, float(body.get("retry_after", 0)))
                            except (TypeError, ValueError):
                                pass
                        wait_seconds = max(wait_seconds, float(2 ** (attempt - 1)))
                        remaining = min(wait_seconds, 180.0)
                        while remaining > 0:
                            step = min(30.0, remaining)
                            print(stable_json({"request_id": item.request_id, "retry_wait_seconds": step,
                                               "http_status": response.status_code, "attempt": attempt}), flush=True)
                            time.sleep(step); remaining -= step
                        continue
                    return self.status_row(item, started, "infra_error" if retryable else "api_error", attempt,
                                           raw_path=raw_path, error_code=f"HTTP_{response.status_code}",
                                           error_message="infrastructure HTTP error" if retryable else "non-retryable HTTP error",
                                           attempts=attempt_records)
                parsed = (
                    parse_compact_response(body, item.alias_to_candidate_id, item.candidate_ids, item.require_selected)
                    if item.alias_to_candidate_id is not None
                    else parse_response(body, item.candidate_ids, item.require_selected)
                )
                parsed_path = self.write_artifact(self.parsed_dir, item, {
                    "request_id": item.request_id, "track": item.track, "request_sha256": item.request_sha256,
                    "valid": parsed.valid, "prediction": parsed.data, "error_code": parsed.error_code,
                    "error_message": parsed.error_message, "reasoning_present": parsed.reasoning_present,
                    "transport_adapter": item.transport_adapter,
                    "alias_map_sha256": sha256_text(stable_json(item.alias_to_candidate_id)) if item.alias_to_candidate_id else None,
                })
                choice = body.get("choices", [{}])[0] if isinstance(body, dict) else {}
                return self.status_row(
                    item, started, "succeeded" if parsed.valid else "parse_failure", attempt,
                    raw_path=raw_path, parsed_path=parsed_path, error_code=parsed.error_code,
                    error_message=parsed.error_message, finish_reason=choice.get("finish_reason"),
                    response_model=body.get("model") if isinstance(body, dict) else None,
                    usage=body.get("usage") if isinstance(body, dict) else None,
                    reasoning_present=parsed.reasoning_present, attempts=attempt_records,
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                attempt_records.append({"attempt": attempt, "transport_error_type": type(exc).__name__, "retryable": True})
                if attempt <= self.max_retries:
                    time.sleep(min(30, 2 ** (attempt - 1))); continue
                return self.status_row(item, started, "infra_error", attempt, error_code="TRANSPORT_ERROR",
                                       error_message=type(exc).__name__, attempts=attempt_records)
        raise AssertionError("retry loop exhausted")

    def status_row(self, item: RequestItem, started: float, status: str, attempts_count: int, **extra: Any) -> dict[str, Any]:
        return {
            "request_id": item.request_id, "track": item.track, "task_type": item.task_type,
            "prediction_target": item.prediction_target, "status": status,
            "request_sha256": item.request_sha256, "source_row_sha256": item.source_row_sha256,
            "candidate_order_sha256": item.candidate_order_sha256, "candidate_count": len(item.candidate_ids),
            "require_selected": item.require_selected, "attempt_count": attempts_count,
            "transport_adapter": item.transport_adapter,
            "parser_contract": item.parser_contract,
            "alias_map_sha256": sha256_text(stable_json(item.alias_to_candidate_id)) if item.alias_to_candidate_id else None,
            "retry_count": max(0, attempts_count - 1), "latency_ms": round((time.perf_counter() - started) * 1000, 3),
            "completed_at_utc": utc_now(), **extra,
        }

    def run(self, items: list[RequestItem], resume: bool = True) -> dict[str, Any]:
        self.output_dir.mkdir(parents=True, exist_ok=True); self.raw_dir.mkdir(exist_ok=True); self.parsed_dir.mkdir(exist_ok=True)
        existing = self.existing() if resume else {}
        pending = []
        for index, item in enumerate(items):
            previous = existing.get(item.request_id)
            if previous and previous.get("request_sha256") != item.request_sha256:
                raise SystemExit(f"request hash changed for resumed row: {item.request_id}")
            previous_code = str(previous.get("error_code", "")) if previous else ""
            prior_server_error = previous_code.startswith("HTTP_5")
            if previous and previous.get("status") in TERMINAL and not prior_server_error:
                continue
            pending.append((index, item))
        print(stable_json({"stage": "run", "total": len(items), "already_terminal": len(items) - len(pending),
                           "pending": len(pending), "concurrency": self.concurrency}), flush=True)
        with ThreadPoolExecutor(max_workers=self.concurrency, thread_name_prefix="qwen-formal") as pool:
            futures = {pool.submit(self.run_one, item, index): item.request_id for index, item in pending}
            for future in as_completed(futures):
                row = future.result(); self.append_status(row)
        final = self.existing()
        counts = Counter(row.get("status", "unknown") for row in final.values())
        elapsed = max(0.001, time.perf_counter() - self.started_at)
        summary = {
            "status": "PASS" if len(final) == len(items) and not counts.get("infra_error") and not counts.get("api_error") else "INCOMPLETE_OR_BLOCKED",
            "request_count": len(items), "status_rows": len(final), "status_counts": dict(sorted(counts.items())),
            "processed_now": len(pending), "resumed_or_skipped": len(items) - len(pending),
            "elapsed_seconds": round(elapsed, 3), "effective_requests_per_second": round(len(pending) / elapsed, 6),
            "concurrency": self.concurrency, "key_slots": len(self.slots), "per_key_concurrency_limit": 4,
            "per_key_rate_limit_per_minute": 60, "timeout_seconds": self.timeout_seconds,
            "max_retries_infrastructure_only": self.max_retries, "model": MODEL, "base_url": self.base_url,
            "formal_calls_completed": sum(counts.values()), "generated_at_utc": utc_now(),
        }
        atomic_json(self.summary_path, summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        return summary


def preflight(base_url: str, keys: list[str], output: Path) -> dict[str, Any]:
    results = []
    for index, key in enumerate(keys, 1):
        with httpx.Client(timeout=120, trust_env=False, http2=False, follow_redirects=True) as client:
            headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            models_response = client.get(f"{base_url.rstrip('/')}/models", headers=headers, timeout=30)
            models_response.raise_for_status(); models = models_response.json()
            served = [row.get("id") for row in models.get("data", []) if isinstance(row, dict)]
            payload = {"model": MODEL, "messages": [{"role": "user", "content": "Return only {\"ok\":true}."}],
                       "response_format": {"type": "json_object"}, "temperature": 0, "top_p": 1,
                       "seed": 0, "max_tokens": 32, "stream": False}
            chat_response = client.post(f"{base_url.rstrip('/')}/chat/completions", headers=headers,
                                        content=stable_json(payload).encode("utf-8"), timeout=120)
            chat_response.raise_for_status(); chat = chat_response.json()
            content = chat.get("choices", [{}])[0].get("message", {}).get("content")
            valid_json = False
            try: valid_json = json.loads(content) == {"ok": True}
            except Exception: pass
            results.append({"key_slot": index, "models_http_status": models_response.status_code,
                            "served_model_ids": served, "expected_model_present": MODEL in served,
                            "chat_http_status": chat_response.status_code, "response_model": chat.get("model"),
                            "finish_reason": chat.get("choices", [{}])[0].get("finish_reason"),
                            "json_mode_valid": valid_json,
                            "reasoning_content_present": "reasoning_content" in chat.get("choices", [{}])[0].get("message", {}),
                            "usage": chat.get("usage")})
    status = "PASS" if len(results) == len(keys) and all(r["expected_model_present"] and r["json_mode_valid"] for r in results) else "FAIL"
    report = {"schema_version": 1, "status": status, "base_url": base_url, "model": MODEL,
              "key_slots_tested": len(keys), "results": results, "api_keys_persisted": False,
              "configured_context": "UNAVAILABLE_FROM_OPENAI_COMPATIBLE_API",
              "max_output_tokens": 4096, "temperature": 0, "top_p": 1, "seed": 0,
              "generated_at_utc": utc_now()}
    atomic_json(output, report); print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["preflight", "smoke", "formal"], required=True)
    parser.add_argument("--track", choices=["native", "unified", "machine", "smoke"], default="smoke")
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--base-url", default=os.environ.get("SDB_QWEN_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--model", default=os.environ.get("SDB_QWEN_MODEL", MODEL))
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument("--timeout-seconds", type=float, default=1800)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--max-output-tokens", type=int, default=4096)
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--request-id", help="Run exactly one named request as an infrastructure canary.")
    parser.add_argument("--limit", type=int, help="Run the first N resolved rows as a bounded scale canary.")
    parser.add_argument("--max-candidates", type=int, help="Run only rows whose candidate pool is at most N; creates a bounded subset revision.")
    parser.add_argument("--stream", action="store_true", help="Use SSE streaming while preserving the same semantic prompt and parser.")
    parser.add_argument("--compact-alias-v2", action="store_true", help="Use the lossless compact-field and positional-alias transport revision.")
    parser.add_argument("--compact-core-v3", action="store_true", help="Use the tunnel-safe core model-visible serializer plus positional aliases.")
    parser.add_argument("--compact-identity-v4", action="store_true", help="Use the 120-second identity/capability serializer plus positional aliases.")
    parser.add_argument("--compact-bounded-v5", action="store_true", help="Use the inline-aware bounded core serializer plus positional aliases.")
    args = parser.parse_args()
    if sum(bool(value) for value in (args.compact_alias_v2, args.compact_core_v3, args.compact_identity_v4, args.compact_bounded_v5)) > 1:
        raise SystemExit("choose only one compact transport revision")
    if args.model != MODEL: raise SystemExit(f"model mismatch: expected {MODEL}, got {args.model}")
    keys = load_keys()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.mode == "preflight":
        report = preflight(args.base_url, keys, args.output_dir / "PREFLIGHT_RUNTIME_FREEZE.json")
        if report["status"] != "PASS": raise SystemExit(2)
        return
    if args.input is None or not args.input.is_file(): raise SystemExit("--input is required and must exist")
    if args.mode == "smoke":
        items = list(iter_smoke(args.input, args.model, args.compact_alias_v2, args.compact_core_v3, args.compact_identity_v4, args.compact_bounded_v5))
    else:
        items = list(iter_formal(args.input, args.track, args.model, args.compact_alias_v2, args.compact_core_v3, args.compact_identity_v4, args.compact_bounded_v5))
    full_expected = {"smoke": 60, "native": 4798, "unified": 4798, "machine": 197}[args.track]
    if len(items) != full_expected: raise SystemExit(f"row count mismatch for {args.track}: {len(items)} != {full_expected}")
    if args.stream:
        for item in items:
            item.payload["stream"] = True
            item.payload["stream_options"] = {"include_usage": True}
    if args.request_id:
        items = [item for item in items if item.request_id == args.request_id]
        if len(items) != 1: raise SystemExit(f"request-id did not resolve uniquely: {args.request_id}")
    if args.max_candidates is not None:
        if args.max_candidates < 1: raise SystemExit("--max-candidates must be positive")
        items = [item for item in items if len(item.candidate_ids) <= args.max_candidates]
    if args.limit is not None:
        if args.limit < 1: raise SystemExit("--limit must be positive")
        items = items[:args.limit]
    if args.max_output_tokens < 1:
        raise SystemExit("--max-output-tokens must be positive")
    for item in items:
        item.payload["max_tokens"] = args.max_output_tokens
    if not items: raise SystemExit("filters resolved to zero rows")
    runner = FormalRunner(args.base_url, keys, args.output_dir, args.concurrency, args.timeout_seconds, args.max_retries)
    try: runner.run(items, resume=not args.no_resume)
    finally: runner.close()


if __name__ == "__main__":
    main()
