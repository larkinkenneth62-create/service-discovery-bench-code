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


MODEL = "Qwen3.6-35B-A3B-APEX-I-Compact.gguf"
TOKENIZER_REVISION = "995ad96eacd98c81ed38be0c5b274b04031597b0"
REVISION = "QWEN_SSE_SELECTION_V1_5"
KEY_ENV_NAMES = [f"SDB_QWEN_API_KEY_{index:02d}" for index in range(1, 5)]
EXPECTED_FORMAL_ROWS = {"machine": 197, "native": 4798}
RETRY_BACKOFF_SECONDS = (15, 30, 60)
RETRYABLE_HTTP = {408, 425, 429, 524}
CONNECT_TIMEOUT_SECONDS = 30.0
READ_TIMEOUT_SECONDS = 45.0
MAX_WALL_SECONDS = 7500.0
SYSTEM_MESSAGE = (
    "You are a deterministic Service/API candidate-selection engine.\n"
    "Return only strict JSON matching the supplied output contract.\n"
    "Do not explain your answer."
)
TOP5_INSTRUCTION = (
    "Rank the five candidates most relevant to completing the user request. "
    "If fewer than five candidates are supplied, rank all candidates. "
    "Return each chosen candidate ID exactly once. "
    "Do not return any field other than ranked_candidate_ids."
)
SET_INSTRUCTION = (
    "Select the minimal sufficient set of candidates required to complete the user request. "
    "Include every necessary candidate and exclude candidates that are merely similar or unnecessary. "
    "Do not infer or reveal a target set size. Return only selected_candidate_ids."
)


def _load_contracts() -> Any:
    path = Path(__file__).with_name("output_contracts_v1_5.py")
    spec = importlib.util.spec_from_file_location("sdb_output_contracts_v1_5", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load output contracts: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CONTRACTS = _load_contracts()


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


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def append_jsonl(path: Path, value: Any, lock: threading.Lock) -> None:
    encoded = stable_json(value)
    with lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded + "\n")
            handle.flush()


