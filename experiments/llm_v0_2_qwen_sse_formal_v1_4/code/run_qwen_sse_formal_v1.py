from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
import threading
import time
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx


MODEL = "Qwen3.6-35B-A3B-APEX-I-Compact.gguf"
TOKENIZER_REVISION = "995ad96eacd98c81ed38be0c5b274b04031597b0"
FROZEN_ADAPTER_SHA256 = "24e8f423cb58e1f284560689e430cdbdece82d7786ed2ab076c5990fd1923afc"
DEFAULT_BASE_URL = ""
KEY_ENV_NAMES = [
    "SDB_QWEN_API_KEY_01",
    "SDB_QWEN_API_KEY_02",
    "SDB_QWEN_API_KEY_03",
    "SDB_QWEN_API_KEY_04",
]
RETRY_BACKOFF_SECONDS = (15, 30, 60)
RETRYABLE_HTTP = {408, 425, 429, 524}
TERMINAL_STATUSES = {"succeeded", "parse_failure", "api_error"}
CONNECT_TIMEOUT_SECONDS = 30.0
READ_TIMEOUT_SECONDS = 45.0
MAX_WALL_SECONDS = 7500.0


def _load_frozen_adapter() -> Any:
    source = Path(__file__).resolve().parent / "frozen_adapter_v1.py"
    spec = importlib.util.spec_from_file_location("sdb_frozen_qwen_adapter_v1", source)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load frozen adapter: {source}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


FROZEN = _load_frozen_adapter()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def append_jsonl(path: Path, value: Any, lock: threading.Lock | None = None) -> None:
    encoded = stable_json(value)
    context = lock if lock is not None else _NullLock()
    with context:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded + "\n")
            handle.flush()


class _NullLock:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *_: Any) -> None:
        return None


def load_keys() -> list[str]:
    keys = [os.environ.get(name, "") for name in KEY_ENV_NAMES]
    keys = [key for key in keys if key]
    if not keys:
        raise SystemExit("NO_QWEN_KEY: set SDB_QWEN_API_KEY_01..04")
    if len(keys) != len(set(keys)):
        raise SystemExit("duplicate Qwen API keys are forbidden")
    return keys


def secret_guard(value: Any, keys: list[str]) -> None:
    encoded = stable_json(value)
    if any(key in encoded for key in keys):
        raise RuntimeError("secret leakage detected before artifact write")


def redact_secrets(value: Any, keys: list[str]) -> Any:
    encoded = stable_json(value)
    for key in keys:
        encoded = encoded.replace(key, "[REDACTED_API_KEY]")
    return json.loads(encoded)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def secrets_absent(root: Path, keys: list[str]) -> bool:
    needles = [key.encode("utf-8") for key in keys]
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        with path.open("rb") as handle:
            data = handle.read()
        if any(needle in data for needle in needles):
            return False
    return True


@dataclass
class SSEOutcome:
    status_code: int
    headers: dict[str, str]
    final_response: dict[str, Any] | None
    heartbeat_count: int
    sse_event_count: int
    first_event_latency_ms: float | None
    first_data_latency_ms: float | None
    terminal_event_received: bool
    done_received: bool
    finish_reason: str | None
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False


