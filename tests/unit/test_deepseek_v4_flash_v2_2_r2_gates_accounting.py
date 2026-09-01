from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
CODE = ROOT / "experiments" / "llm_v0_2_deepseek_v4_flash_structured_selection_v2_2" / "code"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


R = load("sdb_deepseek_r2_runner_test", CODE / "run_deepseek_v4_flash_v2_2.py")
B = load("sdb_deepseek_r2_budget_test", CODE / "freeze_output_budgets_v2_2.py")
U = load("sdb_deepseek_r2_size_test", CODE / "contract_size_utils_v2_2.py")


def write_json(path: Path, value: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    return path


def prerequisite_files(tmp_path: Path):
    runtime = write_json(tmp_path / "runtime.json", {"x": 1})
    budget = write_json(tmp_path / "budget.json", {"x": 2})
    q0_dir = tmp_path / "q0"
    diagnostic = write_json(q0_dir / "RUN_SUMMARY.json", {"status": "DIAGNOSTIC_COMPLETE"})
    q0 = write_json(q0_dir / "Q0_REPORT.json", {
        "status": "PASS", "provider": "deepseek", "experiment_revision": R.REVISION,
        "implementation_revision": R.IMPLEMENTATION_REVISION, "terminal_rows": 6,
        "status_counts": {"succeeded": 6, "parse_failure": 0, "infra_error": 0, "api_error": 0},
        "per_contract_strict_parse": {R.CONTRACTS.TOP5_RANKING_V1: 2, R.CONTRACTS.SELECTED_SET_V1: 2, R.CONTRACTS.RANKING_AND_SELECTED_SET_V1_10: 2},
        "runtime_freeze_sha256": R.sha256_file(runtime), "budget_freeze_sha256": R.sha256_file(budget),
        "native_source_manifest_sha256": "a" * 64, "diagnostic_run_summary_sha256": R.sha256_file(diagnostic),
    })
    smoke = write_json(tmp_path / "smoke" / "RUN_SUMMARY.json", {
        "status": "COMPLETE_ALL_PARSED", "provider": "deepseek", "experiment_revision": R.REVISION,
        "implementation_revision": R.IMPLEMENTATION_REVISION, "mode": "smoke", "track": "smoke",
        "requested_rows": 60, "terminal_rows": 60, "gate_passed": True,
        "status_counts": {"succeeded": 60, "parse_failure": 0, "infra_error": 0, "api_error": 0},
        "prerequisite_q0_report_sha256": R.sha256_file(q0),
        "runtime_freeze_sha256": R.sha256_file(runtime), "budget_freeze_sha256": R.sha256_file(budget),
    })
    machine = write_json(tmp_path / "machine" / "RUN_SUMMARY.json", {
        "status": "COMPLETE_ALL_PARSED", "provider": "deepseek", "experiment_revision": R.REVISION,
        "implementation_revision": R.IMPLEMENTATION_REVISION, "mode": "formal", "track": "machine",
        "requested_rows": 197, "terminal_rows": 197,
        "status_counts": {"succeeded": 197, "parse_failure": 0, "infra_error": 0, "api_error": 0},
        "prerequisite_q0_report_sha256": R.sha256_file(q0),
        "prerequisite_smoke_summary_sha256": R.sha256_file(smoke),
        "runtime_freeze_sha256": R.sha256_file(runtime), "budget_freeze_sha256": R.sha256_file(budget),
    })
    return runtime, budget, q0, smoke, machine


def validate(tmp_path: Path, mode: str, track: str, q0=True, smoke=True, machine=True):
    runtime, budget, q0_path, smoke_path, machine_path = prerequisite_files(tmp_path)
    return R.validate_stage_prerequisites(
        mode=mode, track=track, q0_report=q0_path if q0 else None,
        smoke_summary=smoke_path if smoke else None, machine_summary=machine_path if machine else None,
        runtime_freeze=runtime, budget_freeze=budget,
    )


def test_smoke_without_q0_is_blocked(tmp_path: Path):
    with pytest.raises(SystemExit, match="BLOCKED_STAGE_PREREQUISITE"):
        validate(tmp_path, "smoke", "smoke", q0=False, smoke=False, machine=False)


def test_q0_non_pass_is_blocked(tmp_path: Path):
    runtime, budget, q0, _, _ = prerequisite_files(tmp_path)
    value = json.loads(q0.read_text(encoding="utf-8")); value["status"] = "FAIL"; write_json(q0, value)
    with pytest.raises(SystemExit, match="Q0"):
        R.validate_q0_report(q0, runtime, budget)


@pytest.mark.parametrize("field,value", [("provider", "qwen"), ("experiment_revision", "old"), ("implementation_revision", "old")])
def test_q0_identity_mismatch_is_blocked(tmp_path: Path, field: str, value: str):
    runtime, budget, q0, _, _ = prerequisite_files(tmp_path)
    report = json.loads(q0.read_text(encoding="utf-8")); report[field] = value; write_json(q0, report)
    with pytest.raises(SystemExit, match="Q0"):
        R.validate_q0_report(q0, runtime, budget)


def test_q0_runtime_hash_mismatch_is_blocked(tmp_path: Path):
    runtime, budget, q0, _, _ = prerequisite_files(tmp_path)
    write_json(runtime, {"changed": True})
    with pytest.raises(SystemExit, match="Q0"):
        R.validate_q0_report(q0, runtime, budget)


def test_q0_budget_hash_mismatch_is_blocked(tmp_path: Path):
    runtime, budget, q0, _, _ = prerequisite_files(tmp_path)
    write_json(budget, {"changed": True})
    with pytest.raises(SystemExit, match="Q0"):
        R.validate_q0_report(q0, runtime, budget)


def test_machine_without_smoke_is_blocked(tmp_path: Path):
    with pytest.raises(SystemExit, match="BLOCKED_STAGE_PREREQUISITE"):
        validate(tmp_path, "formal", "machine", smoke=False, machine=False)


def test_smoke_gate_false_is_blocked_for_machine(tmp_path: Path):
    runtime, budget, q0, smoke, _ = prerequisite_files(tmp_path)
    value = json.loads(smoke.read_text(encoding="utf-8")); value["gate_passed"] = False; write_json(smoke, value)
    with pytest.raises(SystemExit, match="Smoke"):
        R.validate_smoke_summary(smoke, q0, runtime, budget)


def test_native_without_machine_is_blocked(tmp_path: Path):
    with pytest.raises(SystemExit, match="BLOCKED_STAGE_PREREQUISITE"):
        validate(tmp_path, "formal", "native", machine=False)


def test_machine_row_count_mismatch_is_blocked(tmp_path: Path):
    runtime, budget, q0, smoke, machine = prerequisite_files(tmp_path)
    value = json.loads(machine.read_text(encoding="utf-8")); value["terminal_rows"] = 196; write_json(machine, value)
    with pytest.raises(SystemExit, match="Machine"):
        R.validate_machine_summary(machine, q0, smoke, runtime, budget)


def test_prerequisite_hash_chain_mismatch_is_blocked(tmp_path: Path):
    runtime, budget, q0, smoke, machine = prerequisite_files(tmp_path)
    value = json.loads(machine.read_text(encoding="utf-8")); value["prerequisite_smoke_summary_sha256"] = "0" * 64; write_json(machine, value)
    with pytest.raises(SystemExit, match="Machine"):
        R.validate_machine_summary(machine, q0, smoke, runtime, budget)


def test_valid_native_prerequisites_return_all_hashes(tmp_path: Path):
    result = validate(tmp_path, "formal", "native")
    assert set(result) == {"prerequisite_q0_report_sha256", "prerequisite_smoke_summary_sha256", "prerequisite_machine_summary_sha256"}


@pytest.mark.parametrize("reason,expected", [
    ("stop", (None, None, False)),
    ("insufficient_system_resource", ("infra_error", "INSUFFICIENT_SYSTEM_RESOURCE", True)),
    ("length", ("api_error", "OUTPUT_BUDGET_EXHAUSTED", False)),
    ("content_filter", ("api_error", "CONTENT_FILTERED", False)),
    ("tool_calls", ("api_error", "UNEXPECTED_TOOL_CALL_FINISH", False)),
    ("future_reason", ("api_error", "UNSUPPORTED_FINISH_REASON", False)),
    (None, ("api_error", "UNSUPPORTED_FINISH_REASON", False)),
])
def test_finish_reason_accounting(reason, expected):
    assert R.classify_finish_reason(reason) == expected


def item() -> R.RequestItem:
    ids = [f"id-{index}" for index in range(5)]
    payload = {"max_tokens": 100, "messages": [{"content": "system"}, {"content": "user"}]}
    return R.RequestItem("request-1", "machine", "single_api_recommendation", "api", ids, R.CONTRACTS.TOP5_RANKING_V1, payload, "a" * 64, 10, 4, 5, 20)


def complete_response(reason: str = "stop") -> dict:
    content = json.dumps({"ranked_candidate_ids": [f"id-{index}" for index in range(5)]}, separators=(",", ":"))
    return {"id": "resp-1", "created": 7, "model": R.MODEL, "system_fingerprint": "fp-1", "choices": [{"message": {"content": content}, "finish_reason": reason}], "usage": {"completion_tokens": 5}}


def test_insufficient_resource_then_success_uses_two_attempts(tmp_path: Path, monkeypatch):
    runner = R.DeepSeekRunner(base_url="https://invalid.example", key="unused", output_dir=tmp_path / "deepseek-retry", concurrency=1, provenance={})
    outcomes = [R.StreamOutcome(200, complete_response("insufficient_system_resource"), "insufficient_system_resource", True, True, 2, "INSUFFICIENT_SYSTEM_RESOURCE", "resource", True), R.StreamOutcome(200, complete_response(), "stop", True, True, 2)]
    runner.send_stream = lambda *_: outcomes.pop(0)
    monkeypatch.setattr(R.time, "sleep", lambda _: None)
    row = runner.run_one(item(), 0)
    assert row["status"] == "succeeded" and row["attempt_count"] == 2 and row["retry_count"] == 1


@pytest.mark.parametrize("reason,error", [("length", "OUTPUT_BUDGET_EXHAUSTED"), ("content_filter", "CONTENT_FILTERED"), ("tool_calls", "UNEXPECTED_TOOL_CALL_FINISH"), ("other", "UNSUPPORTED_FINISH_REASON")])
def test_non_stop_complete_response_is_api_error_and_saved(tmp_path: Path, reason: str, error: str):
    runner = R.DeepSeekRunner(base_url="https://invalid.example", key="unused", output_dir=tmp_path / f"deepseek-{reason}", concurrency=1, provenance={})
    runner.send_stream = lambda *_: R.StreamOutcome(200, complete_response(reason), reason, True, True, 2, error, "blocked", False)
    row = runner.run_one(item(), 0)
    assert row["status"] == "api_error" and row["error_code"] == error and row["attempt_count"] == 1
    assert (runner.output_dir / row["response_path"]).is_file()
    assert (runner.output_dir / row["attempts"][0]["response_attempt_path"]).is_file()


def sse_state() -> dict:
    return {"response_id": None, "response_created": None, "system_fingerprint": None, "model": None, "content": [], "reasoning": [], "usage": None, "finish_reason": None, "terminal": False, "done": False, "events": 0}


def test_sse_captures_response_metadata():
    state = sse_state()
    chunk = {"id": "r", "created": 5, "model": R.MODEL, "system_fingerprint": "fp", "choices": []}
    assert R._consume_sse(json.dumps(chunk), state) is None
    assert (state["response_id"], state["response_created"], state["system_fingerprint"], state["model"]) == ("r", 5, "fp", R.MODEL)


def test_sse_fingerprint_change_is_rejected():
    state = sse_state()
    assert R._consume_sse(json.dumps({"system_fingerprint": "a", "choices": []}), state) is None
    assert R._consume_sse(json.dumps({"system_fingerprint": "b", "choices": []}), state) == "INCONSISTENT_RESPONSE_METADATA"


def test_missing_fingerprint_is_not_fabricated():
    state = sse_state()
    assert R._consume_sse(json.dumps({"id": "r", "choices": []}), state) is None
    assert state["system_fingerprint"] is None


def test_endpoint_is_only_recorded_as_hash(tmp_path: Path):
    runner = R.DeepSeekRunner(base_url="https://secret.invalid/v1", key="unused", output_dir=tmp_path / "deepseek-endpoint", concurrency=1, provenance={})
    runner.send_stream = lambda *_: R.StreamOutcome(200, complete_response(), "stop", True, True, 2)
    row = runner.run_one(item(), 0)
    assert row["endpoint_sha256"] == R.sha256_text("https://secret.invalid/v1")
    assert "secret.invalid" not in json.dumps(row)


def test_runner_and_budget_use_identical_legal_answer_bound():
    row = {"task_type": "single_api_recommendation", "candidate_ids": ["a", "bbbb", "中"]}
    assert B.answer_bound(row, "native") == U.legal_answer_bound_bytes(U.RANKING_AND_SELECTED_SET_V1_10, row["candidate_ids"])


def test_legal_bound_uses_longest_five_ids():
    ids = ["a", "bb", "ccc", "dddd", "eeeee", "ffffff"]
    assert U.legal_answer_bound_bytes(U.TOP5_RANKING_V1, ids) == U.array_bound_bytes("ranked_candidate_ids", list(reversed(ids[1:])))
