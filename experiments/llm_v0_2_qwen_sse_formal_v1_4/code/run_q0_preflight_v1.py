from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import httpx


RUNNER_PATH = Path(__file__).resolve().parent / "run_qwen_sse_formal_v1.py"
SPEC = importlib.util.spec_from_file_location("sdb_qwen_sse_formal", RUNNER_PATH)
assert SPEC is not None and SPEC.loader is not None
RUNNER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = RUNNER
SPEC.loader.exec_module(RUNNER)


def probe_tokenizer(client: httpx.Client, base_url: str, headers: dict[str, str]) -> list[dict[str, Any]]:
    origin = base_url.removesuffix("/v1").rstrip("/")
    urls = list(dict.fromkeys([f"{base_url.rstrip('/')}/tokenize", f"{origin}/tokenize"]))
    results = []
    for url in urls:
        try:
            response = client.post(url, headers=headers, json={"content": "candidate-001"}, timeout=30)
            try:
                body = response.json()
            except Exception:
                body = {"non_json_body": response.text[:1000]}
            results.append({"url": url, "http_status": response.status_code, "body": body})
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            results.append({"url": url, "http_status": None, "error": type(exc).__name__})
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--base-url", default=RUNNER.DEFAULT_BASE_URL)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    keys = RUNNER.load_keys()
    key = keys[0]
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    with httpx.Client(trust_env=False, http2=False, follow_redirects=True) as client:
        models_response = client.get(f"{args.base_url.rstrip('/')}/models", headers=headers, timeout=30)
        try:
            models_body = models_response.json()
        except Exception:
            models_body = {"non_json_body": models_response.text[:2000]}
        models_record = {
            "http_status": models_response.status_code,
            "body": models_body,
            "expected_model": RUNNER.MODEL,
            "expected_model_present": RUNNER.MODEL in [
                row.get("id") for row in models_body.get("data", []) if isinstance(row, dict)
            ] if isinstance(models_body, dict) else False,
        }
        RUNNER.secret_guard(models_record, keys)
        RUNNER.atomic_json(args.output_dir / "MODELS_RESPONSE.json", models_record)
        tokenizer_probe = probe_tokenizer(client, args.base_url, headers)
        RUNNER.secret_guard(tokenizer_probe, keys)
        RUNNER.atomic_json(args.output_dir / "TOKENIZER_ENDPOINT_PROBE.json", tokenizer_probe)

    visible = {
        "query": "Find the only candidate.",
        "task_type": "single_service_recommendation",
        "prediction_target": "service",
        "candidate_documents": [{"candidate_id": "candidate-001", "document": "Only candidate service."}],
        "instructions": "Rank all supplied candidate IDs. Return strict JSON and no explanation.",
        "output_schema": {
            "type": "object",
            "properties": {"ranked_candidate_ids": {"type": "array", "items": {"type": "string"}, "uniqueItems": True}},
            "required": ["ranked_candidate_ids"],
            "additionalProperties": False,
        },
    }
    payload = {
        "model": RUNNER.MODEL,
        "messages": [
            {"role": "system", "content": RUNNER.FROZEN.SYSTEM_PROMPT},
            {"role": "user", "content": "SETTING=q0_preflight\nINPUT_JSON=" + RUNNER.stable_json(visible) + "\n"},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0,
        "top_p": 1,
        "n": 1,
        "seed": 0,
        "max_tokens": 128,
        "stream": True,
    }
    item = RUNNER.FROZEN.RequestItem(
        request_id="q0-sse-preflight-002-fixed-runner",
        track="q0_preflight",
        task_type="single_service_recommendation",
        prediction_target="service",
        candidate_ids=["candidate-001"],
        require_selected=False,
        payload=payload,
        source_row_sha256=RUNNER.sha256_text(RUNNER.stable_json(visible)),
        candidate_order_sha256=RUNNER.sha256_text("candidate-001"),
    )
    canary_dir = args.output_dir / "CANARY_V2_FIXED_RUNNER"
    q0_binding = {
        "budget_freeze_sha256": "Q0_NOT_FORMAL_BUDGET",
        "frozen_max_tokens": 128,
        "tokenizer_revision": RUNNER.TOKENIZER_REVISION,
        "adapter_sha256": RUNNER.FROZEN_ADAPTER_SHA256,
    }
    runner = RUNNER.FormalSSERunner(args.base_url, [key], canary_dir, 1, 3, q0_binding)
    try:
        summary = runner.run([item], resume=True)
    finally:
        runner.close()
    status = runner.existing().get(item.request_id, {})
    gate_checks = {
        "models_http_200": models_record["http_status"] == 200,
        "expected_model_present": models_record["expected_model_present"],
        "streaming_request_succeeded": status.get("status") == "succeeded",
        "parse_valid": status.get("parse_status") == "valid",
        "error_null": status.get("error_code") is None,
        "response_model_exact": status.get("response_model") == RUNNER.MODEL,
        "heartbeat_observed": int(status.get("heartbeat_count", 0)) > 0,
        "terminal_event_received": status.get("terminal_event_received") is True,
        "done_received": status.get("done_received") is True,
        "raw_sse_log_present": (runner.artifact_dir(item) / "raw_sse_events.jsonl").is_file(),
        "no_credentials_persisted": RUNNER.secrets_absent(args.output_dir, keys),
    }
    gate = {
        "schema_version": 1,
        "status": "PASS" if all(gate_checks.values()) else "FAIL",
        "checks": gate_checks,
        "request_status": status,
        "run_summary": summary,
        "tokenizer_endpoint_probe_path": "TOKENIZER_ENDPOINT_PROBE.json",
    }
    RUNNER.secret_guard(gate, keys)
    RUNNER.atomic_json(args.output_dir / "Q0_GATE.json", gate)
    print(json.dumps(gate, ensure_ascii=False, indent=2))
    raise SystemExit(0 if gate["status"] == "PASS" else 2)


if __name__ == "__main__":
    main()
