from __future__ import annotations

import importlib.util
import json
import sys
import threading
import time
from collections import Counter
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[2] / "experiments" / "llm_v0_2_qwen38_sse_structured_selection_v1_9" / "code" / "run_qwen38_sse_structured_selection_v1_9.py"
SPEC = importlib.util.spec_from_file_location("runner_qwen38_structured_v1_9_tested", PATH)
assert SPEC and SPEC.loader
R = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = R
SPEC.loader.exec_module(R)

Q0_PATH = PATH.with_name("run_q0_structured_preflight_v1_9.py")
Q0_SPEC = importlib.util.spec_from_file_location("q0_qwen38_structured_v1_9_tested", Q0_PATH)
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


def test_qwen38_thinking_v1_7_resume_namespace_is_rejected(tmp_path):
    path = tmp_path / "llm_v0_2_qwen38_sse_thinking_selection_v1_7" / "result"
    with pytest.raises(SystemExit):
        R.assert_resume_namespace(path)


def test_qwen38_v1_8_resume_namespace_is_rejected(tmp_path):
    path = tmp_path / "llm_v0_2_qwen38_sse_thinking_structured_selection_v1_8" / "result"
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
    response_format = payload["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    assert response_format["json_schema"]["schema"]["properties"]["ranked_candidate_ids"]["items"]["enum"] == ["c1"]
    visible = json.loads(user.split("INPUT_JSON=", 1)[1])
    assert "enum" not in visible["output_contract"]["properties"]["ranked_candidate_ids"]["items"]


def test_response_envelope_allows_optional_reasoning_and_rejects_bad_envelopes():
    response = {
        "model": R.MODEL,
        "choices": [{"message": {"content": "{}", "reasoning_content": "hidden"}}],
    }
    assert R.response_envelope_ok(response)
    assert R.reasoning_metadata(response)["reasoning_content_present"] is True
    assert R.reasoning_metadata(response)["reasoning_channel_status"] == "present"
    without_reasoning = {"model": R.MODEL, "choices": [{"message": {"content": "{}"}}]}
    assert R.response_envelope_ok(without_reasoning)
    assert R.reasoning_metadata(without_reasoning)["reasoning_channel_status"] == "absent"
    assert R.response_envelope_ok({"model": R.MODEL, "choices": [{"message": {"content": ""}}]})
    assert R.response_envelope_ok({"model": R.MODEL, "choices": [{"message": {"content": "  \n"}}]})
    assert R.response_envelope_ok({
        "model": R.MODEL,
        "choices": [{"message": {"content": "{}", "reasoning_content": ["invalid"]}}],
    })
    assert R.response_envelope_ok({
        "model": R.MODEL,
        "choices": [{"message": {"content": "{}", "reasoning_content": ""}}],
    })
    assert not R.response_envelope_ok({
        "model": "wrong-model",
        "choices": [{"message": {"content": "{}", "reasoning_content": "hidden"}}],
    })
    assert not R.response_envelope_ok({
        "model": R.MODEL,
        "choices": [
            {"message": {"content": "{}", "reasoning_content": "a"}},
            {"message": {"content": "{}", "reasoning_content": "b"}},
        ],
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


def test_q0_covers_24_distinct_requests_without_benchmark_rows():
    cases = [
        (round_index, slot_index, name, request)
        for round_index in range(1, 4)
        for slot_index in range(1, 5)
        for name, request in Q0.synthetic_cases(round_index, slot_index)
    ]
    assert len(cases) == 24
    assert len({request.request_sha256 for _, _, _, request in cases}) == 24
    assert {request.contract for _, _, _, request in cases} == {
        R.CONTRACTS.TOP5_RANKING_V1,
        R.CONTRACTS.SELECTED_SET_V1,
    }
    top5 = cases[0][3]
    assert len(top5.candidate_ids) == 5
    assert top5.payload["max_tokens"] == 1024
    assert all(request.request_id.startswith("synthetic-q0-") for _, _, _, request in cases)
    assert Q0.Q0_EXPECTED_REQUESTS == 24


def q0_rows(*, failures: set[tuple[int, int, str]] | None = None, api_error_at: tuple[int, int, str] | None = None):
    failures = failures or set()
    rows = []
    for round_index in range(1, 4):
        for slot in range(1, 5):
            for contract in (R.CONTRACTS.TOP5_RANKING_V1, R.CONTRACTS.SELECTED_SET_V1):
                identity = (round_index, slot, contract)
                status = "api_error" if identity == api_error_at else "parse_failure" if identity in failures else "succeeded"
                rows.append({
                    "round": round_index,
                    "key_slot": slot,
                    "output_contract": contract,
                    "request_sha256": f"{round_index}-{slot}-{contract}",
                    "status": status,
                    "http_status": 200,
                    "heartbeat_count": 1,
                    "terminal_event_received": True,
                    "done_received": True,
                    "finish_reason": "stop",
                    "response_model": R.MODEL,
                    "response_format_type": "json_schema",
                    "response_schema_strict": True,
                })
    return rows


def test_q0_23_of_24_can_pass_with_model_format_finding():
    failure = {(1, 1, R.CONTRACTS.TOP5_RANKING_V1)}
    assert Q0.evaluate_q0_results(q0_rows(failures=failure))["status"] == "PASS_WITH_MODEL_FORMAT_FINDING"


def test_q0_21_of_24_fails():
    failures = {
        (1, 1, R.CONTRACTS.TOP5_RANKING_V1),
        (1, 2, R.CONTRACTS.TOP5_RANKING_V1),
        (1, 3, R.CONTRACTS.SELECTED_SET_V1),
    }
    assert Q0.evaluate_q0_results(q0_rows(failures=failures))["status"] == "FAIL"


def test_q0_any_api_error_fails():
    identity = (1, 1, R.CONTRACTS.TOP5_RANKING_V1)
    assert Q0.evaluate_q0_results(q0_rows(api_error_at=identity))["status"] == "FAIL"


def test_q0_contract_below_10_of_12_fails():
    failures = {
        (1, 1, R.CONTRACTS.TOP5_RANKING_V1),
        (2, 2, R.CONTRACTS.TOP5_RANKING_V1),
        (3, 3, R.CONTRACTS.TOP5_RANKING_V1),
    }
    assert Q0.evaluate_q0_results(q0_rows(failures=failures))["status"] == "FAIL"


def test_q0_slot_below_5_of_6_fails():
    failures = {
        (1, 1, R.CONTRACTS.TOP5_RANKING_V1),
        (2, 1, R.CONTRACTS.SELECTED_SET_V1),
    }
    assert Q0.evaluate_q0_results(q0_rows(failures=failures))["status"] == "FAIL"


def test_runtime_freeze_is_fail_closed(tmp_path):
    source = PATH.parents[1] / "schemas" / "QWEN38_SSE_STRUCTURED_RUNTIME_FREEZE_V1_9.json"
    valid = tmp_path / "valid.json"
    valid.write_bytes(source.read_bytes())
    provenance = R.load_runtime_freeze(valid)
    assert provenance["runtime_freeze_sha256"] == R.sha256_file(valid)
    value = json.loads(valid.read_text(encoding="utf-8"))
    value["request_contract"]["chat_template_kwargs"]["preserve_thinking"] = False
    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(SystemExit, match="runtime freeze must request preserved thinking"):
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
    monkeypatch.setattr(runner, "send_stream", lambda request_item, slot, raw_sse_path=None: outcome)
    result = runner.run_one(item(0), 0)
    runner.close()
    assert result["status"] == "parse_failure"
    assert result["error_code"] == "NON_STOP_FINISH_REASON"


@pytest.mark.parametrize("reasoning", ["hidden", None])
def test_valid_json_succeeds_with_or_without_reasoning(tmp_path, monkeypatch, reasoning):
    message = {"content": '{"ranked_candidate_ids":["c1"]}'}
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    response = {"model": R.MODEL, "choices": [{"message": message, "finish_reason": "stop"}], "usage": {}}
    outcome = R.SSEOutcome(200, response, 1, 2, True, True, "stop", raw_sse_events=[])
    runner = R.SelectionRunner("https://example.invalid/v1", ["k0"], tmp_path, 1, {})
    monkeypatch.setattr(runner, "send_stream", lambda request_item, slot, raw_sse_path=None: outcome)
    result = runner.run_one(item(0), 0)
    runner.close()
    assert result["status"] == "succeeded"
    assert result["reasoning_channel_status"] == ("present" if reasoning else "absent")


@pytest.mark.parametrize("content", ["", "  \n", "analysis text", 'analysis {"ranked_candidate_ids":["c1"]}'])
def test_invalid_full_content_is_parse_failure_without_retry(tmp_path, monkeypatch, content):
    response = {"model": R.MODEL, "choices": [{"message": {"content": content}, "finish_reason": "stop"}], "usage": {}}
    outcome = R.SSEOutcome(200, response, 1, 2, True, True, "stop", raw_sse_events=[])
    runner = R.SelectionRunner("https://example.invalid/v1", ["k0"], tmp_path, 1, {})
    monkeypatch.setattr(runner, "send_stream", lambda request_item, slot, raw_sse_path=None: outcome)
    result = runner.run_one(item(0), 0)
    runner.close()
    assert result["status"] == "parse_failure"
    assert result["parse_status"] == "invalid"
    assert result["attempt_count"] == 1 and result["retry_count"] == 0


def test_route_and_protocol_revision_metadata_are_explicit_and_route_is_inherited():
    experiment = PATH.parents[1]
    route = json.loads((experiment / "00_GOVERNANCE" / "ROUTE_INVARIANTS.json").read_text(encoding="utf-8"))
    provenance = json.loads((experiment / "RUN_PROVENANCE.json").read_text(encoding="utf-8"))
    expected_route = "QWEN38_SSE_THINKING_STRUCTURED_SELECTION_V1_8"
    expected_protocol = "SDB_RETRIEVER_AND_LLM_EXECUTION_PROTOCOL_V1_6_QWEN38_SSE_MODEL_FAILURE_ACCOUNTING_NATIVE_MACHINE_FIRST_FROZEN"
    assert route["experiment_revision"] == R.REVISION
    assert route["route_revision"] == expected_route
    assert route["protocol_revision"] == expected_protocol
    assert route["route_changed"] is False
    assert provenance["route_revision"] == expected_route
    assert provenance["protocol_revision"] == expected_protocol


def test_model_mismatch_is_hard_api_error(tmp_path, monkeypatch):
    response = {"model": "wrong-model", "choices": [{"message": {"content": '{"ranked_candidate_ids":["c1"]}'}, "finish_reason": "stop"}], "usage": {}}
    outcome = R.SSEOutcome(200, response, 1, 2, True, True, "stop", raw_sse_events=[])
    runner = R.SelectionRunner("https://example.invalid/v1", ["k0"], tmp_path, 1, {})
    monkeypatch.setattr(runner, "send_stream", lambda request_item, slot, raw_sse_path=None: outcome)
    result = runner.run_one(item(0), 0)
    runner.close()
    assert result["status"] == "api_error"
    assert result["error_code"] == "MODEL_IDENTITY_MISMATCH"


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


def test_smoke_gate_keeps_54_overall_and_8_per_task_thresholds(tmp_path, monkeypatch):
    task_types = [
        "single_service_discovery", "single_api_recommendation",
        "multi_service_discovery", "multi_api_recommendation",
        "composable_service_discovery", "composable_api_recommendation",
    ]
    items = []
    failure_ids = set()
    for task_index, task_type in enumerate(task_types):
        contract = R.CONTRACTS.TOP5_RANKING_V1 if task_type.startswith("single_") else R.CONTRACTS.SELECTED_SET_V1
        for row_index in range(10):
            request_id = f"smoke-{task_index}-{row_index}"
            if row_index == 0:
                failure_ids.add(request_id)
            items.append(R.RequestItem(
                request_id=request_id, track="smoke", task_type=task_type,
                prediction_target="api" if "api" in task_type else "service",
                candidate_ids=["c1"], candidate_documents=[{"candidate_id": "c1", "document": "synthetic"}],
                contract=contract, payload={"model": R.MODEL}, source_row_sha256=request_id,
                candidate_order_sha256=request_id,
            ))

    runner = R.SelectionRunner("https://example.invalid/v1", ["k0", "k1", "k2", "k3"], tmp_path, 4, {})

    def fake_run_one(request_item, slot):
        failed = request_item.request_id in failure_ids
        return {
            "experiment_revision": R.REVISION, "request_id": request_item.request_id,
            "status": "parse_failure" if failed else "succeeded",
            "parse_status": "invalid" if failed else "valid",
            "task_type": request_item.task_type, "prediction_target": request_item.prediction_target,
            "output_contract": request_item.contract, "candidate_count": len(request_item.candidate_ids),
            "slot_index": slot,
        }

    monkeypatch.setattr(runner, "run_one", fake_run_one)
    summary = runner.run(items, "smoke")
    runner.close()
    assert summary["status"] == "COMPLETE_WITH_MODEL_FAILURES"
    assert summary["status_counts"] == {"parse_failure": 6, "succeeded": 54}


def test_duplicate_resume_status_is_rejected(tmp_path):
    row = {"experiment_revision": R.REVISION, "request_id": "dup", "status": "succeeded"}
    (tmp_path / "REQUEST_STATUS.jsonl").write_text(json.dumps(row) + "\n" + json.dumps(row) + "\n", encoding="utf-8")
    runner = R.SelectionRunner("https://example.invalid/v1", ["k0"], tmp_path, 1, {})
    with pytest.raises(SystemExit, match="duplicate resume request ID"):
        runner.run([item(0)], "diagnostic")
    runner.close()


def _ledger_row(request_id: str, event: str, attempt: int = 1) -> dict:
    return {
        "schema_version": R.ATTEMPT_LEDGER_SCHEMA_VERSION,
        "experiment_revision": R.REVISION,
        "event": event,
        "request_id": request_id,
        "request_sha256": "a" * 64,
        "attempt": attempt,
    }


def test_resume_rejects_unknown_inflight_attempt(tmp_path):
    (tmp_path / "ATTEMPT_LEDGER.jsonl").write_text(
        json.dumps(_ledger_row("one", "attempt_started")) + "\n", encoding="utf-8"
    )
    with pytest.raises(SystemExit, match="unknown in-flight"):
        R.assert_resume_namespace(tmp_path)


def test_resume_rejects_finished_attempt_without_status(tmp_path):
    rows = [_ledger_row("one", "attempt_started"), _ledger_row("one", "attempt_finished")]
    (tmp_path / "ATTEMPT_LEDGER.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    with pytest.raises(SystemExit, match="without terminal status"):
        R.assert_resume_namespace(tmp_path)


def test_resume_rejects_status_without_attempt_ledger(tmp_path):
    row = {"experiment_revision": R.REVISION, "request_id": "one", "attempt_count": 1}
    (tmp_path / "REQUEST_STATUS.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="without an attempt ledger"):
        R.assert_resume_namespace(tmp_path)


def test_attempt_ledger_is_balanced_and_resume_safe(tmp_path, monkeypatch):
    response = {
        "model": R.MODEL,
        "choices": [{
            "message": {"content": '{"ranked_candidate_ids":["c1"]}', "reasoning_content": "hidden"},
            "finish_reason": "stop",
        }],
        "usage": {},
    }
    outcome = R.SSEOutcome(200, response, 1, 2, True, True, "stop", raw_sse_events=[])
    runner = R.SelectionRunner("https://example.invalid/v1", ["k0"], tmp_path, 1, {})
    monkeypatch.setattr(runner, "send_stream", lambda request_item, slot, raw_sse_path=None: outcome)
    result = runner.run_one(item(0), 0)
    R.append_jsonl(tmp_path / "REQUEST_STATUS.jsonl", result, runner.write_lock, durable=True)
    runner.close()
    ledger = R.read_jsonl(tmp_path / "ATTEMPT_LEDGER.jsonl")
    assert [row["event"] for row in ledger] == ["attempt_started", "attempt_finished"]
    assert result["attempt_count"] == 1
    assert Path(tmp_path / result["raw_sse_events_path"]).is_file()
    R.assert_resume_namespace(tmp_path)


def test_retry_is_initial_plus_network_retries_only(tmp_path, monkeypatch):
    response = {
        "model": R.MODEL,
        "choices": [{
            "message": {"content": '{"ranked_candidate_ids":["c1"]}', "reasoning_content": "hidden"},
            "finish_reason": "stop",
        }],
        "usage": {},
    }
    outcomes = iter([
        R.SSEOutcome(None, None, 0, 0, False, False, None, error_code="TRANSPORT_ERROR", retryable=True, raw_sse_events=[]),
        R.SSEOutcome(200, response, 1, 2, True, True, "stop", raw_sse_events=[]),
    ])
    runner = R.SelectionRunner("https://example.invalid/v1", ["k0"], tmp_path, 1, {})
    monkeypatch.setattr(runner, "send_stream", lambda request_item, slot, raw_sse_path=None: next(outcomes))
    monkeypatch.setattr(R.time, "sleep", lambda seconds: None)
    result = runner.run_one(item(0), 0)
    runner.close()
    assert result["status"] == "succeeded"
    assert result["attempt_count"] == 2 and result["retry_count"] == 1
    assert len(R.read_jsonl(tmp_path / "ATTEMPT_LEDGER.jsonl")) == 4


def test_nonretryable_sse_contract_error_is_not_retried(tmp_path, monkeypatch):
    outcome = R.SSEOutcome(
        200, None, 0, 0, False, False, None,
        error_code="INVALID_SSE_JSON", retryable=False, raw_sse_events=[],
    )
    runner = R.SelectionRunner("https://example.invalid/v1", ["k0"], tmp_path, 1, {})
    monkeypatch.setattr(runner, "send_stream", lambda request_item, slot, raw_sse_path=None: outcome)
    result = runner.run_one(item(0), 0)
    runner.close()
    assert result["status"] == "api_error"
    assert result["attempt_count"] == 1


def test_sse_rejects_more_than_one_choice():
    state = {"content": [], "reasoning": [], "heartbeats": 0, "events": 0, "terminal": False, "done": False, "finish_reason": None, "response_model": None, "usage": None}
    data = '{"model":"' + R.MODEL + '","choices":[{"delta":{}},{"delta":{}}]}'
    assert R._consume_frame(data, None, state) == ("error", "CHOICE_COUNT_CONTRACT_VIOLATION")


def test_send_stream_persists_each_sse_frame_incrementally(tmp_path):
    class FakeResponse:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def iter_lines(self):
            event = {
                "model": R.MODEL,
                "choices": [{
                    "delta": {
                        "content": '{"ranked_candidate_ids":["c1"]}',
                        "reasoning_content": "hidden",
                    },
                    "finish_reason": "stop",
                }],
            }
            return iter([": heartbeat", "", "data: " + json.dumps(event), "", "data: [DONE]", ""])

    class FakeClient:
        def stream(self, *args, **kwargs):
            return FakeResponse()

        def close(self):
            pass

    runner = R.SelectionRunner("https://example.invalid/v1", ["k0"], tmp_path, 1, {})
    runner.clients = [FakeClient()]
    raw = tmp_path / "raw.jsonl"
    raw.touch()
    outcome = runner.send_stream(item(0), 0, raw)
    runner.close()
    frames = R.read_jsonl(raw)
    assert outcome.error_code is None
    assert [frame["sequence"] for frame in frames] == list(range(1, len(frames) + 1))
    assert any(frame["event"] == "heartbeat" for frame in frames)
    assert any(frame["data"] == "[DONE]" for frame in frames)