def git_commit(root: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "UNKNOWN"


def load_keys() -> list[str]:
    keys = [os.environ.get(name, "") for name in KEY_ENV_NAMES]
    keys = [key for key in keys if key]
    if not keys:
        raise SystemExit("NO_QWEN_KEY: set SDB_QWEN_API_KEY_01..04")
    if len(keys) != len(set(keys)):
        raise SystemExit("duplicate Qwen API keys are forbidden")
    return keys


def validate_mode_arguments(
    *, mode: str, track: str, row_count: int, limit: int | None, request_id: str | None
) -> None:
    if mode == "formal":
        if track not in EXPECTED_FORMAL_ROWS:
            raise SystemExit("formal mode supports only machine or native")
        if limit is not None or request_id is not None:
            raise SystemExit("formal mode forbids --limit and --request-id")
        expected = EXPECTED_FORMAL_ROWS[track]
        if row_count != expected:
            raise SystemExit(f"formal {track} row count mismatch: {row_count} != {expected}")
    elif mode == "smoke":
        if track != "smoke":
            raise SystemExit("smoke mode requires --track smoke")
        if row_count != 60:
            raise SystemExit(f"smoke row count mismatch: {row_count} != 60")
        if limit is not None or request_id is not None:
            raise SystemExit("smoke mode forbids --limit and --request-id")
    elif mode == "diagnostic":
        if track not in {"machine", "native", "smoke"}:
            raise SystemExit("unsupported diagnostic track")
    else:
        raise SystemExit(f"unsupported mode: {mode}")


def assert_resume_namespace(output_dir: Path) -> None:
    normalized = str(output_dir.resolve()).replace("\\", "/").lower()
    if "llm_v0_2_qwen_sse_formal_v1_4" in normalized:
        raise SystemExit("V1.4 result directories cannot be resumed by V1.5")
    status_path = output_dir / "REQUEST_STATUS.jsonl"
    if not status_path.exists():
        return
    for line in status_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("experiment_revision") != REVISION:
            raise SystemExit("resume status does not belong to Qwen SSE Selection V1.5")


def _visible_from_row(row: dict[str, Any]) -> dict[str, Any]:
    if isinstance(row.get("model_visible_input"), dict):
        visible = row["model_visible_input"]
        query = visible.get("query", visible.get("query_text"))
        documents = visible.get("candidate_documents")
    elif isinstance(row.get("candidate_documents"), list):
        query = row.get("query", row.get("query_text"))
        documents = row["candidate_documents"]
    elif isinstance(row.get("messages"), list):
        user_messages = [item.get("content") for item in row["messages"] if item.get("role") == "user"]
        if len(user_messages) != 1 or not isinstance(user_messages[0], str) or "INPUT_JSON=" not in user_messages[0]:
            raise ValueError("smoke row lacks one INPUT_JSON user message")
        visible = json.loads(user_messages[0].split("INPUT_JSON=", 1)[1].strip())
        query = visible.get("query", visible.get("query_text"))
        documents = visible.get("candidate_documents")
    else:
        raise ValueError("row lacks model-visible candidate documents")
    if not isinstance(query, str) or not isinstance(documents, list):
        raise ValueError("invalid query or candidate_documents")
    return {"query": query, "candidate_documents": documents}


@dataclass(frozen=True)
class RequestItem:
    request_id: str
    track: str
    task_type: str
    prediction_target: str
    candidate_ids: list[str]
    candidate_documents: list[dict[str, Any]]
    contract: str
    payload: dict[str, Any]
    source_row_sha256: str
    candidate_order_sha256: str

    @property
    def request_sha256(self) -> str:
        return sha256_text(stable_json({
            "experiment_revision": REVISION,
            "payload": self.payload,
            "candidate_ids": self.candidate_ids,
            "source_row_sha256": self.source_row_sha256,
            "candidate_order_sha256": self.candidate_order_sha256,
            "contract": self.contract,
        }))


def build_payload(
    *, query: str, task_type: str, prediction_target: str,
    candidate_documents: list[dict[str, Any]], candidate_ids: list[str],
    contract: str, max_tokens: int,
) -> dict[str, Any]:
    if len(candidate_documents) != len(candidate_ids):
        raise ValueError("candidate document count differs from candidate ID count")
    for index, (document, candidate_id) in enumerate(zip(candidate_documents, candidate_ids, strict=True)):
        if not isinstance(document, dict) or document.get("candidate_id") != candidate_id:
            raise ValueError(f"candidate order mismatch at index {index}")
        if not isinstance(document.get("document"), str):
            raise ValueError(f"candidate document is not text at index {index}")
    if contract == CONTRACTS.TOP5_RANKING_V1:
        expected_k = min(5, len(candidate_ids))
        instruction = TOP5_INSTRUCTION
        output_contract = {
            "type": "object", "additionalProperties": False,
            "required": ["ranked_candidate_ids"],
            "properties": {"ranked_candidate_ids": {
                "type": "array", "items": {"type": "string"},
                "minItems": expected_k, "maxItems": expected_k, "uniqueItems": True,
            }},
        }
    elif contract == CONTRACTS.SELECTED_SET_V1:
        instruction = SET_INSTRUCTION
        output_contract = {
            "type": "object", "additionalProperties": False,
            "required": ["selected_candidate_ids"],
            "properties": {"selected_candidate_ids": {
                "type": "array", "items": {"type": "string"}, "uniqueItems": True,
            }},
        }
    else:
        raise ValueError(f"unknown output contract: {contract}")
    visible = {
        "query": query,
        "task_type": task_type,
        "prediction_target": prediction_target,
        "candidate_documents": candidate_documents,
        "instruction": instruction,
        "output_contract": output_contract,
    }
    return {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_MESSAGE},
            {"role": "user", "content": "INPUT_JSON=" + stable_json(visible)},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0,
        "top_p": 1,
        "n": 1,
        "seed": 0,
        "max_tokens": max_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
    }


