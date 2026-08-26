from __future__ import annotations

import importlib.util
import json
import sys
import threading
import time
from collections import Counter
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[2] / "experiments" / "llm_v0_2_qwen38_sse_thinking_selection_v1_7" / "code" / "run_qwen38_sse_thinking_selection_v1_7.py"
SPEC = importlib.util.spec_from_file_location("runner_qwen38_thinking_v1_7_tested", PATH)
assert SPEC and SPEC.loader
R = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = R
SPEC.loader.exec_module(R)

Q0_PATH = PATH.with_name("run_q0_thinking_preflight_v1_7.py")
Q0_SPEC = importlib.util.spec_from_file_location("q0_qwen38_thinking_v1_7_tested", Q0_PATH)
assert Q0_SPEC and Q0_SPEC.loader
Q0 = importlib.util.module_from_spec(Q0_SPEC)
sys.modules[Q0_SPEC.name] = Q0
Q0_SPEC.loader.exec_module(Q0)


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


def test_qwen38_non_thinking_v1_6_resume_namespace_is_rejected(tmp_path):
    path = tmp_path / "llm_v0_2_qwen38_sse_selection_v1_6" / "result"
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
        "enable_thinking": True,
        "preserve_thinking": True,
    }


def test_preserved_thinking_response_gate_is_fail_closed():
    response = {
        "model": R.MODEL,
        "choices": [{"message": {"content": "{}", "reasoning_content": "hidden"}}],
    }
    assert R.thinking_response_ok(response)
    assert R.reasoning_metadata(response)["reasoning_content_present"] is True
    assert not R.thinking_response_ok({
        "model": R.MODEL,
        "choices": [{"message": {"content": "{}", "reasoning_content": ["invalid"]}}],
    })
    assert not R.thinking_response_ok({
        "model": R.MODEL,
        "choices": [{"message": {"content": "{}", "reasoning_content": ""}}],
    })
    assert not R.thinking_response_ok({
        "model": "wrong-model",
        "choices": [{"message": {"content": "{}", "reasoning_content": "hidden"}}],
    })


def test_reasoning_text_cannot_leak_into_scored_content():
    response = {
        "choices": [{"message": {
            "content": 'analysis first {"ranked_candidate_ids":["c1"]}',
            "reasoning_content": "separate reasoning",
        }}],
    }
    parsed = R.CONTRACTS.parse_topk_response(response, ["c1"], 1)
    assert not parsed.valid and parsed.error_code == "INVALID_JSON"


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


def test_q0_covers_both_contracts_without_benchmark_rows():
    cases = Q0.synthetic_cases()
    assert [name for name, _ in cases] == ["top5", "selected-set"]
    assert {request.contract for _, request in cases} == {
        R.CONTRACTS.TOP5_RANKING_V1,
        R.CONTRACTS.SELECTED_SET_V1,
    }
    top5 = cases[0][1]
    assert len(top5.candidate_ids) == 5
    assert top5.payload["max_tokens"] == 1024
    assert all(request.request_id.startswith("synthetic-q0-") for _, request in cases)


def test_runtime_freeze_is_fail_closed(tmp_path):
    source = PATH.parents[1] / "schemas" / "QWEN38_SSE_THINKING_RUNTIME_FREEZE_V1_7.json"
    valid = tmp_path / "valid.json"
    valid.write_bytes(source.read_bytes())
    provenance = R.load_runtime_freeze(valid)
    assert provenance["runtime_freeze_sha256"] == R.sha256_file(valid)
    value = json.loads(valid.read_text(encoding="utf-8"))
    value["request_contract"]["chat_template_kwargs"]["preserve_thinking"] = False
    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(SystemExit, match="thinking channel"):
        R.load_runtime_freeze(invalid)


def test_non_stop_finish_reason_is_terminal_model_failure(tmp_path, monkeypatch):
    response = {
        "model": R.MODEL,
        "choices": [{
            "message": {"content": '{"ranked_candidate_ids":["c1"]}', "reasoning_content": "hidden"},
            "finish_reason": "length",
        }],
        "usage": {},
    }
    outcome = R.SSEOutcome(200, response, 1, 2, True, True, "length", raw_sse_events=[])
    runner = R.SelectionRunner("https://example.invalid/v1", ["k0"], tmp_path, 1, {})
    monkeypatch.setattr(runner, "send_stream", lambda request_item, slot: outcome)
    result = runner.run_one(item(0), 0)
    runner.close()
    assert result["status"] == "parse_failure"
    assert result["error_code"] == "NON_STOP_FINISH_REASON"


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