def consume_sse_data(data: str, event_name: str | None, state: dict[str, Any]) -> tuple[str, str | None, str | None]:
    if data == "[DONE]":
        return "done", None, None
    if _is_heartbeat(event_name, data, None):
        return "heartbeat", None, None
    try:
        decoded = json.loads(data)
    except json.JSONDecodeError:
        return "error", "INVALID_SSE_JSON", "data event is not JSON"
    if _is_heartbeat(event_name, data, decoded):
        return "heartbeat", None, None
    if not isinstance(decoded, dict):
        return "error", "INVALID_SSE_JSON", "data event is not a JSON object"
    if event_name == "error" or decoded.get("error") is not None or decoded.get("type") == "error":
        return "error", "SSE_ERROR_EVENT", stable_json(decoded)[:2000]
    state["response_id"] = decoded.get("id", state.get("response_id"))
    state["response_model"] = decoded.get("model", state.get("response_model"))
    state["created"] = decoded.get("created", state.get("created"))
    state["usage"] = decoded.get("usage", state.get("usage"))
    choices = decoded.get("choices")
    if isinstance(choices, list) and choices:
        choice = choices[0] if isinstance(choices[0], dict) else {}
        delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
        piece = delta.get("content")
        if isinstance(piece, str):
            state["content_parts"].append(piece)
        reasoning = delta.get("reasoning_content", delta.get("reasoning"))
        if isinstance(reasoning, str):
            state["reasoning_parts"].append(reasoning)
        if choice.get("finish_reason") is not None:
            state["finish_reason"] = str(choice.get("finish_reason"))
            state["terminal"] = True
    return "data", None, None


class KeySlot:
    def __init__(self, key: str, index: int) -> None:
        self.key = key
        self.index = index
        self.semaphore = threading.BoundedSemaphore(1)
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


def _is_heartbeat(event_name: str | None, data: str, decoded: Any) -> bool:
    if event_name and event_name.lower() in {"heartbeat", "ping", "keepalive", "keep-alive"}:
        return True
    if data.strip().lower() in {"heartbeat", "ping", "keepalive", "keep-alive"}:
        return True
    if isinstance(decoded, dict):
        kind = str(decoded.get("type", decoded.get("event", ""))).lower()
        if kind in {"heartbeat", "ping", "keepalive", "keep-alive"}:
            return True
        if decoded.get("heartbeat") is True:
            return True
    return False


def _safe_headers(headers: httpx.Headers) -> dict[str, str]:
    blocked = {"authorization", "set-cookie", "cf-ray"}
    return {key.lower(): value for key, value in headers.items() if key.lower() not in blocked}