def load_items(path: Path, track: str, max_tokens: int) -> list[RequestItem]:
    items: list[RequestItem] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        visible = _visible_from_row(row)
        documents = visible["candidate_documents"]
        candidate_ids = row.get("candidate_ids") or [item.get("candidate_id") for item in documents]
        if not isinstance(candidate_ids, list) or not candidate_ids or any(not isinstance(item, str) for item in candidate_ids):
            raise ValueError(f"invalid candidate IDs at line {line_number}")
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError(f"duplicate candidate ID at line {line_number}")
        task_type = row.get("task_type")
        prediction_target = row.get("prediction_target")
        if not isinstance(task_type, str) or not isinstance(prediction_target, str):
            raise ValueError(f"invalid task metadata at line {line_number}")
        mapping_track = "machine" if track == "machine" else "native"
        contract = CONTRACTS.contract_for(mapping_track, task_type)
        payload = build_payload(
            query=visible["query"], task_type=task_type, prediction_target=prediction_target,
            candidate_documents=documents, candidate_ids=candidate_ids,
            contract=contract, max_tokens=max_tokens,
        )
        if len(stable_json(payload).encode("utf-8")) > 8 * 1024 * 1024:
            raise ValueError(f"request body exceeds 8 MB at line {line_number}")
        request_id = row.get("benchmark_task_id", row.get("request_id"))
        if not isinstance(request_id, str) or not request_id:
            raise ValueError(f"missing request ID at line {line_number}")
        items.append(RequestItem(
            request_id=request_id, track=track, task_type=task_type,
            prediction_target=prediction_target, candidate_ids=candidate_ids,
            candidate_documents=documents, contract=contract, payload=payload,
            source_row_sha256=sha256_text(stable_json(row)),
            candidate_order_sha256=row.get("candidate_order_hash") or sha256_text("\n".join(candidate_ids)),
        ))
    return items


@dataclass
class SSEOutcome:
    http_status: int | None
    final_response: dict[str, Any] | None
    heartbeat_count: int
    sse_event_count: int
    terminal_event_received: bool
    done_received: bool
    finish_reason: str | None
    first_event_latency_ms: float | None = None
    first_data_latency_ms: float | None = None
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False


def _heartbeat(event_name: str | None, data: str, decoded: Any = None) -> bool:
    labels = {"heartbeat", "ping", "keepalive", "keep-alive"}
    if event_name and event_name.lower() in labels:
        return True
    if data.strip().lower() in labels:
        return True
    if isinstance(decoded, dict):
        return str(decoded.get("type", decoded.get("event", ""))).lower() in labels or decoded.get("heartbeat") is True
    return False


def _consume_frame(data: str, event_name: str | None, state: dict[str, Any]) -> tuple[str, str | None]:
    if data == "[DONE]":
        state["done"] = True
        return "done", None
    if _heartbeat(event_name, data):
        state["heartbeats"] += 1
        return "heartbeat", None
    try:
        decoded = json.loads(data)
    except json.JSONDecodeError:
        return "error", "INVALID_SSE_JSON"
    if _heartbeat(event_name, data, decoded):
        state["heartbeats"] += 1
        return "heartbeat", None
    if not isinstance(decoded, dict) or event_name == "error" or decoded.get("error") is not None:
        return "error", "SSE_ERROR_EVENT"
    state["response_model"] = decoded.get("model", state.get("response_model"))
    state["usage"] = decoded.get("usage", state.get("usage"))
    choices = decoded.get("choices")
    if isinstance(choices, list) and choices:
        choice = choices[0] if isinstance(choices[0], dict) else {}
        delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
        if isinstance(delta.get("content"), str):
            state["content"].append(delta["content"])
        reasoning = delta.get("reasoning_content", delta.get("reasoning"))
        if isinstance(reasoning, str):
            state["reasoning"].append(reasoning)
        if choice.get("finish_reason") is not None:
            state["finish_reason"] = str(choice["finish_reason"])
            state["terminal"] = True
    state["events"] += 1
    return "data", None


