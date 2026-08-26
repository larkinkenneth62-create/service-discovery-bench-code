from __future__ import annotations

import importlib.util
import json
import sys
import threading
import time
from collections import Counter
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[2] / "experiments" / "llm_v0_2_qwen38_sse_selection_v1_6" / "code" / "run_qwen38_sse_selection_v1_6.py"
SPEC = importlib.util.spec_from_file_location("runner_qwen38_v1_6_tested", PATH)
assert SPEC and SPEC.loader
R = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = R
SPEC.loader.exec_module(R)


def item(index: int) -> object:
    return R.RequestItem(
        request_id=f"synthetic-{index}",
        track="native",
        task_type="single_service_discovery",
        prediction_target="service",
        candidate_ids=["c1"],
        candidate_documents=[{"candidate_id": "c1", "document": "synthetic"}],
        contract=R.CONTRACTS.TOP5_RANKING_V1,
        payload={"model": R.MODEL},
        source_row_sha256=f"source-{index}",
        candidate_order_sha256=f"order-{index}",
    )


def test_formal_counts_and_filters_are_guarded():
    R.validate_mode_arguments(mode="formal", track="machine", row_count=197, limit=None, request_id=None)
    R.validate_mode_arguments(mode="formal", track="native", row_count=4798, limit=None, request_id=None)
    with pytest.raises(SystemExit):
        R.validate_mode_arguments(mode="formal", track="machine", row_count=196, limit=None, request_id=None)
    with pytest.raises(SystemExit):
        R.validate_mode_arguments(mode="formal", track="native", row_count=4797, limit=None, request_id=None)
    with pytest.raises(SystemExit):
        R.validate_mode_arguments(mode="formal", track="machine", row_count=197, limit=1, request_id=None)
    with pytest.raises(SystemExit):
        R.validate_mode_arguments(mode="formal", track="native", row_count=4798, limit=None, request_id="one")


def test_diagnostic_allows_single_request():
    R.validate_mode_arguments(mode="diagnostic", track="native", row_count=1, limit=1, request_id="one")


def test_v1_4_resume_namespace_is_rejected(tmp_path):
    path = tmp_path / "llm_v0_2_qwen_sse_formal_v1_4" / "result"
    with pytest.raises(SystemExit):
        R.assert_resume_namespace(path)


def test_qwen36_v1_5_resume_namespace_is_rejected(tmp_path):
    path = tmp_path / "llm_v0_2_qwen_sse_selection_v1_5" / "result"
    with pytest.raises(SystemExit):
        R.assert_resume_namespace(path)


def test_prompt_payload_has_only_allowed_visible_fields():
    documents = [{"candidate_id": "c1", "document": "synthetic capability"}]
    payload = R.build_payload(
        query="synthetic query", task_type="single_service", prediction_target="service",
        candidate_documents=documents, candidate_ids=["c1"], contract=R.CONTRACTS.TOP5_RANKING_V1, max_tokens=64,
    )
    user = payload["messages"][1]["content"]
    for forbidden in ("gold", "split", "source_path", "retrieval_coverage", "review"):
        assert forbidden not in user.lower()
    assert payload["stream"] is True
    assert payload["model"] == "qwen3.8-27b-fp8"
    assert payload["chat_template_kwargs"] == {
        "enable_thinking": False,
        "preserve_thinking": False,
    }


def test_non_thinking_response_gate_is_fail_closed():
    assert R.non_thinking_response_ok({
        "model": R.MODEL,
        "choices": [{"message": {"content": "{}"}}],
    })
    assert not R.non_thinking_response_ok({
        "model": R.MODEL,
        "choices": [{"message": {"content": "{}", "reasoning_content": "hidden"}}],
    })
    assert not R.non_thinking_response_ok({
        "model": "wrong-model",
        "choices": [{"message": {"content": "{}"}}],
    })


def test_sse_heartbeat_and_reasoning_are_separate():
    state = {"content": [], "reasoning": [], "heartbeats": 0, "events": 0, "terminal": False, "done": False, "finish_reason": None, "response_model": None, "usage": None}
    assert R._consume_frame("{}", "heartbeat", state)[0] == "heartbeat"
    data = '{"model":"' + R.MODEL + '","choices":[{"delta":{"content":"{}","reasoning_content":"hidden"},"finish_reason":"stop"}]}'
    assert R._consume_frame(data, None, state)[0] == "data"
    assert R._consume_frame("[DONE]", None, state)[0] == "done"
    assert state["heartbeats"] == 1 and state["terminal"] and state["done"]
    assert state["content"] == ["{}"] and state["reasoning"] == ["hidden"]


def test_parse_failure_is_not_retryable_infrastructure():
    result = R.CONTRACTS.parse_topk_response({"choices": [{"message": {"content": '{"ranked_candidate_ids":[]}'}}]}, ["c1"], 1)
    assert not result.valid
    assert result.error_code == "TOPK_LENGTH_MISMATCH"


def test_one_worker_per_key_never_overlaps_same_slot(tmp_path, monkeypatch):
    runner = R.SelectionRunner("https://example.invalid/v1", ["k0", "k1", "k2", "k3"], tmp_path, 4, {})
    active: Counter[int] = Counter()
    peak: Counter[int] = Counter()
    lock = threading.Lock()

    def fake_run_one(request_item, slot):
        with lock:
            active[slot] += 1
            peak[slot] = max(peak[slot], active[slot])
        time.sleep(0.01)
        with lock:
            active[slot] -= 1
        return {
            "experiment_revision": R.REVISION,
            "request_id": request_item.request_id,
            "status": "succeeded",
            "parse_status": "valid",
            "task_type": request_item.task_type,
            "prediction_target": request_item.prediction_target,
            "output_contract": request_item.contract,
            "candidate_count": 1,
            "slot_index": slot,
        }

    monkeypatch.setattr(runner, "run_one", fake_run_one)
    summary = runner.run([item(index) for index in range(12)], "formal")
    runner.close()
    assert summary["status"] == "COMPLETE_ALL_PARSED"
    assert all(value <= 1 for value in peak.values())
    assert set(peak) == {0, 1, 2, 3}


def test_duplicate_resume_status_is_rejected(tmp_path):
    row = {"experiment_revision": R.REVISION, "request_id": "dup", "status": "succeeded"}
    (tmp_path / "REQUEST_STATUS.jsonl").write_text(json.dumps(row) + "\n" + json.dumps(row) + "\n", encoding="utf-8")
    runner = R.SelectionRunner("https://example.invalid/v1", ["k0"], tmp_path, 1, {})
    with pytest.raises(ValueError, match="duplicate request ID"):
        runner.run([item(0)], "diagnostic")
    runner.close()