class FormalSSERunner:
    def __init__(self, base_url: str, keys: list[str], output_dir: Path, concurrency: int, max_retries: int,
                 budget_binding: dict[str, Any]) -> None:
        self.base_url = base_url.rstrip("/")
        self.keys = keys
        self.slots = [KeySlot(key, index + 1) for index, key in enumerate(keys)]
        self.output_dir = output_dir
        self.artifacts_dir = output_dir / "artifacts"
        self.status_path = output_dir / "REQUEST_STATUS.jsonl"
        self.summary_path = output_dir / "RUN_SUMMARY.json"
        self.concurrency = min(4, len(keys), concurrency)
        self.max_retries = min(3, max(0, max_retries))
        self.budget_binding = budget_binding
        self.write_lock = threading.Lock()
        self.started_at = time.perf_counter()
        self.processed_now = 0

    def close(self) -> None:
        for slot in self.slots:
            slot.close()

    def artifact_dir(self, item: Any) -> Path:
        return self.artifacts_dir / FROZEN.safe_name(item.request_id)

    def existing(self) -> dict[str, dict[str, Any]]:
        rows: dict[str, dict[str, Any]] = {}
        if not self.status_path.exists():
            return rows
        with self.status_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict) and isinstance(row.get("request_id"), str):
                    rows[row["request_id"]] = row
        return rows

    def write_request(self, item: Any) -> None:
        value = {
            "schema_version": 1,
            "request_id": item.request_id,
            "track": item.track,
            "request_sha256": item.request_sha256,
            "source_row_sha256": item.source_row_sha256,
            "candidate_order_sha256": item.candidate_order_sha256,
            "candidate_count": len(item.candidate_ids),
            "transport_adapter": item.transport_adapter,
            "parser_contract": item.parser_contract,
            "budget_binding": self.budget_binding,
            "payload": item.payload,
        }
        secret_guard(value, self.keys)
        atomic_json(self.artifact_dir(item) / "request.json", value)

    def send_stream(self, slot: KeySlot, item: Any, attempt: int, raw_path: Path, request_deadline: float) -> SSEOutcome:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {slot.key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        request_started = time.perf_counter()
        remaining = request_deadline - request_started
        if remaining <= 0:
            return SSEOutcome(0, {}, None, 0, 0, None, None, False, False, None,
                              "MAX_WALL_TIME_EXCEEDED", "request exceeded 7500 seconds", True)
        timeout = httpx.Timeout(
            connect=min(CONNECT_TIMEOUT_SECONDS, remaining),
            read=min(READ_TIMEOUT_SECONDS, remaining),
            write=min(30.0, remaining),
            pool=min(30.0, remaining),
        )
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        with raw_path.open("w", encoding="utf-8", newline="\n") as raw_handle:
            def write_raw(record: dict[str, Any]) -> None:
                safe_record = redact_secrets(record, self.keys)
                secret_guard(safe_record, self.keys)
                raw_handle.write(stable_json(safe_record) + "\n")
                raw_handle.flush()

            with slot.client.stream(
                "POST", url, headers=headers, content=stable_json(item.payload).encode("utf-8"), timeout=timeout
            ) as response:
                safe_headers = _safe_headers(response.headers)
                if response.status_code >= 400:
                    body = response.read().decode("utf-8", errors="replace")
                    append = {
                        "received_at_utc": utc_now(),
                        "elapsed_ms": round((time.perf_counter() - request_started) * 1000, 3),
                        "kind": "http_error_body",
                        "data": body[:20000],
                    }
                    write_raw(append)
                    retryable = response.status_code in RETRYABLE_HTTP or 500 <= response.status_code <= 599
                    return SSEOutcome(
                        response.status_code, safe_headers, None, 0, 0, None, None, False, False, None,
                        f"HTTP_{response.status_code}", "HTTP error", retryable,
                    )

                heartbeat_count = 0
                event_count = 0
                first_event_ms: float | None = None
                first_data_ms: float | None = None
                done = False
                event_name: str | None = None
                data_lines: list[str] = []
                data_elapsed_ms: float | None = None
                state: dict[str, Any] = {
                    "terminal": False,
                    "finish_reason": None,
                    "response_id": None,
                    "response_model": None,
                    "created": None,
                    "usage": None,
                    "content_parts": [],
                    "reasoning_parts": [],
                }

                def failure(code: str, message: str) -> SSEOutcome:
                    return SSEOutcome(
                        response.status_code, safe_headers, None, heartbeat_count, event_count,
                        first_event_ms, first_data_ms, bool(state["terminal"]), done, state["finish_reason"],
                        code, message, True,
                    )

                def dispatch_frame() -> SSEOutcome | None:
                    nonlocal heartbeat_count, event_count, first_data_ms, done
                    if not data_lines:
                        return None
                    data = "\n".join(data_lines)
                    kind, error_code, error_message = consume_sse_data(data, event_name, state)
                    if kind == "done":
                        done = True
                    elif kind == "heartbeat":
                        heartbeat_count += 1
                    elif kind == "data":
                        event_count += 1
                        if first_data_ms is None:
                            first_data_ms = data_elapsed_ms
                    elif kind == "error":
                        return failure(error_code or "SSE_ERROR", error_message or "SSE error")
                    return None

                for line in response.iter_lines():
                    now = time.perf_counter()
                    elapsed_ms = round((now - request_started) * 1000, 3)
                    if now >= request_deadline:
                        return failure("MAX_WALL_TIME_EXCEEDED", "request exceeded 7500 seconds")
                    if not line:
                        write_raw({"received_at_utc": utc_now(), "elapsed_ms": elapsed_ms, "kind": "frame_boundary", "raw_line": ""})
                        if not data_lines and event_name and event_name.lower() in {"heartbeat", "ping", "keepalive", "keep-alive"}:
                            heartbeat_count += 1
                        dispatched = dispatch_frame()
                        if dispatched is not None:
                            return dispatched
                        data_lines = []
                        data_elapsed_ms = None
                        event_name = None
                        if done:
                            break
                        continue
                    if first_event_ms is None:
                        first_event_ms = elapsed_ms
                    record: dict[str, Any] = {
                        "received_at_utc": utc_now(), "elapsed_ms": elapsed_ms, "raw_line": line,
                    }
                    if line.startswith(":"):
                        heartbeat_count += 1
                        record["kind"] = "heartbeat_comment"
                        write_raw(record)
                        continue
                    if line.startswith("event:"):
                        event_name = line[6:].strip()
                        record["kind"] = "event_name"
                        write_raw(record)
                        continue
                    if line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
                        data_elapsed_ms = elapsed_ms
                        record["kind"] = "data_line"
                        write_raw(record)
                        continue
                    else:
                        record["kind"] = "unrecognized_sse_line"
                        write_raw(record)
                        continue
                if data_lines and not done:
                    dispatched = dispatch_frame()
                    if dispatched is not None:
                        return dispatched
                terminal = bool(state["terminal"])
                finish_reason = state["finish_reason"]
                if not terminal or not done:
                    missing = []
                    if not terminal:
                        missing.append("terminal event")
                    if not done:
                        missing.append("[DONE]")
                    return SSEOutcome(
                        response.status_code, safe_headers, None, heartbeat_count, event_count,
                        first_event_ms, first_data_ms, terminal, done, finish_reason,
                        "INCOMPLETE_SSE_TERMINATION", "missing " + " and ".join(missing), True,
                    )
                message: dict[str, Any] = {"role": "assistant", "content": "".join(state["content_parts"])}
                if state["reasoning_parts"]:
                    message["reasoning_content"] = "".join(state["reasoning_parts"])
                final = {
                    "id": state["response_id"],
                    "object": "chat.completion",
                    "created": state["created"],
                    "model": state["response_model"],
                    "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
                    "usage": state["usage"],
                }
                return SSEOutcome(
                    response.status_code, safe_headers, final, heartbeat_count, event_count,
                    first_event_ms, first_data_ms, terminal, done, finish_reason,
                )

    def status_row(self, item: Any, started_utc: str, started: float, status: str, attempt: int, **extra: Any) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "request_id": item.request_id,
            "track": item.track,
            "task_type": item.task_type,
            "prediction_target": item.prediction_target,
            "model": MODEL,
            "status": status,
            "request_sha256": item.request_sha256,
            "source_row_sha256": item.source_row_sha256,
            "candidate_order_sha256": item.candidate_order_sha256,
            "candidate_count": len(item.candidate_ids),
            "started_at_utc": started_utc,
            "completed_at_utc": utc_now(),
            "end_to_end_latency_ms": round((time.perf_counter() - started) * 1000, 3),
            "attempt_count": attempt,
            "retry_count": max(0, attempt - 1),
            "transport_adapter": item.transport_adapter,
            "parser_contract": item.parser_contract,
            "budget_freeze_sha256": self.budget_binding.get("budget_freeze_sha256"),
            "frozen_max_tokens": self.budget_binding.get("frozen_max_tokens"),
            "tokenizer_revision": self.budget_binding.get("tokenizer_revision"),
            "adapter_sha256": self.budget_binding.get("adapter_sha256"),
            **extra,
        }

    def run_one(self, item: Any, sequence: int) -> dict[str, Any]:
        started_utc = utc_now()
        started = time.perf_counter()
        request_deadline = started + MAX_WALL_SECONDS
        self.write_request(item)
        attempt_records: list[dict[str, Any]] = []
        for attempt in range(1, self.max_retries + 2):
            slot = self.slots[(sequence + attempt - 1) % len(self.slots)]
            slot.rate_wait()
            raw_path = self.artifact_dir(item) / (
                "raw_sse_events.jsonl" if attempt == 1 else f"raw_sse_events.attempt-{attempt}.jsonl"
            )
            try:
                with slot.semaphore:
                    outcome = self.send_stream(slot, item, attempt, raw_path, request_deadline)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                outcome = SSEOutcome(
                    0, {}, None, 0, 0, None, None, False, False, None,
                    "SSE_READ_TIMEOUT" if isinstance(exc, httpx.ReadTimeout) else "TRANSPORT_ERROR",
                    type(exc).__name__, True,
                )
            attempt_record = {
                "attempt": attempt,
                "key_slot": slot.index,
                "http_status": outcome.status_code,
                "heartbeat_count": outcome.heartbeat_count,
                "sse_event_count": outcome.sse_event_count,
                "terminal_event_received": outcome.terminal_event_received,
                "done_received": outcome.done_received,
                "error_code": outcome.error_code,
                "retryable": outcome.retryable,
                "raw_sse_events_path": raw_path.relative_to(self.output_dir).as_posix(),
            }
            attempt_records.append(attempt_record)

            if outcome.retryable:
                if attempt <= self.max_retries:
                    wait_seconds = RETRY_BACKOFF_SECONDS[attempt - 1]
                    if time.perf_counter() + wait_seconds >= request_deadline:
                        outcome.error_code = "MAX_WALL_TIME_EXCEEDED"
                        outcome.error_message = "retry backoff would exceed 7500-second request deadline"
                        attempt = self.max_retries + 1
                    else:
                        print(stable_json({"request_id": item.request_id, "attempt": attempt, "retry_in_seconds": wait_seconds,
                                           "error_code": outcome.error_code}), flush=True)
                        time.sleep(wait_seconds)
                        continue
                row = self.status_row(
                    item, started_utc, started, "infra_error", attempt,
                    http_status=outcome.status_code, finish_reason=outcome.finish_reason,
                    parse_status="not_attempted", error_code=outcome.error_code,
                    error_message=outcome.error_message, heartbeat_count=outcome.heartbeat_count,
                    first_event_latency_ms=outcome.first_event_latency_ms,
                    first_data_latency_ms=outcome.first_data_latency_ms,
                    terminal_event_received=outcome.terminal_event_received, done_received=outcome.done_received,
                    attempts=attempt_records,
                )
                atomic_json(self.artifact_dir(item) / "status.json", row)
                return row

            if outcome.status_code >= 400:
                row = self.status_row(
                    item, started_utc, started, "api_error", attempt,
                    http_status=outcome.status_code, finish_reason=outcome.finish_reason,
                    parse_status="not_attempted", error_code=outcome.error_code,
                    error_message=outcome.error_message, heartbeat_count=outcome.heartbeat_count,
                    first_event_latency_ms=outcome.first_event_latency_ms,
                    first_data_latency_ms=outcome.first_data_latency_ms,
                    terminal_event_received=outcome.terminal_event_received, done_received=outcome.done_received,
                    attempts=attempt_records,
                )
                atomic_json(self.artifact_dir(item) / "status.json", row)
                return row

            assert outcome.final_response is not None
            final_value = {
                "schema_version": 1,
                "request_id": item.request_id,
                "request_sha256": item.request_sha256,
                "response": outcome.final_response,
                "http_status": outcome.status_code,
                "headers": outcome.headers,
            }
            secret_guard(final_value, self.keys)
            atomic_json(self.artifact_dir(item) / "final_response.json", final_value)
            if outcome.final_response.get("model") != MODEL:
                row = self.status_row(
                    item, started_utc, started, "api_error", attempt,
                    http_status=outcome.status_code, finish_reason=outcome.finish_reason,
                    parse_status="not_attempted", error_code="MODEL_IDENTITY_MISMATCH",
                    error_message=f"expected {MODEL}; received {outcome.final_response.get('model')}",
                    heartbeat_count=outcome.heartbeat_count, sse_event_count=outcome.sse_event_count,
                    first_event_latency_ms=outcome.first_event_latency_ms,
                    first_data_latency_ms=outcome.first_data_latency_ms,
                    terminal_event_received=outcome.terminal_event_received, done_received=outcome.done_received,
                    response_model=outcome.final_response.get("model"), attempts=attempt_records,
                )
                atomic_json(self.artifact_dir(item) / "status.json", row)
                return row
            parsed = (
                FROZEN.parse_compact_response(
                    outcome.final_response, item.alias_to_candidate_id, item.candidate_ids, item.require_selected
                ) if item.alias_to_candidate_id is not None
                else FROZEN.parse_response(outcome.final_response, item.candidate_ids, item.require_selected)
            )
            parsed_value = {
                "schema_version": 1,
                "request_id": item.request_id,
                "track": item.track,
                "request_sha256": item.request_sha256,
                "parse_status": "valid" if parsed.valid else "invalid",
                "prediction": parsed.data,
                "error_code": parsed.error_code,
                "error_message": parsed.error_message,
                "reasoning_content_present": parsed.reasoning_present,
            }
            atomic_json(self.artifact_dir(item) / "parsed_prediction.json", parsed_value)
            usage = outcome.final_response.get("usage") if isinstance(outcome.final_response, dict) else None
            row = self.status_row(
                item, started_utc, started, "succeeded" if parsed.valid else "parse_failure", attempt,
                http_status=outcome.status_code, finish_reason=outcome.finish_reason,
                parse_status=parsed_value["parse_status"], error_code=parsed.error_code,
                error_message=parsed.error_message, heartbeat_count=outcome.heartbeat_count,
                sse_event_count=outcome.sse_event_count,
                first_event_latency_ms=outcome.first_event_latency_ms,
                first_data_latency_ms=outcome.first_data_latency_ms,
                terminal_event_received=outcome.terminal_event_received, done_received=outcome.done_received,
                response_model=outcome.final_response.get("model"), usage=usage, attempts=attempt_records,
            )
            atomic_json(self.artifact_dir(item) / "status.json", row)
            return row
        raise AssertionError("retry loop exhausted")

    def append_status(self, row: dict[str, Any]) -> None:
        secret_guard(row, self.keys)
        append_jsonl(self.status_path, row, self.write_lock)
        with self.write_lock:
            self.processed_now += 1
            completed = self.processed_now
        if completed % 10 == 0:
            elapsed = max(0.001, time.perf_counter() - self.started_at)
            print(stable_json({"processed_now": completed, "elapsed_seconds": round(elapsed, 1),
                               "requests_per_second": round(completed / elapsed, 5)}), flush=True)

    def validate_reusable(self, item: Any, previous: dict[str, Any]) -> None:
        if previous.get("budget_freeze_sha256") != self.budget_binding.get("budget_freeze_sha256"):
            raise SystemExit(f"resume budget freeze mismatch for {item.request_id}")
        if previous.get("frozen_max_tokens") != self.budget_binding.get("frozen_max_tokens"):
            raise SystemExit(f"resume max_tokens mismatch for {item.request_id}")
        directory = self.artifact_dir(item)
        required_json = [directory / "request.json", directory / "status.json"]
        if previous.get("status") in {"succeeded", "parse_failure"}:
            required_json.extend([directory / "final_response.json", directory / "parsed_prediction.json"])
        for path in required_json:
            if not path.is_file():
                raise SystemExit(f"resume artifact missing: {path}")
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise SystemExit(f"resume artifact unreadable: {path}: {type(exc).__name__}") from exc
            if value.get("request_sha256") != item.request_sha256:
                raise SystemExit(f"resume artifact request hash mismatch: {path}")
            if path.name == "request.json":
                artifact_binding = value.get("budget_binding", {})
                if artifact_binding.get("budget_freeze_sha256") != self.budget_binding.get("budget_freeze_sha256"):
                    raise SystemExit(f"resume request budget binding mismatch: {path}")
        for attempt in previous.get("attempts", []):
            raw_relative = attempt.get("raw_sse_events_path")
            if not isinstance(raw_relative, str) or not (self.output_dir / raw_relative).is_file():
                raise SystemExit(f"resume raw SSE artifact missing for {item.request_id}")

    def run(self, items: list[Any], resume: bool) -> dict[str, Any]:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(exist_ok=True)
        existing = self.existing() if resume else {}
        pending: list[tuple[int, Any]] = []
        for index, item in enumerate(items):
            previous = existing.get(item.request_id)
            if previous and previous.get("request_sha256") != item.request_sha256:
                raise SystemExit(f"request hash changed for resumed row: {item.request_id}")
            if previous and previous.get("status") in TERMINAL_STATUSES:
                self.validate_reusable(item, previous)
                continue
            pending.append((index, item))
        print(stable_json({"stage": "run", "total": len(items), "already_terminal": len(items) - len(pending),
                           "pending": len(pending), "concurrency": self.concurrency, "per_key_inflight": 1}), flush=True)
        with ThreadPoolExecutor(max_workers=self.concurrency, thread_name_prefix="qwen-sse-formal") as pool:
            futures = {pool.submit(self.run_one, item, index): item.request_id for index, item in pending}
            for future in as_completed(futures):
                self.append_status(future.result())
        final = self.existing()
        counts = Counter(row.get("status", "unknown") for row in final.values())
        elapsed = max(0.001, time.perf_counter() - self.started_at)
        summary = {
            "schema_version": 1,
            "status": "PASS" if len(final) == len(items) and not counts.get("infra_error") and not counts.get("api_error") else "INCOMPLETE_OR_BLOCKED",
            "request_count": len(items),
            "status_rows": len(final),
            "status_counts": dict(sorted(counts.items())),
            "processed_now": len(pending),
            "resumed_or_skipped": len(items) - len(pending),
            "elapsed_seconds": round(elapsed, 3),
            "effective_requests_per_second": round(len(pending) / elapsed, 6),
            "concurrency": self.concurrency,
            "key_slots": len(self.slots),
            "per_key_inflight": 1,
            "connect_timeout_seconds": CONNECT_TIMEOUT_SECONDS,
            "sse_read_timeout_seconds": READ_TIMEOUT_SECONDS,
            "maximum_wall_time_seconds": MAX_WALL_SECONDS,
            "max_infrastructure_retries": self.max_retries,
            "retry_backoff_seconds": list(RETRY_BACKOFF_SECONDS),
            "model": MODEL,
            "base_url": self.base_url,
            "stream": True,
            "budget_binding": self.budget_binding,
            "artifact_credentials_scan_pass": secrets_absent(self.output_dir, self.keys),
            "generated_at_utc": utc_now(),
        }
        if not summary["artifact_credentials_scan_pass"]:
            summary["status"] = "INCOMPLETE_OR_BLOCKED"
        secret_guard(summary, self.keys)
        atomic_json(self.summary_path, summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        return summary


def load_budget_binding(args: argparse.Namespace) -> dict[str, Any]:
    if sha256_file(Path(FROZEN.__file__)) != FROZEN_ADAPTER_SHA256:
        raise SystemExit("frozen adapter SHA-256 mismatch")
    report = json.loads(args.budget_freeze.read_text(encoding="utf-8"))
    report_sha = sha256_file(args.budget_freeze)
    if report.get("status") != "PASS" or report.get("model") != MODEL:
        raise SystemExit("budget freeze status/model mismatch")
    tokenizer = report.get("tokenizer_binding", {})
    if tokenizer.get("revision") != TOKENIZER_REVISION:
        raise SystemExit("tokenizer revision mismatch in budget freeze")
    track_key = "native" if args.track == "smoke" else args.track
    track = report.get("tracks", {}).get(track_key)
    if not isinstance(track, dict):
        raise SystemExit(f"budget freeze lacks track: {track_key}")
    input_hash = sha256_file(args.input)
    if args.track == "smoke":
        if args.smoke_budget_validation is None:
            raise SystemExit("smoke requires --smoke-budget-validation")
        smoke = json.loads(args.smoke_budget_validation.read_text(encoding="utf-8"))
        if smoke.get("status") != "PASS" or smoke.get("smoke_source_sha256") != input_hash:
            raise SystemExit("smoke budget validation status/input mismatch")
        if smoke.get("applied_native_frozen_max_tokens") != track.get("frozen_max_tokens"):
            raise SystemExit("smoke/native frozen max_tokens mismatch")
        validation_sha = sha256_file(args.smoke_budget_validation)
    else:
        if track.get("source_sha256") != input_hash:
            raise SystemExit(f"{args.track} manifest SHA-256 does not match budget freeze")
        validation_sha = None
    return {
        "budget_freeze_path": str(args.budget_freeze.resolve()),
        "budget_freeze_sha256": report_sha,
        "budget_track": track_key,
        "frozen_max_tokens": int(track["frozen_max_tokens"]),
        "source_manifest_sha256": input_hash,
        "tokenizer_repo_id": tokenizer.get("repo_id"),
        "tokenizer_revision": tokenizer.get("revision"),
        "tokenizer_files": tokenizer.get("files"),
        "smoke_budget_validation_sha256": validation_sha,
        "adapter_sha256": FROZEN_ADAPTER_SHA256,
    }


def resolve_items(args: argparse.Namespace, max_tokens: int) -> list[Any]:
    if args.mode == "smoke":
        items = list(FROZEN.iter_smoke(args.input, MODEL))
    else:
        items = list(FROZEN.iter_formal(args.input, args.track, MODEL))
    if args.request_id:
        items = [item for item in items if item.request_id == args.request_id]
        if len(items) != 1:
            raise SystemExit(f"request id did not resolve uniquely: {args.request_id}")
    if args.limit is not None:
        items = items[: args.limit]
    for item in items:
        item.payload["stream"] = True
        item.payload["temperature"] = 0
        item.payload["top_p"] = 1
        item.payload["n"] = 1
        item.payload["seed"] = 0
        item.payload["max_tokens"] = max_tokens
    return items


def main() -> None:
    parser = argparse.ArgumentParser(description="Frozen Qwen SSE runner for SDB V1.4/V1.2")
    parser.add_argument("--mode", choices=("smoke", "formal"), required=True)
    parser.add_argument("--track", choices=("native", "machine", "unified", "smoke"), required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--base-url", default=os.environ.get("SDB_QWEN_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--budget-freeze", type=Path, required=True)
    parser.add_argument("--smoke-budget-validation", type=Path)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--request-id")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args()
    if args.mode == "smoke" and args.track != "smoke":
        raise SystemExit("smoke mode requires --track smoke")
    if args.mode == "formal" and args.track == "smoke":
        raise SystemExit("formal mode requires native, machine, or unified")
    configured_model = os.environ.get("SDB_QWEN_MODEL", MODEL)
    if configured_model != MODEL:
        raise SystemExit(f"SDB_QWEN_MODEL mismatch: {configured_model}")
    keys = load_keys()
    budget_binding = load_budget_binding(args)
    items = resolve_items(args, budget_binding["frozen_max_tokens"])
    if not items:
        raise SystemExit("no requests resolved")
    runner = FormalSSERunner(args.base_url, keys, args.output_dir, args.concurrency, args.max_retries, budget_binding)
    try:
        summary = runner.run(items, resume=not args.no_resume)
    finally:
        runner.close()
    raise SystemExit(0 if summary["status"] == "PASS" else 2)


if __name__ == "__main__":
    main()