class SelectionRunner:
    def __init__(self, base_url: str, keys: list[str], output_dir: Path, concurrency: int, provenance: dict[str, Any]) -> None:
        self.base_url = base_url.rstrip("/")
        self.keys = keys
        self.output_dir = output_dir
        self.concurrency = min(concurrency, len(keys))
        self.provenance = provenance
        self.clients = [httpx.Client(timeout=None, trust_env=False, follow_redirects=True) for _ in keys]
        self.write_lock = threading.Lock()

    def close(self) -> None:
        for client in self.clients:
            client.close()

    def send_stream(self, item: RequestItem, slot: int) -> SSEOutcome:
        started = time.perf_counter()
        state: dict[str, Any] = {
            "content": [], "reasoning": [], "heartbeats": 0, "events": 0,
            "terminal": False, "done": False, "finish_reason": None,
            "response_model": None, "usage": None,
        }
        first_event = None
        first_data = None
        event_name: str | None = None
        data_lines: list[str] = []
        try:
            timeout = httpx.Timeout(connect=CONNECT_TIMEOUT_SECONDS, read=READ_TIMEOUT_SECONDS, write=60.0, pool=60.0)
            with self.clients[slot].stream(
                "POST", self.base_url + "/chat/completions",
                headers={"Authorization": f"Bearer {self.keys[slot]}", "Accept": "text/event-stream"},
                json=item.payload, timeout=timeout,
            ) as response:
                if response.status_code != 200:
                    retryable = response.status_code in RETRYABLE_HTTP or 500 <= response.status_code <= 599
                    return SSEOutcome(response.status_code, None, 0, 0, False, False, None,
                                      error_code=f"HTTP_{response.status_code}", error_message="non-200 response", retryable=retryable)
                for line in response.iter_lines():
                    now = time.perf_counter()
                    if now - started > MAX_WALL_SECONDS:
                        return SSEOutcome(200, None, state["heartbeats"], state["events"], state["terminal"], state["done"], state["finish_reason"],
                                          error_code="MAX_WALL_TIME_EXCEEDED", error_message="request exceeded frozen wall limit", retryable=True)
                    if first_event is None:
                        first_event = (now - started) * 1000
                    if line == "":
                        if data_lines:
                            kind, error = _consume_frame("\n".join(data_lines), event_name, state)
                            if kind == "data" and first_data is None:
                                first_data = (time.perf_counter() - started) * 1000
                            if error:
                                return SSEOutcome(200, None, state["heartbeats"], state["events"], state["terminal"], state["done"], state["finish_reason"],
                                                  first_event, first_data, error, "SSE error event", True)
                        event_name, data_lines = None, []
                    elif line.startswith(":"):
                        state["heartbeats"] += 1
                    elif line.startswith("event:"):
                        event_name = line[6:].strip()
                    elif line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
                if data_lines:
                    _consume_frame("\n".join(data_lines), event_name, state)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            return SSEOutcome(None, None, state["heartbeats"], state["events"], state["terminal"], state["done"], state["finish_reason"],
                              first_event, first_data, "TRANSPORT_ERROR", f"{type(exc).__name__}: {exc}", True)
        if not state["terminal"] or not state["done"]:
            return SSEOutcome(200, None, state["heartbeats"], state["events"], state["terminal"], state["done"], state["finish_reason"],
                              first_event, first_data, "INCOMPLETE_SSE_TERMINATION", "terminal event and [DONE] are required", True)
        message: dict[str, Any] = {"content": "".join(state["content"])}
        if state["reasoning"]:
            message["reasoning_content"] = "".join(state["reasoning"])
        final = {
            "model": state["response_model"],
            "choices": [{"message": message, "finish_reason": state["finish_reason"]}],
            "usage": state["usage"],
        }
        return SSEOutcome(200, final, state["heartbeats"], state["events"], True, True, state["finish_reason"], first_event, first_data)

    def run_one(self, item: RequestItem, slot: int) -> dict[str, Any]:
        started = time.perf_counter()
        attempts: list[dict[str, Any]] = []
        outcome: SSEOutcome | None = None
        for attempt in range(1, len(RETRY_BACKOFF_SECONDS) + 2):
            outcome = self.send_stream(item, slot)
            attempts.append({
                "attempt": attempt, "http_status": outcome.http_status,
                "error_code": outcome.error_code, "retryable": outcome.retryable,
                "heartbeat_count": outcome.heartbeat_count,
                "sse_event_count": outcome.sse_event_count,
                "terminal_event_received": outcome.terminal_event_received,
                "done_received": outcome.done_received,
            })
            if outcome.error_code is None or not outcome.retryable or attempt > len(RETRY_BACKOFF_SECONDS):
                break
            time.sleep(RETRY_BACKOFF_SECONDS[attempt - 1])
        assert outcome is not None
        base = {
            **self.provenance,
            "experiment_revision": REVISION,
            "request_id": item.request_id,
            "track": item.track,
            "task_type": item.task_type,
            "prediction_target": item.prediction_target,
            "output_contract": item.contract,
            "candidate_count": len(item.candidate_ids),
            "candidate_order_sha256": item.candidate_order_sha256,
            "source_row_sha256": item.source_row_sha256,
            "request_sha256": item.request_sha256,
            "attempt_count": len(attempts),
            "retry_count": len(attempts) - 1,
            "attempts": attempts,
            "http_status": outcome.http_status,
            "heartbeat_count": outcome.heartbeat_count,
            "sse_event_count": outcome.sse_event_count,
            "terminal_event_received": outcome.terminal_event_received,
            "done_received": outcome.done_received,
            "finish_reason": outcome.finish_reason,
            "first_event_latency_ms": outcome.first_event_latency_ms,
            "first_data_latency_ms": outcome.first_data_latency_ms,
            "end_to_end_latency_ms": round((time.perf_counter() - started) * 1000, 3),
            "completed_at_utc": utc_now(),
        }
        artifact = self.output_dir / "artifacts" / sha256_text(item.request_id)[:24]
        artifact.mkdir(parents=True, exist_ok=True)
        atomic_json(artifact / "request.json", item.payload)
        if outcome.error_code is not None:
            status = "infra_error" if outcome.retryable else "api_error"
            row = {**base, "status": status, "parse_status": "not_attempted", "error_code": outcome.error_code, "error_message": outcome.error_message}
        elif outcome.final_response is None or outcome.final_response.get("model") != MODEL:
            row = {**base, "status": "api_error", "parse_status": "not_attempted", "error_code": "MODEL_IDENTITY_MISMATCH", "error_message": "response model differs from frozen model"}
        else:
            atomic_json(artifact / "response.json", outcome.final_response)
            if item.contract == CONTRACTS.TOP5_RANKING_V1:
                parsed = CONTRACTS.parse_topk_response(outcome.final_response, item.candidate_ids, min(5, len(item.candidate_ids)))
            else:
                parsed = CONTRACTS.parse_selected_set_response(outcome.final_response, item.candidate_ids)
            if parsed.valid:
                atomic_json(artifact / "parsed_prediction.json", parsed.data)
                row = {**base, "status": "succeeded", "parse_status": "valid", "error_code": None, "error_message": None,
                       "parsed_prediction_path": str((artifact / "parsed_prediction.json").relative_to(self.output_dir)).replace("\\", "/")}
            else:
                row = {**base, "status": "parse_failure", "parse_status": "invalid", "error_code": parsed.error_code, "error_message": parsed.error_message}
        return row

    def run(self, items: list[RequestItem], mode: str) -> dict[str, Any]:
        status_path = self.output_dir / "REQUEST_STATUS.jsonl"
        completed: dict[str, dict[str, Any]] = {}
        if status_path.exists():
            for line in status_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    completed[row["request_id"]] = row
        pending = [item for item in items if item.request_id not in completed]
        with ThreadPoolExecutor(max_workers=self.concurrency, thread_name_prefix="qwen-selection-v1-5") as pool:
            futures = {pool.submit(self.run_one, item, index % len(self.keys)): item for index, item in enumerate(pending)}
            for future in as_completed(futures):
                row = future.result()
                append_jsonl(status_path, row, self.write_lock)
                completed[row["request_id"]] = row
        rows = [completed[item.request_id] for item in items if item.request_id in completed]
        counts = Counter(row["status"] for row in rows)
        unresolved = counts["infra_error"] + counts["api_error"]
        if mode == "diagnostic":
            status = "DIAGNOSTIC_PARTIAL"
        elif mode == "smoke":
            per_task = {task: Counter(row["parse_status"] for row in rows if row["task_type"] == task) for task in sorted({row["task_type"] for row in rows})}
            top_max = max((row["candidate_count"] for row in rows if row["output_contract"] == CONTRACTS.TOP5_RANKING_V1), default=0)
            set_max = max((row["candidate_count"] for row in rows if row["output_contract"] == CONTRACTS.SELECTED_SET_V1), default=0)
            gate = (
                len(rows) == 60 and unresolved == 0 and counts["succeeded"] >= 54
                and all(counter["valid"] >= 8 for counter in per_task.values())
                and any(row["candidate_count"] == top_max and row["status"] == "succeeded" for row in rows if row["output_contract"] == CONTRACTS.TOP5_RANKING_V1)
                and any(row["candidate_count"] == set_max and row["status"] == "succeeded" for row in rows if row["output_contract"] == CONTRACTS.SELECTED_SET_V1)
            )
            status = "COMPLETE_ALL_PARSED" if gate and counts["parse_failure"] == 0 else "COMPLETE_WITH_MODEL_FAILURES" if gate else "BLOCKED_SELECTION_CONTRACT_SMOKE"
        elif unresolved:
            status = "BLOCKED_INFRASTRUCTURE" if counts["infra_error"] else "BLOCKED_API_OR_MODEL_IDENTITY"
        elif len(rows) != len(items):
            status = "BLOCKED_INPUT_OR_CONTRACT"
        else:
            status = "COMPLETE_ALL_PARSED" if counts["parse_failure"] == 0 else "COMPLETE_WITH_MODEL_FAILURES"
        summary = {
            **self.provenance,
            "experiment_revision": REVISION,
            "status": status,
            "mode": mode,
            "requested_rows": len(items),
            "terminal_rows": len(rows),
            "status_counts": dict(sorted(counts.items())),
            "generated_at_utc": utc_now(),
        }
        atomic_json(self.output_dir / "RUN_SUMMARY.json", summary)
        return summary


