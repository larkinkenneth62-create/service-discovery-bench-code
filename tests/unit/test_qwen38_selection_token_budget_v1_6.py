from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

PATH = Path(__file__).resolve().parents[2] / "experiments" / "llm_v0_2_qwen38_sse_selection_v1_6" / "code" / "freeze_selection_token_budgets_v1_6.py"
SPEC = importlib.util.spec_from_file_location("budget_qwen38_v1_6_tested", PATH)
assert SPEC and SPEC.loader
B = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = B
SPEC.loader.exec_module(B)


def write_rows(path: Path, values) -> None:
    path.write_text("".join(json.dumps(value) + "\n" for value in values), encoding="utf-8")


def row(task_type: str, count: int) -> dict:
    return {
        "task_type": task_type,
        "candidate_ids": [f"candidate-{index:03d}-with-a-long-id" for index in range(count)],
    }


def test_qwen38_selected_set_budget_is_full_pool_and_gold_independent(tmp_path):
    native = tmp_path / "native.jsonl"
    machine = tmp_path / "machine.jsonl"
    smoke = tmp_path / "smoke.jsonl"
    write_rows(native, [row("single_service_discovery", 8), row("composable_api_recommendation", 37)])
    write_rows(machine, [row("machine_challenge", 10)])
    write_rows(smoke, [row("multi_service_discovery", 12), row("single_api_recommendation", 9)])
    result = B.freeze(
        native_path=native,
        machine_path=machine,
        smoke_path=smoke,
        token_count=lambda value: len(value.encode("utf-8")),
    )
    ids = row("composable_api_recommendation", 37)["candidate_ids"]
    expected = B.json_array_upper_bound_tokens(
        "selected_candidate_ids", ids, lambda value: len(value.encode("utf-8"))
    )
    assert result["experiment_revision"] == "QWEN38_SSE_SELECTION_V1_6"
    assert result["model"] == "qwen3.8-27b-fp8"
    assert result["token_counter_revision"] == "UTF8_BYTE_UPPER_BOUND_V1"
    assert result["statistics"]["native_selected_set_full_pool_json_tokens_max"] >= expected
    assert result["tracks"]["native"]["frozen_max_tokens"] >= expected + B.SAFETY_MARGIN_TOKENS
    assert result["dev_gold_read"] is False and result["test_gold_read"] is False
