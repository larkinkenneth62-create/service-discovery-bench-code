from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


MODEL = "deepseek-v4-flash"
REVISION = "DEEPSEEK_V4_FLASH_FULL_SIX_TASK_V2_2"
TOKEN_COUNTER_REVISION = "UTF8_BYTE_UPPER_BOUND_PLUS_REASONING_4096_V2_2"
SAFETY_MARGIN_TOKENS = 64
REASONING_ALLOWANCE_TOKENS = 4096


def _load_size_utils() -> Any:
    path = Path(__file__).with_name("contract_size_utils_v2_2.py")
    spec = importlib.util.spec_from_file_location("sdb_deepseek_contract_sizes_v2_2_budget", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load contract-size utilities: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


SIZE = _load_size_utils()
stable_json = SIZE.stable_json


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def read_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def candidate_ids(row: dict[str, Any]) -> list[str]:
    visible = row.get("model_visible_input", row)
    documents = visible.get("candidate_documents") if isinstance(visible, dict) else None
    values = row.get("candidate_ids") or ([item.get("candidate_id") for item in documents] if isinstance(documents, list) else None)
    if not isinstance(values, list) or not values or any(not isinstance(value, str) or not value for value in values) or len(values) != len(set(values)):
        raise ValueError("invalid candidate IDs")
    return values


def contract(row: dict[str, Any], track: str) -> str:
    task = row.get("task_type")
    if track == "machine" or task == "single_service_discovery":
        return "TOP5_RANKING_V1"
    if task == "single_api_recommendation":
        return "RANKING_AND_SELECTED_SET_V1_10"
    if isinstance(task, str) and task.startswith(("multi_", "composable_")):
        return "SELECTED_SET_V1"
    raise ValueError(f"unregistered task type: {task}")


def answer_bound(row: dict[str, Any], track: str) -> int:
    return SIZE.legal_answer_bound_bytes(contract(row, track), candidate_ids(row))


def freeze(native: Path, machine: Path, smoke: Path) -> dict[str, Any]:
    native_rows = read_rows(native)
    machine_rows = read_rows(machine)
    smoke_rows = read_rows(smoke)
    native_bounds = [answer_bound(row, "native") for row in native_rows + smoke_rows]
    machine_bounds = [answer_bound(row, "machine") for row in machine_rows]
    if not native_bounds or not machine_bounds:
        raise ValueError("empty Native/Smoke or Machine manifest")
    native_tokens = max(native_bounds) + SAFETY_MARGIN_TOKENS + REASONING_ALLOWANCE_TOKENS
    machine_tokens = max(machine_bounds) + SAFETY_MARGIN_TOKENS + REASONING_ALLOWANCE_TOKENS
    return {
        "schema_version": 1,
        "status": "PASS",
        "provider": "deepseek",
        "experiment_revision": REVISION,
        "model": MODEL,
        "token_counter_revision": TOKEN_COUNTER_REVISION,
        "token_counter_method": "UTF8_BYTES_AS_CONSERVATIVE_TOKEN_UPPER_BOUND",
        "reasoning_allowance_tokens": REASONING_ALLOWANCE_TOKENS,
        "safety_margin_tokens": SAFETY_MARGIN_TOKENS,
        "budget_principle": "ONE_REGISTRY_FOR_Q0_DEV_FORMAL; FULL_LEGAL_ANSWER_SPACE; NO_GOLD_CARDINALITY",
        "tracks": {
            "native": {"frozen_max_tokens": native_tokens, "allowed_source_manifest_sha256": [sha256_file(native), sha256_file(smoke)]},
            "machine": {"frozen_max_tokens": machine_tokens, "allowed_source_manifest_sha256": [sha256_file(machine)]},
        },
        "statistics": {"native_or_smoke_answer_bound_max": max(native_bounds), "machine_answer_bound_max": max(machine_bounds)},
        "dev_gold_read": False,
        "test_gold_read": False,
        "model_results_read": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Freeze DeepSeek V2.2 output budgets from provider-visible manifests")
    parser.add_argument("--native", type=Path, required=True)
    parser.add_argument("--machine", type=Path, required=True)
    parser.add_argument("--smoke", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = freeze(args.native, args.machine, args.smoke)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