def load_budget(path: Path, track: str, source: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("status") != "PASS" or value.get("model") != MODEL or value.get("tokenizer_revision") != TOKENIZER_REVISION:
        raise SystemExit("token budget freeze identity mismatch")
    budget_track = "native" if track == "smoke" else track
    record = value.get("tracks", {}).get(budget_track)
    if not isinstance(record, dict) or int(record.get("frozen_max_tokens", 0)) < 1:
        raise SystemExit(f"token budget freeze lacks {budget_track}")
    source_hash = sha256_file(source)
    bound_hashes = record.get("allowed_source_manifest_sha256", [])
    if bound_hashes and source_hash not in bound_hashes:
        raise SystemExit("source manifest is not bound by token budget freeze")
    return {"frozen_max_tokens": int(record["frozen_max_tokens"]), "budget_freeze_sha256": sha256_file(path), "source_manifest_sha256": source_hash}


def main() -> None:
    parser = argparse.ArgumentParser(description="Qwen SSE Selection V1.5 runner")
    parser.add_argument("--mode", choices=("smoke", "formal", "diagnostic"), required=True)
    parser.add_argument("--track", choices=("smoke", "machine", "native"), required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--budget-freeze", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--request-id")
    args = parser.parse_args()
    if not args.input.is_file() or not args.budget_freeze.is_file():
        raise SystemExit("input and budget freeze must exist")
    base_url = os.environ.get("SDB_QWEN_BASE_URL", "").strip()
    if not base_url:
        raise SystemExit("SDB_QWEN_BASE_URL is required; no live endpoint is hard-coded")
    if os.environ.get("SDB_QWEN_MODEL", MODEL) != MODEL:
        raise SystemExit("SDB_QWEN_MODEL differs from the frozen model")
    raw_rows = [line for line in args.input.read_text(encoding="utf-8").splitlines() if line.strip()]
    validate_mode_arguments(mode=args.mode, track=args.track, row_count=len(raw_rows), limit=args.limit, request_id=args.request_id)
    assert_resume_namespace(args.output_dir)
    budget = load_budget(args.budget_freeze, args.track, args.input)
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
    prompt = Path(__file__).resolve().parents[1] / "prompts" / "SELECTION_PROMPT_CONTRACT_V1_5.md"
    registry = Path(__file__).resolve().parents[1] / "schemas" / "TASK_OUTPUT_CONTRACT_REGISTRY_V1_5.json"
    provenance = {
        "git_commit_sha": git_commit(root),
        "runner_sha256": sha256_file(Path(__file__)),
        "prompt_contract_sha256": sha256_file(prompt),
        "output_contract_registry_sha256": sha256_file(registry),
        "model": MODEL,
        "tokenizer_revision": TOKENIZER_REVISION,
        **budget,
    }
    keys = load_keys()
    runner = SelectionRunner(base_url, keys, args.output_dir, args.concurrency, provenance)
    try:
        summary = runner.run(items, args.mode)
    finally:
        runner.close()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    raise SystemExit(0 if summary["status"] in {"COMPLETE_ALL_PARSED", "COMPLETE_WITH_MODEL_FAILURES", "DIAGNOSTIC_PARTIAL"} else 2)


if __name__ == "__main__":
    main()
