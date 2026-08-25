from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


PATH = Path(__file__).resolve().parents[2] / "experiments" / "llm_v0_2_qwen_sse_selection_v1_5" / "code" / "run_qwen_sse_selection_v1_5.py"
SPEC = importlib.util.spec_from_file_location("runner_v1_5_tested", PATH)
assert SPEC and SPEC.loader
R = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = R
SPEC.loader.exec_module(R)


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
