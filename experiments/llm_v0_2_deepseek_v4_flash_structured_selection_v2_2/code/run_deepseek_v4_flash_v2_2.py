from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx


MODEL = "deepseek-v4-flash"
MODEL_VERSION = "DeepSeek-V4-Flash-0731"
REVISION = "DEEPSEEK_V4_FLASH_FULL_SIX_TASK_V2_2"
IMPLEMENTATION_REVISION = "DEEPSEEK_V4_FLASH_V2_2_R2_GATE_ACCOUNTING"
TOKEN_COUNTER_REVISION = "UTF8_BYTE_UPPER_BOUND_PLUS_REASONING_4096_V2_2"
KEY_ENV_NAME = "SDB_DEEPSEEK_API_KEY"
BASE_URL_ENV_NAME = "SDB_DEEPSEEK_BASE_URL"
MODEL_ENV_NAME = "SDB_DEEPSEEK_MODEL"
EXPECTED_FORMAL_ROWS = {"native": 4798, "machine": 197}
EXPECTED_SMOKE_ROWS = 60
TASKS = (
    "single_service_discovery",
    "single_api_recommendation",
    "multi_service_discovery",
    "multi_api_recommendation",
    "composable_service_discovery",
    "composable_api_recommendation",
)
RETRY_BACKOFF_SECONDS = (15, 30, 60)
MAX_ATTEMPTS = len(RETRY_BACKOFF_SECONDS) + 1
RETRYABLE_HTTP = {408, 425, 429, 524}
CONNECT_TIMEOUT_SECONDS = 30.0
READ_TIMEOUT_SECONDS = 120.0
MAX_WALL_SECONDS = 7500.0
SYSTEM_MESSAGE = (
    "You are a deterministic Service/API candidate-selection engine. "
    "Return only one valid JSON object matching the supplied output contract. "
    "Do not add Markdown or explanation to the final answer."
)
INSTRUCTIONS = {
    "TOP5_RANKING_V1": "Rank the five candidates most relevant to the request, or all candidates if fewer than five are supplied. Return ranked_candidate_ids only.",
    "SELECTED_SET_V1": "Select the minimal sufficient set required to complete the request. Include every necessary candidate and no merely similar candidate. Return selected_candidate_ids only; do not assume a target set size.",
    "RANKING_AND_SELECTED_SET_V1_10": "Rank the five most relevant candidates (or all if fewer than five), then independently select the complete minimal sufficient API set. The selected set may exceed five and may include IDs outside the Top-5. Return exactly ranked_candidate_ids and selected_candidate_ids.",
}


