from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any


def _load_runner() -> Any:
    path = Path(__file__).with_name("run_deepseek_v4_flash_v2_2_r3_nonstream.py")
    spec = importlib.util.spec_from_file_location("sdb_deepseek_v2_2_q0_runner", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load runner: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


R = _load_runner()
CONTRACTS = (R.CONTRACTS.TOP5_RANKING_V1, R.CONTRACTS.SELECTED_SET_V1, R.CONTRACTS.RANKING_AND_SELECTED_SET_V1_10)


def synthetic_cases(max_tokens: int) -> list[R.RequestItem]:
    cases: list[R.RequestItem] = []
    specifications = [
        (R.CONTRACTS.TOP5_RANKING_V1, "single_service_discovery", "service", "Find the service that translates text."),
        (R.CONTRACTS.SELECTED_SET_V1, "multi_api_recommendation", "api", "Select lookup and balance APIs."),
        (R.CONTRACTS.RANKING_AND_SELECTED_SET_V1_10, "single_api_recommendation", "api", "Use lookup, balance, history, profile, alerts, and audit APIs."),
    ]
    for round_index in (1, 2):
        for contract, task, target, query in specifications:
            prefix = f"q0-r{round_index}-{contract.lower()}"
            documents = [
                {"candidate_id": f"{prefix}-{name}", "document": description}
                for name, description in (
                    ("lookup", "Look up an account."),
                    ("balance", "Return current balance."),
                    ("history", "Return transaction history."),
                    ("profile", "Return account profile."),
                    ("alerts", "Return account alerts."),
                    ("audit", "Return audit events."),
                    ("translate", "Translate text."),
                )
            ]
            ids = [row["candidate_id"] for row in documents]
            payload = R.build_payload(query=query, task_type=task, prediction_target=target, candidate_documents=documents, candidate_ids=ids, contract=contract, max_tokens=max_tokens)
            cases.append(
                R.RequestItem(
                    prefix,
                    "smoke",
                    task,
                    target,
                    ids,
                    contract,
                    payload,
                    R.sha256_text(prefix),
                    len(R.stable_json(payload).encode("utf-8")),
                    len(payload["messages"][1]["content"].encode("utf-8")),
                    len(R.stable_json(documents).encode("utf-8")),
                    R.SIZE.legal_answer_bound_bytes(contract, ids),
                )
            )
    return cases


def evaluate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {status: sum(row.get("status") == status for row in rows) for status in ("succeeded", "parse_failure", "infra_error", "api_error")}
    per_contract = {contract: sum(row.get("output_contract") == contract and row.get("status") == "succeeded" for row in rows) for contract in CONTRACTS}
    hard_provider_failures = {"OUTPUT_BUDGET_EXHAUSTED", "CONTENT_FILTERED", "UNEXPECTED_TOOL_CALL_FINISH"}
    passed = (len(rows) == 6 and counts["infra_error"] == 0 and counts["api_error"] == 0 and not any(row.get("error_code") in hard_provider_failures for row in rows) and all(row.get("status") in {"succeeded", "parse_failure"} and row.get("response_complete_received") is True and row.get("transport_protocol") == R.TRANSPORT_PROTOCOL for row in rows) and all(value >= 1 for value in per_contract.values()))
    return {"status": "PASS" if passed else "FAIL", "provider": "deepseek", "experiment_revision": R.REVISION, "implementation_revision": R.IMPLEMENTATION_REVISION, "terminal_rows": len(rows), "status_counts": counts, "per_contract_strict_parse": per_contract, "thresholds": {"requests": 6, "requests_per_contract": 2, "min_strict_parse_per_contract": 1, "unresolved_infrastructure_or_api_allowed": 0}}


def main() -> None:
    parser = argparse.ArgumentParser(description="DeepSeek V4 Flash V2.2 six-request synthetic Q0")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--budget-freeze", type=Path, required=True)
    parser.add_argument("--runtime-freeze", type=Path, required=True)
    parser.add_argument("--native-source-manifest", type=Path, required=True)
    args = parser.parse_args()
    R.assert_independent_namespace(args.output_dir)
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        raise SystemExit("Q0 output directory must be empty")
    base_url = os.environ.get(R.BASE_URL_ENV_NAME, "").strip()
    if not base_url:
        raise SystemExit(f"{R.BASE_URL_ENV_NAME} is required")
    budget = R.load_budget(args.budget_freeze, "native", args.native_source_manifest)
    runtime = R.load_runtime_freeze(args.runtime_freeze)
    provenance = {"model": R.MODEL, "implementation_revision": R.IMPLEMENTATION_REVISION, **runtime, **budget}
    runner = R.DeepSeekRunner(base_url=base_url, key=R.load_key(), output_dir=args.output_dir, concurrency=1, provenance=provenance)
    items = synthetic_cases(budget["frozen_max_tokens"])
    runner.run(items, "diagnostic")
    report = evaluate(R.read_jsonl(args.output_dir / "REQUEST_STATUS.jsonl"))
    report.update(
        {
            "runtime_freeze_sha256": R.sha256_file(args.runtime_freeze),
            "budget_freeze_sha256": R.sha256_file(args.budget_freeze),
            "native_source_manifest_sha256": R.sha256_file(args.native_source_manifest),
            "diagnostic_run_summary_sha256": R.sha256_file(args.output_dir / "RUN_SUMMARY.json"),
            "generated_at_utc": R.utc_now(),
        }
    )
    R.atomic_json(args.output_dir / "Q0_REPORT.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["status"] == "PASS" else 2)


if __name__ == "__main__":
    main()
