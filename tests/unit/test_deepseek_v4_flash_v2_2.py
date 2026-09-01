from __future__ import annotations

import importlib.util
import inspect
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


C = load("sdb_deepseek_contracts_test", CODE / "output_contracts_v2_2.py")
R = load("sdb_deepseek_runner_test", CODE / "run_deepseek_v4_flash_v2_2.py")
Q0 = load("sdb_deepseek_q0_test", CODE / "run_q0_v2_2.py")
B = load("sdb_deepseek_budget_test", CODE / "freeze_output_budgets_v2_2.py")
S = load("sdb_deepseek_scorer_test", ROOT / "scripts" / "evaluation" / "score_deepseek_full_v2_2.py")


def documents(n: int = 6) -> list[dict[str, str]]:
    return [{"candidate_id": f"api-{index}", "document": f"API {index}"} for index in range(n)]


def payload(contract: str = C.RANKING_AND_SELECTED_SET_V1_10, n: int = 6) -> dict:
    docs = documents(n)
    return R.build_payload(
        query="Use every necessary API.",
        task_type="single_api_recommendation",
        prediction_target="api",
        candidate_documents=docs,
        candidate_ids=[row["candidate_id"] for row in docs],
        contract=contract,
        max_tokens=5924,
    )


def response(value: dict, reasoning: str = "audit reasoning") -> dict:
    return {"choices": [{"message": {"reasoning_content": reasoning, "content": json.dumps(value, separators=(",", ":"))}}]}


def test_deepseek_payload_uses_official_chat_fields_without_qwen_or_sampling_parameters():
    request = payload()
    assert request["model"] == "deepseek-v4-flash"
    assert request["thinking"] == {"type": "enabled"}
    assert request["reasoning_effort"] == "high"
    assert request["response_format"] == {"type": "json_object"}
    for forbidden in ("temperature", "top_p", "seed", "chat_template_kwargs", "json_schema"):
        assert forbidden not in request


def test_combined_contract_supports_six_selected_apis_and_reasoning_is_not_answer():
    value = {"ranked_candidate_ids": [f"api-{index}" for index in range(5)], "selected_candidate_ids": [f"api-{index}" for index in range(6)]}
    parsed = C.parse_ranking_and_selected_set_response(response(value), [f"api-{index}" for index in range(6)])
    assert parsed.valid and parsed.reasoning_present
    assert len(parsed.data["selected_candidate_ids"]) == 6
    assert "reasoning_content" not in parsed.data


def test_contract_map_covers_full_native_and_machine_without_qwen_special_case():
    assert C.contract_for("native", "single_service_discovery") == C.TOP5_RANKING_V1
    assert C.contract_for("native", "single_api_recommendation") == C.RANKING_AND_SELECTED_SET_V1_10
    assert C.contract_for("native", "multi_service_discovery") == C.SELECTED_SET_V1
    assert C.contract_for("native", "composable_api_recommendation") == C.SELECTED_SET_V1
    assert C.contract_for("machine", "single_api_recommendation") == C.TOP5_RANKING_V1


def test_gold_cannot_enter_request_builder_or_budget_logic():
    assert "gold" not in inspect.signature(R.build_payload).parameters
    assert "gold" not in inspect.signature(B.answer_bound).parameters
    encoded = json.dumps(payload(), sort_keys=True).lower()
    assert "gold_count" not in encoded and "acceptable_gold_sets" not in encoded


def test_q0_has_exactly_two_cases_per_contract_and_gate_requires_one_parse_each():
    items = Q0.synthetic_cases(5924)
    assert len(items) == 6
    assert {contract: sum(item.contract == contract for item in items) for contract in Q0.CONTRACTS} == {contract: 2 for contract in Q0.CONTRACTS}
    rows = [{"status": "succeeded", "output_contract": item.contract, "terminal_event_received": True, "done_received": True} for item in items]
    assert Q0.evaluate(rows)["status"] == "PASS"
    rows[0]["status"] = "parse_failure"
    rows[3]["status"] = "parse_failure"
    assert Q0.evaluate(rows)["status"] == "FAIL"


