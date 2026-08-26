from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any


def _load_runner():
    path = Path(__file__).with_name("run_qwen38_sse_thinking_selection_v1_7.py")
    spec = importlib.util.spec_from_file_location("sdb_qwen38_thinking_selection_v1_7_preflight_runner", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load runner: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


R = _load_runner()


def _case(
    *,
    name: str,
    task_type: str,
    prediction_target: str,
    query: str,
    documents: list[dict[str, str]],
    contract: str,
) -> R.RequestItem:
    candidate_ids = [item["candidate_id"] for item in documents]
    payload = R.build_payload(
        query=query,
        task_type=task_type,
        prediction_target=prediction_target,
        candidate_documents=documents,
        candidate_ids=candidate_ids,
        contract=contract,
        max_tokens=1024,
    )
    return R.RequestItem(
        request_id=f"synthetic-q0-{name}",
        track="smoke",
        task_type=task_type,
        prediction_target=prediction_target,
        candidate_ids=candidate_ids,
        candidate_documents=documents,
        contract=contract,
        payload=payload,
        source_row_sha256=R.sha256_text(f"synthetic-q0-{name}"),
        candidate_order_sha256=R.sha256_text("\n".join(candidate_ids)),
    )


def synthetic_cases() -> list[tuple[str, R.RequestItem]]:
    top5_documents = [
        {"candidate_id": "synthetic-top5-status", "document": "Retrieve the current status of a synthetic service."},
        {"candidate_id": "synthetic-top5-metrics", "document": "Read synthetic service latency and error metrics."},
        {"candidate_id": "synthetic-top5-logs", "document": "Search synthetic service diagnostic logs."},
        {"candidate_id": "synthetic-top5-alerts", "document": "List active synthetic service alerts."},
        {"candidate_id": "synthetic-top5-history", "document": "Read historical synthetic service incidents."},
    ]
    selected_set_documents = [
        {"candidate_id": "synthetic-set-validator", "document": "Validate a supplied JSON document against a schema."},
        {"candidate_id": "synthetic-set-hasher", "document": "Calculate a SHA-256 digest for validated text."},
        {"candidate_id": "synthetic-set-weather", "document": "Return a synthetic weather forecast."},
        {"candidate_id": "synthetic-set-translator", "document": "Translate synthetic text between languages."},
    ]
    return [
        (
            "top5",
            _case(
                name="top5",
                task_type="single_service_preflight",
                prediction_target="service",
                query="Rank the supplied synthetic observability tools for diagnosing a service incident.",
                documents=top5_documents,
                contract=R.CONTRACTS.TOP5_RANKING_V1,
            ),
        ),
        (
            "selected-set",
            _case(
                name="selected-set",
                task_type="multi_service_preflight",
                prediction_target="service",
                query="Validate a JSON document and then calculate its SHA-256 digest.",
                documents=selected_set_documents,
                contract=R.CONTRACTS.SELECTED_SET_V1,
            ),
        ),
    ]


def _message(final_response: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(final_response, dict):
        return None
    choices = final_response.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None
    value = choices[0].get("message")
    return value if isinstance(value, dict) else None


def main() -> None:
    parser = argparse.ArgumentParser(description="Q0 SSE preserved-thinking preflight for Qwen3.8 Selection V1.7")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runtime-freeze", type=Path, required=True)
    args = parser.parse_args()
    if not args.runtime_freeze.is_file():
        raise SystemExit("runtime freeze must exist")
    runtime_provenance = R.load_runtime_freeze(args.runtime_freeze)
    base_url = os.environ.get("SDB_QWEN_BASE_URL", "").strip()
    if not base_url:
        raise SystemExit("SDB_QWEN_BASE_URL is required")
    if os.environ.get("SDB_QWEN_MODEL", R.MODEL) != R.MODEL:
        raise SystemExit("SDB_QWEN_MODEL differs from the frozen Qwen3.8 served model")
    keys = R.load_keys()
    if len(keys) != 4:
        raise SystemExit("Q0 requires exactly four distinct API key slots")

    cases = synthetic_cases()
    results: list[dict[str, Any]] = []
    for slot, key in enumerate(keys, 1):
        slot_dir = args.output.parent / f"slot-{slot}"
        runner = R.SelectionRunner(base_url, [key], slot_dir, 1, runtime_provenance)
        try:
            for case_name, item in cases:
                outcome = runner.send_stream(item, 0)
                parsed = None
                if outcome.final_response is not None:
                    if item.contract == R.CONTRACTS.TOP5_RANKING_V1:
                        parsed = R.CONTRACTS.parse_topk_response(
                            outcome.final_response, item.candidate_ids, min(5, len(item.candidate_ids))
                        )
                    else:
                        parsed = R.CONTRACTS.parse_selected_set_response(outcome.final_response, item.candidate_ids)
                message = _message(outcome.final_response)
                content = message.get("content") if isinstance(message, dict) else None
                reasoning = (
                    message.get("reasoning_content")
                    if isinstance(message, dict)
                    else None
                )
                usage = outcome.final_response.get("usage") if outcome.final_response is not None else None
                case_path = case_name.replace("-", "_")
                raw_events_path = slot_dir / f"{case_path}_RAW_SSE_EVENTS.json"
                response_path = slot_dir / f"{case_path}_FINAL_RESPONSE.json"
                R.atomic_json(raw_events_path, outcome.raw_sse_events or [])
                if outcome.final_response is not None:
                    R.atomic_json(response_path, outcome.final_response)
                parsed_data = parsed.data if parsed is not None and parsed.valid else None
                results.append({
                    "key_slot": slot,
                    "case": case_name,
                    "output_contract": item.contract,
                    "request_sha256": item.request_sha256,
                    "candidate_order_sha256": item.candidate_order_sha256,
                    "candidate_count": len(item.candidate_ids),
                    "http_status": outcome.http_status,
                    "heartbeat_count": outcome.heartbeat_count,
                    "terminal_event_received": outcome.terminal_event_received,
                    "done_received": outcome.done_received,
                    "response_model": None if outcome.final_response is None else outcome.final_response.get("model"),
                    "final_content_present": isinstance(content, str) and bool(content.strip()),
                    "content_bytes": len(content.encode("utf-8")) if isinstance(content, str) else 0,
                    "content_sha256": R.sha256_text(content) if isinstance(content, str) else None,
                    "response_message_keys": sorted(message) if isinstance(message, dict) else [],
                    "reasoning_content_present": isinstance(reasoning, str) and bool(reasoning),
                    "reasoning_char_count": len(reasoning) if isinstance(reasoning, str) else 0,
                    "reasoning_sha256": R.sha256_text(reasoning) if isinstance(reasoning, str) else None,
                    "parse_valid": bool(parsed and parsed.valid),
                    "parse_error_code": None if parsed is None else parsed.error_code,
                    "parse_error_message": None if parsed is None else parsed.error_message,
                    "parsed_prediction": parsed_data,
                    "raw_sse_events_path": str(raw_events_path.relative_to(args.output.parent)).replace("\\", "/"),
                    "raw_sse_events_sha256": R.sha256_file(raw_events_path),
                    "final_response_path": (
                        str(response_path.relative_to(args.output.parent)).replace("\\", "/")
                        if response_path.is_file()
                        else None
                    ),
                    "final_response_sha256": R.sha256_file(response_path) if response_path.is_file() else None,
                    "usage_supported": isinstance(usage, dict) and bool(usage),
                    "usage_source": "sse_usage" if isinstance(usage, dict) and bool(usage) else "unavailable",
                    "usage": usage,
                    "first_event_latency_ms": outcome.first_event_latency_ms,
                    "first_data_latency_ms": outcome.first_data_latency_ms,
                    "max_output_tokens_requested": item.payload["max_tokens"],
                    "finish_reason": outcome.finish_reason,
                    "error_code": outcome.error_code,
                })
        finally:
            runner.close()

    passed = len(results) == 8 and all(
        row["http_status"] == 200
        and row["heartbeat_count"] >= 1
        and row["terminal_event_received"]
        and row["done_received"]
        and row["response_model"] == R.MODEL
        and row["final_content_present"]
        and row["reasoning_content_present"]
        and row["parse_valid"]
        and row["finish_reason"] == "stop"
        and row["error_code"] is None
        for row in results
    )
    report = {
        "schema_version": 3,
        "experiment_revision": R.REVISION,
        "status": "PASS" if passed else "FAIL",
        "model": R.MODEL,
        "official_model_id": R.OFFICIAL_MODEL_ID,
        "thinking_mode": R.THINKING_MODE,
        "reasoning_channel_policy": "saved_not_scored_content_must_remain_strict_json",
        "q0_requests": len(results),
        "benchmark_rows_transmitted": 0,
        "required_key_slots": 4,
        "required_contracts": [R.CONTRACTS.TOP5_RANKING_V1, R.CONTRACTS.SELECTED_SET_V1],
        "request_contract": {
            "stream": True,
            "stream_options_include_usage": True,
            "response_format": {"type": "json_object"},
            "temperature": 0,
            "top_p": 1,
            "n": 1,
            "seed": 0,
            "enable_thinking": True,
            "preserve_thinking": True,
            "max_tokens": 1024,
        },
        "runtime_capabilities": {
            "context_length": None,
            "server_runtime_version": None,
            "tokenizer_identity": None,
            "unavailable_fields_reason": "not_exposed_by_chat_completions_response",
        },
        **runtime_provenance,
        "runner_sha256": R.sha256_file(Path(__file__).with_name("run_qwen38_sse_thinking_selection_v1_7.py")),
        "q0_script_sha256": R.sha256_file(Path(__file__)),
        "parser_sha256": R.sha256_file(Path(__file__).with_name("output_contracts_v1_5.py")),
        "endpoint_sha256": hashlib.sha256(base_url.encode("utf-8")).hexdigest(),
        "api_keys_persisted": False,
        "results": results,
        "generated_at_utc": R.utc_now(),
    }
    R.atomic_json(args.output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if passed else 2)


if __name__ == "__main__":
    main()
