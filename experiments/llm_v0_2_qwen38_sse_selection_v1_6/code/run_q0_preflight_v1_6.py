from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path


def _load_runner():
    path = Path(__file__).with_name("run_qwen38_sse_selection_v1_6.py")
    spec = importlib.util.spec_from_file_location("sdb_qwen38_selection_v1_6_preflight_runner", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load runner: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


R = _load_runner()


def main() -> None:
    parser = argparse.ArgumentParser(description="Q0 SSE non-thinking preflight for Qwen3.8 Selection V1.6")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    base_url = os.environ.get("SDB_QWEN_BASE_URL", "").strip()
    if not base_url:
        raise SystemExit("SDB_QWEN_BASE_URL is required")
    if os.environ.get("SDB_QWEN_MODEL", R.MODEL) != R.MODEL:
        raise SystemExit("SDB_QWEN_MODEL differs from the frozen Qwen3.8 served model")
    keys = R.load_keys()
    payload = R.build_payload(
        query="Select the synthetic candidate.",
        task_type="single_service_preflight",
        prediction_target="service",
        candidate_documents=[{"candidate_id": "synthetic-preflight-candidate", "document": "Synthetic preflight capability."}],
        candidate_ids=["synthetic-preflight-candidate"],
        contract=R.CONTRACTS.TOP5_RANKING_V1,
        max_tokens=64,
    )
    item = R.RequestItem(
        request_id="synthetic-q0-preflight",
        track="smoke",
        task_type="single_service_preflight",
        prediction_target="service",
        candidate_ids=["synthetic-preflight-candidate"],
        candidate_documents=[{"candidate_id": "synthetic-preflight-candidate", "document": "Synthetic preflight capability."}],
        contract=R.CONTRACTS.TOP5_RANKING_V1,
        payload=payload,
        source_row_sha256=R.sha256_text("synthetic-q0-preflight"),
        candidate_order_sha256=R.sha256_text("synthetic-preflight-candidate"),
    )
    results = []
    for slot, key in enumerate(keys, 1):
        runner = R.SelectionRunner(base_url, [key], args.output.parent / f"slot-{slot}", 1, {})
        try:
            outcome = runner.send_stream(item, 0)
        finally:
            runner.close()
        parsed = None
        if outcome.final_response is not None:
            parsed = R.CONTRACTS.parse_topk_response(outcome.final_response, item.candidate_ids, 1)
        message = None
        if outcome.final_response is not None:
            choices = outcome.final_response.get("choices")
            if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                message = choices[0].get("message")
        content = message.get("content") if isinstance(message, dict) else None
        reasoning = (
            message.get("reasoning_content", message.get("reasoning"))
            if isinstance(message, dict) else None
        )
        usage = outcome.final_response.get("usage") if outcome.final_response is not None else None
        raw_events_path = args.output.parent / f"slot-{slot}" / "RAW_SSE_EVENTS.json"
        R.atomic_json(raw_events_path, outcome.raw_sse_events or [])
        results.append({
            "key_slot": slot,
            "http_status": outcome.http_status,
            "heartbeat_count": outcome.heartbeat_count,
            "terminal_event_received": outcome.terminal_event_received,
            "done_received": outcome.done_received,
            "response_model": None if outcome.final_response is None else outcome.final_response.get("model"),
            "final_content_present": isinstance(content, str) and bool(content.strip()),
            "final_content": content,
            "response_message_keys": sorted(message) if isinstance(message, dict) else [],
            "reasoning_content_absent_or_empty": reasoning in (None, ""),
            "parse_valid": bool(parsed and parsed.valid),
            "parse_error_code": None if parsed is None else parsed.error_code,
            "parse_error_message": None if parsed is None else parsed.error_message,
            "raw_sse_events_path": str(raw_events_path.relative_to(args.output.parent)).replace("\\", "/"),
            "raw_sse_events_sha256": R.sha256_file(raw_events_path),
            "usage_supported": isinstance(usage, dict) and bool(usage),
            "usage": usage,
            "first_event_latency_ms": outcome.first_event_latency_ms,
            "first_data_latency_ms": outcome.first_data_latency_ms,
            "max_output_tokens_requested": payload["max_tokens"],
            "error_code": outcome.error_code,
        })
    passed = all(
        row["http_status"] == 200 and row["terminal_event_received"] and row["done_received"]
        and row["response_model"] == R.MODEL and row["final_content_present"]
        and row["reasoning_content_absent_or_empty"] and row["parse_valid"]
        and row["error_code"] is None
        for row in results
    )
    report = {
        "schema_version": 1,
        "experiment_revision": R.REVISION,
        "status": "PASS" if passed else "FAIL",
        "model": R.MODEL,
        "official_model_id": R.OFFICIAL_MODEL_ID,
        "thinking_mode": "disabled",
        "request_contract": {
            "stream": True,
            "stream_options_include_usage": True,
            "temperature": 0,
            "top_p": 1,
            "n": 1,
            "seed": 0,
            "enable_thinking": False,
            "preserve_thinking": False,
        },
        "runtime_capabilities": {
            "context_length": None,
            "server_runtime_version": None,
            "tokenizer_identity": None,
            "unavailable_fields_reason": "not_exposed_by_chat_completions_response",
        },
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