def _load_contracts() -> Any:
    path = Path(__file__).with_name("output_contracts_v2_2.py")
    spec = importlib.util.spec_from_file_location("sdb_deepseek_contracts_v2_2", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load output contracts: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CONTRACTS = _load_contracts()


def _load_size_utils() -> Any:
    path = Path(__file__).with_name("contract_size_utils_v2_2.py")
    spec = importlib.util.spec_from_file_location("sdb_deepseek_contract_sizes_v2_2_runner", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load contract-size utilities: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


SIZE = _load_size_utils()


def stable_json(value: Any) -> str:
    return SIZE.stable_json(value)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def append_jsonl(path: Path, value: Any, lock: threading.Lock) -> None:
    with lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(stable_json(value) + "\n")
            handle.flush()
            os.fsync(handle.fileno())


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"non-object row at {path}:{line_number}")
        rows.append(row)
    return rows


def git_commit(root: Path) -> str:
    try:
        return subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return "UNKNOWN"


def load_key() -> str:
    key = os.environ.get(KEY_ENV_NAME, "").strip()
    if not key:
        raise SystemExit(f"{KEY_ENV_NAME} is required")
    return key


def assert_independent_namespace(output_dir: Path) -> None:
    normalized = str(output_dir.resolve()).replace("\\", "/").lower()
    if "qwen" in normalized:
        raise SystemExit("DeepSeek output must not use a Qwen result namespace")
    if "deepseek" not in normalized:
        raise SystemExit("DeepSeek output path must contain an explicit deepseek namespace")
    for row in read_jsonl(output_dir / "REQUEST_STATUS.jsonl"):
        if row.get("experiment_revision") != REVISION or row.get("implementation_revision") != IMPLEMENTATION_REVISION or row.get("provider") != "deepseek":
            raise SystemExit("output directory contains results from another experiment/provider")


def validate_resume_audit(output_dir: Path) -> None:
    statuses = read_jsonl(output_dir / "REQUEST_STATUS.jsonl")
    status_ids = [row.get("request_id") for row in statuses]
    if any(not isinstance(value, str) or not value for value in status_ids) or len(status_ids) != len(set(status_ids)):
        raise SystemExit("invalid or duplicate DeepSeek resume status ID")
    ledger = read_jsonl(output_dir / "ATTEMPT_LEDGER.jsonl")
    if statuses and any("attempt_count" in row for row in statuses) and not ledger:
        raise SystemExit("DeepSeek resume statuses exist without an attempt ledger")
    events: dict[tuple[str, int], list[str]] = {}
    for line_number, row in enumerate(ledger, 1):
        if row.get("provider") != "deepseek" or row.get("experiment_revision") != REVISION or row.get("implementation_revision") != IMPLEMENTATION_REVISION:
            raise SystemExit(f"attempt ledger provider/revision mismatch at line {line_number}")
        request_id = row.get("request_id")
        attempt = row.get("attempt")
        event = row.get("event")
        if not isinstance(request_id, str) or not isinstance(attempt, int) or event not in {"attempt_started", "attempt_finished"}:
            raise SystemExit(f"invalid attempt ledger row at line {line_number}")
        key = (request_id, attempt)
        sequence = events.setdefault(key, [])
        if event == "attempt_started" and sequence:
            raise SystemExit(f"duplicate or out-of-order attempt start: {request_id}#{attempt}")
        if event == "attempt_finished" and sequence != ["attempt_started"]:
            raise SystemExit(f"attempt finish lacks one preceding start: {request_id}#{attempt}")
        sequence.append(event)
    unfinished = [key for key, sequence in events.items() if sequence != ["attempt_started", "attempt_finished"]]
    if unfinished:
        raise SystemExit(f"resume blocked by unknown in-flight DeepSeek attempts: {unfinished[:5]}")
    finished_ids = {request_id for request_id, _ in events}
    audited_status_ids = {row["request_id"] for row in statuses if "attempt_count" in row}
    if finished_ids != audited_status_ids:
        raise SystemExit("attempt-ledger/status request identities differ")


def validate_mode_arguments(mode: str, track: str, rows: list[dict[str, Any]], limit: int | None, request_id: str | None) -> None:
    if mode == "formal":
        if track not in EXPECTED_FORMAL_ROWS or limit is not None or request_id is not None:
            raise SystemExit("formal mode requires native/machine and forbids diagnostic filters")
        if len(rows) != EXPECTED_FORMAL_ROWS[track]:
            raise SystemExit(f"formal {track} row count mismatch: {len(rows)} != {EXPECTED_FORMAL_ROWS[track]}")
    elif mode == "smoke":
        if track != "smoke" or limit is not None or request_id is not None or len(rows) != EXPECTED_SMOKE_ROWS:
            raise SystemExit("smoke mode requires exactly 60 unfiltered smoke rows")
        counts = Counter(row.get("task_type") for row in rows)
        if counts != Counter({task: 10 for task in TASKS}):
            raise SystemExit(f"smoke identity must contain 10 rows per task: {dict(counts)}")
    elif mode == "diagnostic":
        if track not in {"native", "machine", "smoke"}:
            raise SystemExit("unsupported diagnostic track")
    else:
        raise SystemExit(f"unsupported mode: {mode}")


def _visible(row: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    visible = row.get("model_visible_input", row)
    query = visible.get("query", visible.get("query_text")) if isinstance(visible, dict) else None
    documents = visible.get("candidate_documents") if isinstance(visible, dict) else None
    if not isinstance(query, str) or not isinstance(documents, list):
        raise ValueError("row lacks model-visible query/candidate_documents")
    return query, documents


def _output_contract(contract: str, candidate_count: int) -> dict[str, Any]:
    ranked = {"type": "array", "items": {"type": "string"}, "minItems": min(5, candidate_count), "maxItems": min(5, candidate_count), "uniqueItems": True}
    selected = {"type": "array", "items": {"type": "string"}, "maxItems": candidate_count, "uniqueItems": True}
    if contract == CONTRACTS.TOP5_RANKING_V1:
        return {"type": "object", "additionalProperties": False, "required": ["ranked_candidate_ids"], "properties": {"ranked_candidate_ids": ranked}}
    if contract == CONTRACTS.SELECTED_SET_V1:
        return {"type": "object", "additionalProperties": False, "required": ["selected_candidate_ids"], "properties": {"selected_candidate_ids": selected}}
    if contract == CONTRACTS.RANKING_AND_SELECTED_SET_V1_10:
        return {"type": "object", "additionalProperties": False, "required": ["ranked_candidate_ids", "selected_candidate_ids"], "properties": {"ranked_candidate_ids": ranked, "selected_candidate_ids": selected}}
    raise ValueError(f"unknown contract: {contract}")


def build_payload(*, query: str, task_type: str, prediction_target: str, candidate_documents: list[dict[str, Any]], candidate_ids: list[str], contract: str, max_tokens: int) -> dict[str, Any]:
    if not candidate_ids or len(candidate_ids) != len(set(candidate_ids)) or len(candidate_documents) != len(candidate_ids):
        raise ValueError("candidate IDs/documents are empty, duplicated, or misaligned")
    for index, (document, candidate_id) in enumerate(zip(candidate_documents, candidate_ids, strict=True)):
        if not isinstance(document, dict) or document.get("candidate_id") != candidate_id or not isinstance(document.get("document"), str):
            raise ValueError(f"candidate document mismatch at index {index}")
    visible = {
        "query": query,
        "task_type": task_type,
        "prediction_target": prediction_target,
        "candidate_documents": candidate_documents,
        "instruction": INSTRUCTIONS[contract],
        "output_contract": _output_contract(contract, len(candidate_ids)),
        "allowed_candidate_ids": candidate_ids,
    }
    return {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_MESSAGE},
            {"role": "user", "content": "INPUT_JSON=" + stable_json(visible)},
        ],
        "thinking": {"type": "enabled"},
        "reasoning_effort": "high",
        "response_format": {"type": "json_object"},
        "max_tokens": max_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
    }


@dataclass(frozen=True)
class RequestItem:
    request_id: str
    track: str
    task_type: str
    prediction_target: str
    candidate_ids: list[str]
    contract: str
    payload: dict[str, Any]
    source_row_sha256: str
    serialized_request_bytes: int = 0
    user_message_bytes: int = 0
    candidate_document_bytes: int = 0
    legal_answer_bound_bytes: int = 0

    @property
    def request_sha256(self) -> str:
        return sha256_text(stable_json({"revision": REVISION, "source": self.source_row_sha256, "contract": self.contract, "payload": self.payload}))


