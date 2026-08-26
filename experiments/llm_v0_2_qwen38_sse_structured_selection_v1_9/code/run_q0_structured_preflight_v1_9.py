from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any


def _load_runner() -> Any:
    path = Path(__file__).with_name("run_qwen38_sse_structured_selection_v1_9.py")
    spec = importlib.util.spec_from_file_location("sdb_qwen38_structured_selection_v1_9_preflight_runner", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load runner: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


R = _load_runner()
Q0_ROUNDS = 3
Q0_KEY_SLOTS = 4
Q0_CONTRACTS_PER_SLOT = 2
Q0_EXPECTED_REQUESTS = Q0_ROUNDS * Q0_KEY_SLOTS * Q0_CONTRACTS_PER_SLOT


def _case(
    *,
    round_index: int,
    slot_index: int,
    name: str,
    task_type: str,
    prediction_target: str,
    query: str,
    documents: list[dict[str, str]],
    contract: str,
) -> Any:
    candidate_ids = [item["candidate_id"] for item in documents]
    variant = f"r{round_index}-s{slot_index}-{name}"
    payload = R.build_payload(
        query=f"{query} Synthetic audit variant {variant}.",
        task_type=task_type,
        prediction_target=prediction_target,
        candidate_documents=documents,
        candidate_ids=candidate_ids,
        contract=contract,
        max_tokens=1024,
    )
    return R.RequestItem(
        request_id=f"synthetic-q0-{variant}",
        track="smoke",
        task_type=task_type,
        prediction_target=prediction_target,
        candidate_ids=candidate_ids,
        candidate_documents=documents,
        contract=contract,
        payload=payload,
        source_row_sha256=R.sha256_text(f"synthetic-source-{variant}"),
        candidate_order_sha256=R.sha256_text("\n".join(candidate_ids)),
    )


def synthetic_cases(round_index: int = 1, slot_index: int = 1) -> list[tuple[str, Any]]:
    prefix = f"synthetic-r{round_index}-s{slot_index}"
    top5_documents = [
        {"candidate_id": f"{prefix}-status", "document": "Retrieve the current status of a synthetic service."},
        {"candidate_id": f"{prefix}-metrics", "document": "Read synthetic service latency and error metrics."},
        {"candidate_id": f"{prefix}-logs", "document": "Search synthetic service diagnostic logs."},
        {"candidate_id": f"{prefix}-alerts", "document": "List active synthetic service alerts."},
        {"candidate_id": f"{prefix}-history", "document": "Read historical synthetic service incidents."},
    ]
    selected_set_documents = [
        {"candidate_id": f"{prefix}-validator", "document": "Validate a supplied JSON document against a schema."},
        {"candidate_id": f"{prefix}-hasher", "document": "Calculate a SHA-256 digest for validated text."},
        {"candidate_id": f"{prefix}-weather", "document": "Return a synthetic weather forecast."},
        {"candidate_id": f"{prefix}-translator", "document": "Translate synthetic text between languages."},
    ]
    return [
        (
            "top5",
            _case(
                round_index=round_index,
                slot_index=slot_index,
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
                round_index=round_index,
                slot_index=slot_index,
                name="selected-set",
                task_type="multi_service_preflight",
                prediction_target="service",
                query="Validate a JSON document and then calculate its SHA-256 digest.",
                documents=selected_set_documents,
                contract=R.CONTRACTS.SELECTED_SET_V1,
            ),
        ),
    ]


def _run_slot(
    *,
    base_url: str,
    key: str,
    root: Path,
    runtime_provenance: dict[str, Any],
    round_index: int,
    slot_index: int,
    concurrency_mode: str,
) -> list[dict[str, Any]]:
    slot_dir = root / f"round-{round_index}" / f"slot-{slot_index}"
    cases = synthetic_cases(round_index, slot_index)
    runner = R.SelectionRunner(base_url, [key], slot_dir, 1, runtime_provenance)
    try:
        runner.run([item for _, item in cases], "diagnostic")
    finally:
        runner.close()
    R.assert_resume_namespace(slot_dir)
    rows = R.read_jsonl(slot_dir / "REQUEST_STATUS.jsonl")
    by_id = {row["request_id"]: row for row in rows}
    results: list[dict[str, Any]] = []
    for case_name, item in cases:
        row = by_id[item.request_id]
        results.append({
            "round": round_index,
            "key_slot": slot_index,
            "concurrency_mode": concurrency_mode,
            "case": case_name,
            "request_id": item.request_id,
            "request_sha256": item.request_sha256,
            "output_contract": item.contract,
            "candidate_count": len(item.candidate_ids),
            "status": row.get("status"),
            "parse_status": row.get("parse_status"),
            "error_code": row.get("error_code"),
            "attempt_count": row.get("attempt_count"),
            "retry_count": row.get("retry_count"),
            "http_status": row.get("http_status"),
            "heartbeat_count": row.get("heartbeat_count"),
            "terminal_event_received": row.get("terminal_event_received"),
            "done_received": row.get("done_received"),
            "finish_reason": row.get("finish_reason"),
            "response_model": row.get("response_model"),
            "reasoning_content_present": row.get("reasoning_content_present"),
            "reasoning_channel_status": row.get("reasoning_channel_status"),
            "reasoning_char_count": row.get("reasoning_char_count"),
            "reasoning_sha256": row.get("reasoning_sha256"),
            "content_bytes": row.get("content_bytes"),
            "content_sha256": row.get("content_sha256"),
            "response_format_type": row.get("response_format_type"),
            "response_schema_name": row.get("response_schema_name"),
            "response_schema_strict": row.get("response_schema_strict"),
            "raw_sse_events_path": f"round-{round_index}/slot-{slot_index}/{row.get('raw_sse_events_path')}",
            "raw_sse_events_sha256": row.get("raw_sse_events_sha256"),
            "response_path": f"round-{round_index}/slot-{slot_index}/{row.get('response_path')}" if row.get("response_path") else None,
            "response_sha256": row.get("response_sha256"),
        })
    return results


def evaluate_q0_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    coverage = {(row.get("round"), row.get("key_slot"), row.get("output_contract")) for row in results}
    expected_coverage = {
        (round_index, slot_index, contract)
        for round_index in range(1, Q0_ROUNDS + 1)
        for slot_index in range(1, Q0_KEY_SLOTS + 1)
        for contract in (R.CONTRACTS.TOP5_RANKING_V1, R.CONTRACTS.SELECTED_SET_V1)
    }
    status_counts = {
        status: sum(row.get("status") == status for row in results)
        for status in ("succeeded", "parse_failure", "infra_error", "api_error")
    }
    per_contract_success = {
        contract: sum(
            row.get("output_contract") == contract and row.get("status") == "succeeded"
            for row in results
        )
        for contract in (R.CONTRACTS.TOP5_RANKING_V1, R.CONTRACTS.SELECTED_SET_V1)
    }
    per_slot_success = {
        str(slot): sum(row.get("key_slot") == slot and row.get("status") == "succeeded" for row in results)
        for slot in range(1, Q0_KEY_SLOTS + 1)
    }
    hard_transport_pass = (
        len(results) == Q0_EXPECTED_REQUESTS
        and coverage == expected_coverage
        and len({row.get("request_sha256") for row in results}) == Q0_EXPECTED_REQUESTS
        and status_counts["infra_error"] == 0
        and status_counts["api_error"] == 0
        and all(
            row.get("status") in {"succeeded", "parse_failure"}
            and row.get("http_status") == 200
            and int(row.get("heartbeat_count") or 0) >= 1
            and row.get("terminal_event_received") is True
            and row.get("done_received") is True
            and row.get("finish_reason") == "stop"
            and row.get("response_model") == R.MODEL
            and row.get("response_format_type") == "json_schema"
            and row.get("response_schema_strict") is True
            for row in results
        )
    )
    format_gate_pass = (
        status_counts["succeeded"] >= 22
        and all(value >= 10 for value in per_contract_success.values())
        and all(value >= 5 for value in per_slot_success.values())
    )
    passed = hard_transport_pass and format_gate_pass
    if passed and status_counts["succeeded"] == Q0_EXPECTED_REQUESTS:
        status = "PASS_ALL_PARSED"
    elif passed:
        status = "PASS_WITH_MODEL_FORMAT_FINDING"
    else:
        status = "FAIL"
    return {
        "status": status,
        "hard_transport_gate_pass": hard_transport_pass,
        "format_feasibility_gate_pass": format_gate_pass,
        "status_counts": status_counts,
        "per_contract_success": per_contract_success,
        "per_slot_success": per_slot_success,
        "thresholds": {
            "requests": 24,
            "min_overall_success": 22,
            "min_success_per_contract": 10,
            "min_success_per_slot": 5,
            "infra_error_allowed": 0,
            "api_error_allowed": 0,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Q0 model-failure-accounting preflight for Qwen3.8 Structured Selection V1.9")
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
    if len(keys) != Q0_KEY_SLOTS:
        raise SystemExit("Q0 requires exactly four distinct API key slots")

    root = args.output.parent
    if args.output.exists() or any((root / f"round-{index}").exists() for index in range(1, Q0_ROUNDS + 1)):
        raise SystemExit("Q0 output namespace is not empty; use a new directory")
    results: list[dict[str, Any]] = []

    # Round 1 is globally serial while still exercising every credential slot.
    for slot_index, key in enumerate(keys, 1):
        results.extend(_run_slot(
            base_url=base_url,
            key=key,
            root=root,
            runtime_provenance=runtime_provenance,
            round_index=1,
            slot_index=slot_index,
            concurrency_mode="serial_global_1",
        ))

    # Rounds 2 and 3 are four-way concurrent, with one sequential worker per key.
    for round_index in (2, 3):
        with ThreadPoolExecutor(max_workers=Q0_KEY_SLOTS, thread_name_prefix=f"q0-v1-9-round-{round_index}") as pool:
            futures = [
                pool.submit(
                    _run_slot,
                    base_url=base_url,
                    key=key,
                    root=root,
                    runtime_provenance=runtime_provenance,
                    round_index=round_index,
                    slot_index=slot_index,
                    concurrency_mode="concurrent_global_4",
                )
                for slot_index, key in enumerate(keys, 1)
            ]
            for future in as_completed(futures):
                results.extend(future.result())

    results.sort(key=lambda row: (row["round"], row["key_slot"], row["case"]))
    gate = evaluate_q0_results(results)
    passed = gate["status"] in {"PASS_ALL_PARSED", "PASS_WITH_MODEL_FORMAT_FINDING"}
    report = {
        "schema_version": 5,
        "experiment_revision": R.REVISION,
        "status": gate["status"],
        "model": R.MODEL,
        "official_model_id": R.OFFICIAL_MODEL_ID,
        "thinking_mode": R.THINKING_MODE,
        "response_format_mode": R.RESPONSE_FORMAT_MODE,
        "reasoning_channel_policy": R.REASONING_CHANNEL_POLICY,
        "content_policy": R.CONTENT_POLICY,
        "structured_output_policy": R.STRUCTURED_OUTPUT_POLICY,
        "q0_requests": len(results),
        "benchmark_rows_transmitted": 0,
        "required_rounds": Q0_ROUNDS,
        "required_key_slots": Q0_KEY_SLOTS,
        "required_contracts": [R.CONTRACTS.TOP5_RANKING_V1, R.CONTRACTS.SELECTED_SET_V1],
        "concurrency_schedule": {"round_1": "serial_global_1", "round_2": "concurrent_global_4", "round_3": "concurrent_global_4"},
        "request_contract": {
            "stream": True,
            "stream_options_include_usage": True,
            "response_format": "dynamic_strict_json_schema_with_candidate_enum",
            "temperature": 0,
            "top_p": 1,
            "n": 1,
            "seed": 0,
            "enable_thinking": True,
            "preserve_thinking": True,
            "max_tokens": 1024,
            "max_attempts": R.MAX_ATTEMPTS,
        },
        "q0_gate": gate,
        **runtime_provenance,
        "runner_sha256": R.sha256_file(Path(__file__).with_name("run_qwen38_sse_structured_selection_v1_9.py")),
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