def test_deepseek_namespace_rejects_qwen_and_foreign_resume(tmp_path: Path):
    with pytest.raises(SystemExit):
        R.assert_independent_namespace(tmp_path / "qwen" / "deepseek")
    target = tmp_path / "deepseek_v2_2"
    target.mkdir()
    (target / "REQUEST_STATUS.jsonl").write_text(json.dumps({"request_id": "x", "provider": "qwen", "experiment_revision": "old"}) + "\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        R.assert_independent_namespace(target)


def test_dev_gate_is_sixty_rows_ten_per_task_with_all_three_contracts(tmp_path: Path):
    task_target = {
        "single_service_discovery": "service",
        "single_api_recommendation": "api",
        "multi_service_discovery": "service",
        "multi_api_recommendation": "api",
        "composable_service_discovery": "service",
        "composable_api_recommendation": "api",
    }
    items = []
    for task, target in task_target.items():
        contract = C.contract_for("native", task)
        for index in range(10):
            items.append(R.RequestItem(f"{task}-{index}", "smoke", task, target, ["a", "b", "c", "d", "e", "f"], contract, {"max_tokens": 10}, f"source-{task}-{index}"))
    output = tmp_path / "deepseek_dev"
    runner = R.DeepSeekRunner(base_url="https://invalid.example", key="not-used", output_dir=output, concurrency=3, provenance={})

    def fake_run_one(item, worker_index):
        return {"provider": "deepseek", "experiment_revision": R.REVISION, "implementation_revision": R.IMPLEMENTATION_REVISION, "request_id": item.request_id, "task_type": item.task_type, "output_contract": item.contract, "candidate_count": len(item.candidate_ids), "worker_index": worker_index, "status": "succeeded", "parse_status": "valid", "finish_reason": "stop"}

    runner.run_one = fake_run_one
    summary = runner.run(items, "smoke")
    assert summary["status"] == "COMPLETE_ALL_PARSED"
    assert summary["terminal_rows"] == 60


def test_six_api_request_parser_scorer_linkage_uses_exact_set_not_top1(tmp_path: Path):
    request = payload(C.RANKING_AND_SELECTED_SET_V1_10, 6)
    assert request["response_format"] == {"type": "json_object"}
    provider_value = {"ranked_candidate_ids": [f"api-{index}" for index in range(5)], "selected_candidate_ids": [f"api-{index}" for index in range(6)]}
    parsed = C.parse_ranking_and_selected_set_response(response(provider_value), [f"api-{index}" for index in range(6)])
    assert parsed.valid
    artifact_root = tmp_path / "deepseek_score"
    prediction_path = artifact_root / "artifacts" / "case" / "parsed_prediction.json"
    prediction_path.parent.mkdir(parents=True)
    prediction_path.write_text(json.dumps(parsed.data), encoding="utf-8")
    manifest = [{"benchmark_task_id": "six-api", "task_type": "single_api_recommendation", "prediction_target": "api", "acceptable_gold_sets": [[f"api-{index}" for index in range(6)]]}]
    status = [{"request_id": "six-api", "provider": "deepseek", "experiment_revision": R.REVISION, "status": "succeeded", "parse_status": "valid", "output_contract": C.RANKING_AND_SELECTED_SET_V1_10, "candidate_count": 6, "parsed_prediction_path": "artifacts/case/parsed_prediction.json"}]
    scored = S.score_rows(manifest, status, artifact_root, "native", enforce_scope=False)
    assert scored[0]["ranking_metrics"]["hit_at_1"] == 1.0
    assert scored[0]["set_metrics"]["exact_set_match"] == 1.0
    assert scored[0]["exact_task_success"] == scored[0]["set_metrics"]["exact_set_match"]


def test_full_scorer_rejects_qwen_status_even_when_ids_match():
    manifests = {str(index): {"task_type": "single_service_discovery"} for index in range(197)}
    statuses = {key: {"provider": "qwen", "experiment_revision": R.REVISION, "status": "parse_failure"} for key in manifests}
    with pytest.raises(ValueError, match="independent DeepSeek"):
        S.validate_scope("machine", manifests, statuses)