def load_items(path: Path, track: str, max_tokens: int) -> list[RequestItem]:
    rows = read_jsonl(path)
    items: list[RequestItem] = []
    mapping_track = "machine" if track == "machine" else "native"
    for line_number, row in enumerate(rows, 1):
        query, documents = _visible(row)
        candidate_ids = row.get("candidate_ids") or [document.get("candidate_id") for document in documents]
        task_type = row.get("task_type")
        prediction_target = row.get("prediction_target")
        request_id = row.get("benchmark_task_id", row.get("request_id"))
        if not isinstance(candidate_ids, list) or any(not isinstance(item, str) for item in candidate_ids):
            raise ValueError(f"invalid candidate IDs at line {line_number}")
        if not all(isinstance(value, str) and value for value in (task_type, prediction_target, request_id)):
            raise ValueError(f"invalid request metadata at line {line_number}")
        contract = CONTRACTS.contract_for(mapping_track, task_type)
        payload = build_payload(query=query, task_type=task_type, prediction_target=prediction_target, candidate_documents=documents, candidate_ids=candidate_ids, contract=contract, max_tokens=max_tokens)
        serialized_request_bytes = len(stable_json(payload).encode("utf-8"))
        input_limit = 8 * 1024 * 1024
        if serialized_request_bytes > input_limit:
            raise ValueError(
                f"request body exceeds 8 MiB: request_id={request_id}; "
                f"line={line_number}; actual_bytes={serialized_request_bytes}; limit_bytes={input_limit}"
            )
        items.append(
            RequestItem(
                request_id,
                track,
                task_type,
                prediction_target,
                candidate_ids,
                contract,
                payload,
                sha256_text(stable_json(row)),
                serialized_request_bytes,
                len(payload["messages"][1]["content"].encode("utf-8")),
                len(stable_json(documents).encode("utf-8")),
                SIZE.legal_answer_bound_bytes(contract, candidate_ids),
            )
        )
    if len({item.request_id for item in items}) != len(items):
        raise ValueError("duplicate request ID in manifest")
    return items


@dataclass
class StreamOutcome:
    http_status: int | None
    final_response: dict[str, Any] | None
    finish_reason: str | None
    terminal: bool
    done: bool
    event_count: int
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False


def classify_finish_reason(finish_reason: str | None) -> tuple[str | None, str | None, bool]:
    mapping: dict[str, tuple[str | None, str | None, bool]] = {
        "stop": (None, None, False),
        "insufficient_system_resource": ("infra_error", "INSUFFICIENT_SYSTEM_RESOURCE", True),
        "length": ("api_error", "OUTPUT_BUDGET_EXHAUSTED", False),
        "content_filter": ("api_error", "CONTENT_FILTERED", False),
        "tool_calls": ("api_error", "UNEXPECTED_TOOL_CALL_FINISH", False),
    }
    return mapping.get(finish_reason, ("api_error", "UNSUPPORTED_FINISH_REASON", False))


def capture_stable_metadata(state: dict[str, Any], field: str, incoming: Any) -> str | None:
    if incoming is None:
        return None
    current = state.get(field)
    if current is None:
        state[field] = incoming
        return None
    if current == incoming:
        return None
    return f"INCONSISTENT_{field.upper()}"


def _consume_sse(data: str, state: dict[str, Any]) -> str | None:
    if data == "[DONE]":
        state["done"] = True
        return None
    try:
        chunk = json.loads(data)
    except json.JSONDecodeError:
        return "INVALID_SSE_JSON"
    if not isinstance(chunk, dict) or chunk.get("error") is not None:
        return "SSE_ERROR_EVENT"
    for state_field, chunk_field in (
        ("response_id", "id"),
        ("response_created", "created"),
        ("system_fingerprint", "system_fingerprint"),
        ("model", "model"),
    ):
        if capture_stable_metadata(state, state_field, chunk.get(chunk_field)) is not None:
            return "INCONSISTENT_RESPONSE_METADATA"
    if isinstance(chunk.get("usage"), dict):
        state["usage"] = chunk["usage"]
    choices = chunk.get("choices")
    if choices == []:
        state["events"] += 1
        return None
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        return "CHOICE_COUNT_CONTRACT_VIOLATION"
    choice = choices[0]
    delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
    if isinstance(delta.get("reasoning_content"), str):
        state["reasoning"].append(delta["reasoning_content"])
    if isinstance(delta.get("content"), str):
        state["content"].append(delta["content"])
    if choice.get("finish_reason") is not None:
        state["finish_reason"] = str(choice["finish_reason"])
        state["terminal"] = True
    state["events"] += 1
    return None


