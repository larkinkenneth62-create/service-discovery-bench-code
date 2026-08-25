from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from argparse import Namespace
from collections import Counter
from pathlib import Path


RUNNER_PATH = Path(__file__).resolve().parent / "run_qwen_sse_formal_v1.py"
SPEC = importlib.util.spec_from_file_location("sdb_qwen_sse_q1_audit", RUNNER_PATH)
assert SPEC is not None and SPEC.loader is not None
RUNNER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = RUNNER
SPEC.loader.exec_module(RUNNER)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--budget-freeze", type=Path, required=True)
    parser.add_argument("--smoke-budget-validation", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    keys = RUNNER.load_keys()
    binding_args = Namespace(
        track="smoke", input=args.input, budget_freeze=args.budget_freeze,
        smoke_budget_validation=args.smoke_budget_validation,
    )
    binding = RUNNER.load_budget_binding(binding_args)
    items = list(RUNNER.FROZEN.iter_smoke(args.input, RUNNER.MODEL))
    for item in items:
        item.payload.update({"stream": True, "temperature": 0, "top_p": 1, "n": 1, "seed": 0,
                             "max_tokens": binding["frozen_max_tokens"]})
    runner = RUNNER.FormalSSERunner("audit://offline", keys, args.run_dir, 1, 3, binding)
    try:
        status = runner.existing()
        artifact_errors = []
        for item in items:
            row = status.get(item.request_id)
            if row is None:
                artifact_errors.append(f"missing status: {item.request_id}")
                continue
            try:
                runner.validate_reusable(item, row)
            except SystemExit as exc:
                artifact_errors.append(str(exc))
    finally:
        runner.close()
    rows = [status.get(item.request_id, {}) for item in items]
    counts = Counter(row.get("status", "missing") for row in rows)
    long_rows = [row for row in rows if row.get("candidate_count") == 199]
    checks = {
        "exactly_60_frozen_requests": len(items) == 60 and len(status) == 60,
        "all_60_terminal_model_status": all(row.get("status") in {"succeeded", "parse_failure"} for row in rows),
        "no_infrastructure_or_api_failure": not counts.get("infra_error") and not counts.get("api_error") and not counts.get("missing"),
        "all_terminal_event_received": all(row.get("terminal_event_received") is True for row in rows),
        "all_done_received": all(row.get("done_received") is True for row in rows),
        "all_response_models_exact": all(row.get("response_model") == RUNNER.MODEL for row in rows),
        "no_output_length_finish": all(row.get("finish_reason") != "length" for row in rows),
        "all_10_long_native_rows_have_heartbeat": len(long_rows) == 10 and all(int(row.get("heartbeat_count", 0)) > 0 for row in long_rows),
        "artifact_chain_complete_and_hash_bound": not artifact_errors,
        "no_credentials_persisted": RUNNER.secrets_absent(args.run_dir, keys),
        "frozen_max_tokens_exact": all(row.get("frozen_max_tokens") == binding["frozen_max_tokens"] for row in rows),
        "resume_cache_evidence_valid": not artifact_errors,
    }
    gate = {
        "schema_version": 1,
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "request_count": len(items),
        "status_counts": dict(sorted(counts.items())),
        "parse_failure_is_nonblocking_by_protocol": True,
        "heartbeat_count_total": sum(int(row.get("heartbeat_count", 0)) for row in rows),
        "long_native_request_ids": [row.get("request_id") for row in long_rows],
        "artifact_errors": artifact_errors,
        "budget_binding": binding,
    }
    RUNNER.atomic_json(args.output, gate)
    print(json.dumps(gate, ensure_ascii=False, indent=2))
    raise SystemExit(0 if gate["status"] == "PASS" else 2)


if __name__ == "__main__":
    main()
