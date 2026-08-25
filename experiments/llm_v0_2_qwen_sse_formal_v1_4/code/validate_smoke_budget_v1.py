from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from pathlib import Path

from transformers import AutoTokenizer


BUDGET_SCRIPT = Path(__file__).resolve().parent / "freeze_token_budgets_v1.py"
SPEC = importlib.util.spec_from_file_location("sdb_qwen_budget", BUDGET_SCRIPT)
assert SPEC is not None and SPEC.loader is not None
BUDGET = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = BUDGET
SPEC.loader.exec_module(BUDGET)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", type=Path, required=True)
    parser.add_argument("--tokenizer-snapshot", type=Path, required=True)
    parser.add_argument("--native-budget-freeze", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    tokenizer = AutoTokenizer.from_pretrained(args.tokenizer_snapshot, local_files_only=True, trust_remote_code=False)
    items = list(BUDGET.RUNNER.FROZEN.iter_smoke(args.smoke, BUDGET.RUNNER.MODEL))
    counts = []
    inputs = []
    native_freeze = json.loads(args.native_budget_freeze.read_text(encoding="utf-8"))
    pretty_envelope = native_freeze.get("output_serialization_envelope") == "sort_keys=true, indent=2"
    for item in items:
        if pretty_envelope:
            value = {"ranked_candidate_ids": list(item.candidate_ids)}
            if item.require_selected:
                value["selected_candidate_ids"] = list(item.candidate_ids)
            output = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)
        else:
            output = BUDGET.legal_output(item)
        counts.append((len(tokenizer.encode(output, add_special_tokens=False)), item.request_id))
        inputs.append((BUDGET.input_tokens(tokenizer, item), item.request_id))
    worst_output = max(counts)
    worst_input = max(inputs)
    native_max_tokens = native_freeze["tracks"]["native"]["frozen_max_tokens"]
    smoke_needed = math.ceil(worst_output[0] * 1.10)
    report = {
        "schema_version": 1,
        "status": "PASS" if native_max_tokens >= smoke_needed else "FAIL",
        "rows": len(items),
        "smoke_source_sha256": BUDGET.sha256_file(args.smoke),
        "max_input_tokens": worst_input[0],
        "max_input_task_id": worst_input[1],
        "worst_legal_output_tokens": worst_output[0],
        "worst_legal_output_task_id": worst_output[1],
        "smoke_required_with_10_percent_margin": smoke_needed,
        "applied_native_frozen_max_tokens": native_max_tokens,
        "budget_sufficient": native_max_tokens >= smoke_needed,
    }
    BUDGET.RUNNER.atomic_json(args.output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["status"] == "PASS" else 2)


if __name__ == "__main__":
    main()