class DeepSeekRunner:
    def __init__(self, *, base_url: str, key: str, output_dir: Path, concurrency: int, provenance: dict[str, Any]) -> None:
        if concurrency < 1 or concurrency > 32:
            raise ValueError("concurrency must be in 1..32")
        self.base_url = base_url.rstrip("/")
        self.key = key
        self.output_dir = output_dir
        self.concurrency = concurrency
        self.provenance = provenance
        self.write_lock = threading.Lock()

    def send_stream(self, item: RequestItem, raw_sse_path: Path) -> StreamOutcome:
        state: dict[str, Any] = {
            "response_id": None,
            "response_created": None,
            "system_fingerprint": None,
            "model": None,
            "content": [],
            "reasoning": [],
            "usage": None,
            "finish_reason": None,
            "terminal": False,
            "done": False,
            "events": 0,
        }
        started = time.perf_counter()
        try:
            timeout = httpx.Timeout(connect=CONNECT_TIMEOUT_SECONDS, read=READ_TIMEOUT_SECONDS, write=60.0, pool=60.0)
            with httpx.stream("POST", self.base_url + "/chat/completions", headers={"Authorization": f"Bearer {self.key}", "Accept": "text/event-stream"}, json=item.payload, timeout=timeout, trust_env=False, follow_redirects=True) as response:
                if response.status_code != 200:
                    retryable = response.status_code in RETRYABLE_HTTP or 500 <= response.status_code <= 599
                    return StreamOutcome(response.status_code, None, None, False, False, 0, f"HTTP_{response.status_code}", "non-200 response", retryable)
                for line in response.iter_lines():
                    if time.perf_counter() - started > MAX_WALL_SECONDS:
                        return StreamOutcome(200, None, state["finish_reason"], state["terminal"], state["done"], state["events"], "MAX_WALL_TIME_EXCEEDED", "request exceeded wall limit", True)
                    if not line or line.startswith(":"):
                        continue
                    if not line.startswith("data:"):
                        return StreamOutcome(200, None, state["finish_reason"], state["terminal"], state["done"], state["events"], "INVALID_SSE_LINE", "unexpected SSE line", False)
                    data = line[5:].lstrip()
                    append_jsonl(raw_sse_path, {"sequence": state["events"] + 1, "captured_at_utc": utc_now(), "data": data}, self.write_lock)
                    error = _consume_sse(data, state)
                    if error:
                        return StreamOutcome(200, None, state["finish_reason"], state["terminal"], state["done"], state["events"], error, "SSE contract violation", False)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            return StreamOutcome(None, None, state["finish_reason"], state["terminal"], state["done"], state["events"], "TRANSPORT_ERROR", type(exc).__name__, True)
        if not state["terminal"] or not state["done"]:
            return StreamOutcome(200, None, state["finish_reason"], state["terminal"], state["done"], state["events"], "INCOMPLETE_SSE_TERMINATION", "finish_reason and [DONE] are required", True)
        message: dict[str, Any] = {"content": "".join(state["content"])}
        if state["reasoning"]:
            message["reasoning_content"] = "".join(state["reasoning"])
        final = {
            "id": state["response_id"],
            "created": state["response_created"],
            "model": state["model"],
            "system_fingerprint": state["system_fingerprint"],
            "choices": [{"message": message, "finish_reason": state["finish_reason"]}],
            "usage": state["usage"],
        }
        status_domain, error_code, retryable = classify_finish_reason(state["finish_reason"])
        if error_code is not None:
            return StreamOutcome(
                200,
                final,
                state["finish_reason"],
                True,
                True,
                state["events"],
                error_code,
                f"finish_reason={state['finish_reason']!r} classified as {status_domain}",
                retryable,
            )
        return StreamOutcome(200, final, state["finish_reason"], True, True, state["events"])

    def run_one(self, item: RequestItem, worker_index: int) -> dict[str, Any]:
        artifact = self.output_dir / "artifacts" / sha256_text(item.request_id)[:24]
        artifact.mkdir(parents=True, exist_ok=True)
        request_path = artifact / "request.json"
        if request_path.exists() and sha256_file(request_path) != sha256_text(json.dumps(item.payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"):
            raise RuntimeError(f"request artifact mismatch for {item.request_id}")
        if not request_path.exists():
            atomic_json(request_path, item.payload)
        attempts: list[dict[str, Any]] = []
        outcome: StreamOutcome | None = None
        ledger_path = self.output_dir / "ATTEMPT_LEDGER.jsonl"
        for attempt in range(1, MAX_ATTEMPTS + 1):
            raw_sse_path = artifact / f"raw_sse_events_attempt_{attempt}.jsonl"
            raw_sse_path.touch(exist_ok=False)
            append_jsonl(ledger_path, {"provider": "deepseek", "experiment_revision": REVISION, "implementation_revision": IMPLEMENTATION_REVISION, "event": "attempt_started", "request_id": item.request_id, "request_sha256": item.request_sha256, "attempt": attempt, "started_at_utc": utc_now(), "raw_sse_events_path": str(raw_sse_path.relative_to(self.output_dir)).replace("\\", "/")}, self.write_lock)
            attempt_started = time.perf_counter()
            outcome = self.send_stream(item, raw_sse_path)
            latency_seconds = time.perf_counter() - attempt_started
            response_attempt_path: str | None = None
            response_attempt_sha256: str | None = None
            if outcome.final_response is not None:
                saved_attempt = artifact / f"response_attempt_{attempt}.json"
                atomic_json(saved_attempt, outcome.final_response)
                response_attempt_path = str(saved_attempt.relative_to(self.output_dir)).replace("\\", "/")
                response_attempt_sha256 = sha256_file(saved_attempt)
            retry = bool(outcome.error_code and outcome.retryable and attempt < MAX_ATTEMPTS)
            raw_hash = sha256_file(raw_sse_path)
            response = outcome.final_response or {}
            metadata = {
                "finish_reason": outcome.finish_reason,
                "response_model": response.get("model"),
                "system_fingerprint": response.get("system_fingerprint"),
                "response_id": response.get("id"),
                "response_created": response.get("created"),
            }
            append_jsonl(ledger_path, {"provider": "deepseek", "experiment_revision": REVISION, "implementation_revision": IMPLEMENTATION_REVISION, "event": "attempt_finished", "request_id": item.request_id, "request_sha256": item.request_sha256, "attempt": attempt, "finished_at_utc": utc_now(), "http_status": outcome.http_status, "error_code": outcome.error_code, "retryable": outcome.retryable, "will_retry": retry, "latency_seconds": latency_seconds, "raw_sse_events_path": str(raw_sse_path.relative_to(self.output_dir)).replace("\\", "/"), "raw_sse_events_sha256": raw_hash, "response_attempt_path": response_attempt_path, "response_attempt_sha256": response_attempt_sha256, **metadata}, self.write_lock)
            attempts.append({"attempt": attempt, "http_status": outcome.http_status, "error_code": outcome.error_code, "retryable": outcome.retryable, "will_retry": retry, "latency_seconds": latency_seconds, "raw_sse_events_path": str(raw_sse_path.relative_to(self.output_dir)).replace("\\", "/"), "raw_sse_events_sha256": raw_hash, "response_attempt_path": response_attempt_path, "response_attempt_sha256": response_attempt_sha256, **metadata})
            if not retry:
                break
            time.sleep(RETRY_BACKOFF_SECONDS[attempt - 1])
        assert outcome is not None
        base = {
            **self.provenance,
            "provider": "deepseek",
            "experiment_revision": REVISION,
            "implementation_revision": IMPLEMENTATION_REVISION,
            "request_id": item.request_id,
            "track": item.track,
            "task_type": item.task_type,
            "prediction_target": item.prediction_target,
            "output_contract": item.contract,
            "candidate_count": len(item.candidate_ids),
            "serialized_request_bytes": item.serialized_request_bytes,
            "user_message_bytes": item.user_message_bytes,
            "candidate_document_bytes": item.candidate_document_bytes,
            "legal_answer_bound_bytes": item.legal_answer_bound_bytes,
            "source_row_sha256": item.source_row_sha256,
            "request_sha256": item.request_sha256,
            "requested_model": MODEL,
            "requested_model_version_mapping": MODEL_VERSION,
            "thinking": {"type": "enabled"},
            "reasoning_effort": "high",
            "sampling_parameters_applicable": False,
            "response_format_type": "json_object",
            "max_output_tokens_requested": item.payload["max_tokens"],
            "worker_index": worker_index,
            "attempt_count": len(attempts),
            "retry_count": len(attempts) - 1,
            "attempts": attempts,
            "raw_sse_events_path": attempts[-1]["raw_sse_events_path"],
            "raw_sse_events_sha256": attempts[-1]["raw_sse_events_sha256"],
            "http_status": outcome.http_status,
            "terminal_event_received": outcome.terminal,
            "done_received": outcome.done,
            "finish_reason": outcome.finish_reason,
            "sse_event_count": outcome.event_count,
            "response_model": None if outcome.final_response is None else outcome.final_response.get("model"),
            "response_id": None if outcome.final_response is None else outcome.final_response.get("id"),
            "response_created": None if outcome.final_response is None else outcome.final_response.get("created"),
            "system_fingerprint": None if outcome.final_response is None else outcome.final_response.get("system_fingerprint"),
            "endpoint_sha256": sha256_text(self.base_url.rstrip("/")),
            "usage": None if outcome.final_response is None else outcome.final_response.get("usage"),
            "latency_seconds": sum(float(attempt_row["latency_seconds"]) for attempt_row in attempts),
            "completed_at_utc": utc_now(),
        }
        if outcome.final_response is not None:
            atomic_json(artifact / "response.json", outcome.final_response)
            base["response_path"] = str((artifact / "response.json").relative_to(self.output_dir)).replace("\\", "/")
        if outcome.error_code:
            return {**base, "status": "infra_error" if outcome.retryable else "api_error", "parse_status": "not_attempted", "error_code": outcome.error_code, "error_message": outcome.error_message}
        assert outcome.final_response is not None
        model = outcome.final_response.get("model")
        if model not in {MODEL, MODEL_VERSION}:
            return {**base, "status": "api_error", "parse_status": "not_attempted", "error_code": "MODEL_IDENTITY_MISMATCH", "error_message": "response model is outside the frozen alias/version mapping"}
        if outcome.finish_reason != "stop":
            raise AssertionError("non-stop finish reason reached the local parser")
        if item.contract == CONTRACTS.TOP5_RANKING_V1:
            parsed = CONTRACTS.parse_topk_response(outcome.final_response, item.candidate_ids, min(5, len(item.candidate_ids)))
        elif item.contract == CONTRACTS.SELECTED_SET_V1:
            parsed = CONTRACTS.parse_selected_set_response(outcome.final_response, item.candidate_ids)
        else:
            parsed = CONTRACTS.parse_ranking_and_selected_set_response(outcome.final_response, item.candidate_ids)
        message = outcome.final_response["choices"][0]["message"]
        reasoning = message.get("reasoning_content")
        base.update({"reasoning_content_present": isinstance(reasoning, str) and bool(reasoning), "reasoning_sha256": sha256_text(reasoning) if isinstance(reasoning, str) else None, "reasoning_char_count": len(reasoning) if isinstance(reasoning, str) else 0, "content_sha256": sha256_text(message["content"]), "content_bytes": len(message["content"].encode("utf-8"))})
        if parsed.valid:
            atomic_json(artifact / "parsed_prediction.json", parsed.data)
            return {**base, "status": "succeeded", "parse_status": "valid", "error_code": None, "error_message": None, "parsed_prediction_path": str((artifact / "parsed_prediction.json").relative_to(self.output_dir)).replace("\\", "/")}
        return {**base, "status": "parse_failure", "parse_status": "invalid", "error_code": parsed.error_code, "error_message": parsed.error_message}

    def run(self, items: list[RequestItem], mode: str) -> dict[str, Any]:
        assert_independent_namespace(self.output_dir)
        validate_resume_audit(self.output_dir)
        completed = {row["request_id"]: row for row in read_jsonl(self.output_dir / "REQUEST_STATUS.jsonl")}
        if len(completed) != len(read_jsonl(self.output_dir / "REQUEST_STATUS.jsonl")):
            raise ValueError("duplicate resume request ID")
        if set(completed) - {item.request_id for item in items}:
            raise ValueError("resume output contains IDs outside the current manifest")
        pending = [(index, item) for index, item in enumerate(items) if item.request_id not in completed]
        with ThreadPoolExecutor(max_workers=self.concurrency, thread_name_prefix="deepseek-v4-flash-v2-2") as pool:
            futures = {pool.submit(self.run_one, item, index % self.concurrency): item for index, item in pending}
            for future in as_completed(futures):
                row = future.result()
                append_jsonl(self.output_dir / "REQUEST_STATUS.jsonl", row, self.write_lock)
                completed[row["request_id"]] = row
        rows = [completed[item.request_id] for item in items if item.request_id in completed]
        counts = Counter(row["status"] for row in rows)
        unresolved = counts["infra_error"] + counts["api_error"]
        track = items[0].track if items else None
        smoke_gate_details: dict[str, Any] | None = None
        gate_passed: bool | None = None
        if mode == "diagnostic":
            status = "DIAGNOSTIC_COMPLETE"
        elif mode == "smoke":
            by_task = {task: [row for row in rows if row["task_type"] == task] for task in TASKS}
            rows_by_id = {row["request_id"]: row for row in rows}
            max_request: dict[str, dict[str, Any]] = {}
            max_answer: dict[str, dict[str, Any]] = {}
            exact_passes: list[bool] = []
            for contract in (CONTRACTS.TOP5_RANKING_V1, CONTRACTS.SELECTED_SET_V1, CONTRACTS.RANKING_AND_SELECTED_SET_V1_10):
                contract_items = [item for item in items if item.contract == contract]
                if not contract_items:
                    exact_passes.append(False)
                    continue
                for destination, field in ((max_request, "serialized_request_bytes"), (max_answer, "legal_answer_bound_bytes")):
                    selected = sorted(contract_items, key=lambda item: (-getattr(item, field), item.request_id))[0]
                    row = rows_by_id.get(selected.request_id, {})
                    passed = row.get("status") == "succeeded" and row.get("finish_reason") == "stop" and row.get("parse_status") == "valid"
                    destination[contract] = {
                        "request_id": selected.request_id,
                        field: getattr(selected, field),
                        "status": row.get("status"),
                        "finish_reason": row.get("finish_reason"),
                        "parse_status": row.get("parse_status"),
                        "passed": passed,
                    }
                    exact_passes.append(passed)
            per_task_succeeded = {task: sum(row["status"] == "succeeded" for row in task_rows) for task, task_rows in by_task.items()}
            smoke_gate_details = {
                "per_task_succeeded": per_task_succeeded,
                "per_contract_max_request": max_request,
                "per_contract_max_legal_answer": max_answer,
            }
            gate_passed = (
                len(rows) == EXPECTED_SMOKE_ROWS
                and unresolved == 0
                and counts["succeeded"] >= 54
                and all(value >= 8 for value in per_task_succeeded.values())
                and len(exact_passes) == 6
                and all(exact_passes)
            )
            status = "COMPLETE_ALL_PARSED" if gate_passed and counts["parse_failure"] == 0 else "COMPLETE_WITH_MODEL_FAILURES" if gate_passed else "BLOCKED_DEEPSEEK_DEV_SMOKE"
        elif unresolved:
            status = "BLOCKED_INFRASTRUCTURE_OR_API"
        elif len(rows) != len(items):
            status = "BLOCKED_INPUT_OR_CONTRACT"
        else:
            status = "COMPLETE_ALL_PARSED" if counts["parse_failure"] == 0 else "COMPLETE_WITH_MODEL_FAILURES"
        fingerprints = [row.get("system_fingerprint") for row in rows]
        present_fingerprints = [value for value in fingerprints if isinstance(value, str) and value]
        missing_fingerprints = len(fingerprints) - len(present_fingerprints)
        distinct_fingerprints = set(present_fingerprints)
        if not present_fingerprints:
            fingerprint_finding = "ALL_MISSING"
        elif missing_fingerprints:
            fingerprint_finding = "MIXED_PRESENT_AND_MISSING"
        elif len(distinct_fingerprints) == 1:
            fingerprint_finding = "SINGLE_FINGERPRINT"
        else:
            fingerprint_finding = "MULTIPLE_FINGERPRINTS"
        created_values = [row.get("response_created") for row in rows if isinstance(row.get("response_created"), (int, float))]
        status_counts = {name: counts[name] for name in ("succeeded", "parse_failure", "infra_error", "api_error")}
        summary = {
            **self.provenance,
            "provider": "deepseek",
            "experiment_revision": REVISION,
            "implementation_revision": IMPLEMENTATION_REVISION,
            "status": status,
            "mode": mode,
            "track": track,
            "requested_rows": len(items),
            "terminal_rows": len(rows),
            "status_counts": status_counts,
            "gate_passed": gate_passed,
            "smoke_gate_details": smoke_gate_details,
            "account_scoped_concurrency": self.concurrency,
            "observed_response_models": dict(sorted(Counter(row.get("response_model") for row in rows if row.get("response_model")).items())),
            "observed_system_fingerprints": dict(sorted(Counter(present_fingerprints).items())),
            "missing_system_fingerprint_count": missing_fingerprints,
            "unique_response_id_count": len({row.get("response_id") for row in rows if row.get("response_id") is not None}),
            "response_created_min": min(created_values) if created_values else None,
            "response_created_max": max(created_values) if created_values else None,
            "endpoint_sha256": sha256_text(self.base_url.rstrip("/")),
            "backend_fingerprint_finding": fingerprint_finding,
            "generated_at_utc": utc_now(),
        }
        atomic_json(self.output_dir / "RUN_SUMMARY.json", summary)
        return summary


def load_budget(path: Path, track: str, source: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("status") != "PASS" or value.get("model") != MODEL or value.get("experiment_revision") != REVISION or value.get("token_counter_revision") != TOKEN_COUNTER_REVISION:
        raise SystemExit("DeepSeek budget freeze identity mismatch")
    budget_track = "native" if track == "smoke" else track
    record = value.get("tracks", {}).get(budget_track)
    if not isinstance(record, dict) or int(record.get("frozen_max_tokens", 0)) < 1:
        raise SystemExit(f"budget freeze lacks {budget_track}")
    source_hash = sha256_file(source)
    if source_hash not in record.get("allowed_source_manifest_sha256", []):
        raise SystemExit("source manifest is not bound by the DeepSeek budget freeze")
    return {"frozen_max_tokens": int(record["frozen_max_tokens"]), "budget_freeze_sha256": sha256_file(path), "source_manifest_sha256": source_hash}


def load_runtime_freeze(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "schema_version": 2,
        "provider": "deepseek",
        "experiment_revision": REVISION,
        "implementation_revision": IMPLEMENTATION_REVISION,
        "served_model_id": MODEL,
        "model_version_mapping": MODEL_VERSION,
        "endpoint_in_repository": False,
        "thinking": {"type": "enabled"},
        "reasoning_effort": "high",
        "response_format": {"type": "json_object"},
        "sampling_parameters": "NOT_APPLICABLE_IN_THINKING_MODE_AND_NOT_SENT",
        "stage_gate_policy": "Q0_PASS_THEN_SMOKE_PASS_THEN_MACHINE_COMPLETE_THEN_NATIVE",
        "finish_reason_accounting": "STOP_PARSE; INSUFFICIENT_RESOURCE_RETRY_INFRA; LENGTH_BUDGET_BLOCK; CONTENT_FILTER_PROVIDER_BLOCK; TOOL_CALLS_BLOCK",
        "smoke_coverage_policy": "EXACT_MAX_SERIALIZED_REQUEST_AND_EXACT_MAX_LEGAL_ANSWER_PER_CONTRACT",
        "response_metadata_policy": "CAPTURE_ID_CREATED_MODEL_SYSTEM_FINGERPRINT_ENDPOINT_HASH",
    }
    if any(value.get(key) != expected_value for key, expected_value in expected.items()):
        raise SystemExit("DeepSeek runtime freeze mismatch")
    return {"runtime_freeze_sha256": sha256_file(path), "model_version_mapping": value["model_version_mapping"], "sampling_parameter_policy": value["sampling_parameters"]}


def read_json_object(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise SystemExit(f"BLOCKED_STAGE_PREREQUISITE: missing {label}: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"BLOCKED_STAGE_PREREQUISITE: invalid {label}: {type(exc).__name__}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"BLOCKED_STAGE_PREREQUISITE: {label} must be a JSON object")
    return value


def _stage_block(message: str) -> None:
    raise SystemExit(f"BLOCKED_STAGE_PREREQUISITE: {message}")


def _status_count(report: dict[str, Any], name: str) -> int:
    value = report.get("status_counts", {}).get(name, 0)
    return int(value) if isinstance(value, (int, float)) else -1


def validate_q0_report(q0_report: Path, runtime_freeze: Path, budget_freeze: Path) -> dict[str, Any]:
    value = read_json_object(q0_report, "Q0 report")
    expected = {
        "status": "PASS",
        "provider": "deepseek",
        "experiment_revision": REVISION,
        "implementation_revision": IMPLEMENTATION_REVISION,
        "terminal_rows": 6,
        "runtime_freeze_sha256": sha256_file(runtime_freeze),
        "budget_freeze_sha256": sha256_file(budget_freeze),
    }
    if any(value.get(field) != expected_value for field, expected_value in expected.items()):
        _stage_block("Q0 identity, status, row count, or freeze hash mismatch")
    if _status_count(value, "infra_error") != 0 or _status_count(value, "api_error") != 0:
        _stage_block("Q0 contains infrastructure or API failures")
    per_contract = value.get("per_contract_strict_parse")
    contracts = (CONTRACTS.TOP5_RANKING_V1, CONTRACTS.SELECTED_SET_V1, CONTRACTS.RANKING_AND_SELECTED_SET_V1_10)
    if not isinstance(per_contract, dict) or any(int(per_contract.get(contract, 0)) < 1 for contract in contracts):
        _stage_block("Q0 lacks a strict parse for every contract")
    summary_path = q0_report.parent / "RUN_SUMMARY.json"
    expected_summary_hash = value.get("diagnostic_run_summary_sha256")
    if not summary_path.is_file() or expected_summary_hash != sha256_file(summary_path):
        _stage_block("Q0 diagnostic run summary hash mismatch")
    native_source_hash = value.get("native_source_manifest_sha256")
    if not isinstance(native_source_hash, str) or len(native_source_hash) != 64:
        _stage_block("Q0 native source manifest hash is missing")
    return value


def validate_smoke_summary(
    smoke_summary: Path,
    q0_report: Path,
    runtime_freeze: Path,
    budget_freeze: Path,
) -> dict[str, Any]:
    value = read_json_object(smoke_summary, "Smoke summary")
    expected = {
        "provider": "deepseek",
        "experiment_revision": REVISION,
        "implementation_revision": IMPLEMENTATION_REVISION,
        "mode": "smoke",
        "track": "smoke",
        "requested_rows": 60,
        "terminal_rows": 60,
        "gate_passed": True,
        "prerequisite_q0_report_sha256": sha256_file(q0_report),
        "runtime_freeze_sha256": sha256_file(runtime_freeze),
        "budget_freeze_sha256": sha256_file(budget_freeze),
    }
    if value.get("status") not in {"COMPLETE_ALL_PARSED", "COMPLETE_WITH_MODEL_FAILURES"}:
        _stage_block("Smoke status is not complete")
    if any(value.get(field) != expected_value for field, expected_value in expected.items()):
        _stage_block("Smoke summary identity, gate, prerequisite, or freeze hash mismatch")
    if _status_count(value, "infra_error") != 0 or _status_count(value, "api_error") != 0:
        _stage_block("Smoke contains infrastructure or API failures")
    return value


def validate_machine_summary(
    machine_summary: Path,
    q0_report: Path,
    smoke_summary: Path,
    runtime_freeze: Path,
    budget_freeze: Path,
) -> dict[str, Any]:
    value = read_json_object(machine_summary, "Machine summary")
    expected = {
        "provider": "deepseek",
        "experiment_revision": REVISION,
        "implementation_revision": IMPLEMENTATION_REVISION,
        "mode": "formal",
        "track": "machine",
        "requested_rows": 197,
        "terminal_rows": 197,
        "prerequisite_q0_report_sha256": sha256_file(q0_report),
        "prerequisite_smoke_summary_sha256": sha256_file(smoke_summary),
        "runtime_freeze_sha256": sha256_file(runtime_freeze),
        "budget_freeze_sha256": sha256_file(budget_freeze),
    }
    if value.get("status") not in {"COMPLETE_ALL_PARSED", "COMPLETE_WITH_MODEL_FAILURES"}:
        _stage_block("Machine status is not complete")
    if any(value.get(field) != expected_value for field, expected_value in expected.items()):
        _stage_block("Machine summary identity, prerequisite, or freeze hash mismatch")
    if _status_count(value, "infra_error") != 0 or _status_count(value, "api_error") != 0:
        _stage_block("Machine contains infrastructure or API failures")
    return value


def validate_stage_prerequisites(
    *,
    mode: str,
    track: str,
    q0_report: Path | None,
    smoke_summary: Path | None,
    machine_summary: Path | None,
    runtime_freeze: Path,
    budget_freeze: Path,
) -> dict[str, str]:
    if mode == "diagnostic":
        return {}
    if mode == "smoke":
        if q0_report is None or smoke_summary is not None or machine_summary is not None:
            _stage_block("Smoke requires only --q0-report")
        validate_q0_report(q0_report, runtime_freeze, budget_freeze)
        return {"prerequisite_q0_report_sha256": sha256_file(q0_report)}
    if mode == "formal" and track == "machine":
        if q0_report is None or smoke_summary is None or machine_summary is not None:
            _stage_block("Formal Machine requires Q0 and Smoke reports only")
        validate_q0_report(q0_report, runtime_freeze, budget_freeze)
        validate_smoke_summary(smoke_summary, q0_report, runtime_freeze, budget_freeze)
        return {
            "prerequisite_q0_report_sha256": sha256_file(q0_report),
            "prerequisite_smoke_summary_sha256": sha256_file(smoke_summary),
        }
    if mode == "formal" and track == "native":
        if q0_report is None or smoke_summary is None or machine_summary is None:
            _stage_block("Formal Native requires Q0, Smoke, and Machine reports")
        validate_q0_report(q0_report, runtime_freeze, budget_freeze)
        validate_smoke_summary(smoke_summary, q0_report, runtime_freeze, budget_freeze)
        validate_machine_summary(machine_summary, q0_report, smoke_summary, runtime_freeze, budget_freeze)
        return {
            "prerequisite_q0_report_sha256": sha256_file(q0_report),
            "prerequisite_smoke_summary_sha256": sha256_file(smoke_summary),
            "prerequisite_machine_summary_sha256": sha256_file(machine_summary),
        }
    _stage_block("unsupported mode/track prerequisite combination")
    return {}


def main() -> None:
    parser = argparse.ArgumentParser(description="DeepSeek V4 Flash V2.2 independent six-task runner")
    parser.add_argument("--mode", choices=("smoke", "formal", "diagnostic"), required=True)
    parser.add_argument("--track", choices=("smoke", "native", "machine"), required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--budget-freeze", type=Path, required=True)
    parser.add_argument("--runtime-freeze", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--request-id")
    parser.add_argument("--q0-report", type=Path)
    parser.add_argument("--smoke-summary", type=Path)
    parser.add_argument("--machine-summary", type=Path)
    args = parser.parse_args()
    for path in (args.input, args.budget_freeze, args.runtime_freeze):
        if not path.is_file():
            raise SystemExit(f"required file is missing: {path}")
    rows = read_jsonl(args.input)
    validate_mode_arguments(args.mode, args.track, rows, args.limit, args.request_id)
    assert_independent_namespace(args.output_dir)
    runtime = load_runtime_freeze(args.runtime_freeze)
    budget = load_budget(args.budget_freeze, args.track, args.input)
    prerequisites = validate_stage_prerequisites(
        mode=args.mode,
        track=args.track,
        q0_report=args.q0_report,
        smoke_summary=args.smoke_summary,
        machine_summary=args.machine_summary,
        runtime_freeze=args.runtime_freeze,
        budget_freeze=args.budget_freeze,
    )
    base_url = os.environ.get(BASE_URL_ENV_NAME, "").strip()
    if not base_url:
        raise SystemExit(f"{BASE_URL_ENV_NAME} is required; no endpoint is hard-coded")
    if os.environ.get(MODEL_ENV_NAME, MODEL) != MODEL:
        raise SystemExit(f"{MODEL_ENV_NAME} differs from the frozen model")
    items = load_items(args.input, args.track, budget["frozen_max_tokens"])
    if args.mode == "diagnostic":
        if args.request_id:
            items = [item for item in items if item.request_id == args.request_id]
            if len(items) != 1:
                raise SystemExit("--request-id did not resolve uniquely")
        if args.limit is not None:
            if args.limit < 1:
                raise SystemExit("--limit must be positive")
            items = items[:args.limit]
    root = Path(__file__).resolve().parents[3]
    provenance = {"git_commit_sha": git_commit(root), "runner_sha256": sha256_file(Path(__file__)), "parser_sha256": sha256_file(Path(__file__).with_name("output_contracts_v2_2.py")), "model": MODEL, "implementation_revision": IMPLEMENTATION_REVISION, **runtime, **budget, **prerequisites}
    runner = DeepSeekRunner(base_url=base_url, key=load_key(), output_dir=args.output_dir, concurrency=args.concurrency, provenance=provenance)
    summary = runner.run(items, args.mode)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    raise SystemExit(0 if summary["status"] in {"COMPLETE_ALL_PARSED", "COMPLETE_WITH_MODEL_FAILURES", "DIAGNOSTIC_COMPLETE"} else 2)


if __name__ == "__main__":
    main()
