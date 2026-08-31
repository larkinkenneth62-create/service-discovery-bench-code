from __future__ import annotations

import importlib.util
import inspect
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
CODE = ROOT / "experiments" / "llm_v0_2_qwen38_native_single_api_correction_v1_10" / "code"
SCHEMAS = ROOT / "experiments" / "llm_v0_2_qwen38_native_single_api_correction_v1_10" / "schemas"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


contracts = load("sdb_contracts_v1_10_test", CODE / "output_contracts_v1_10.py")
runner = load("sdb_runner_v1_10_test", CODE / "run_qwen38_native_single_api_correction_v1_10.py")
q0 = load("sdb_q0_v1_10_test", CODE / "run_q0_single_api_correction_v1_10.py")
scorer = load("sdb_scorer_v1_10_test", ROOT / "scripts" / "evaluation" / "score_single_api_correction_v1_10.py")


def response(value: dict) -> dict:
    return {"choices": [{"message": {"content": json.dumps(value, separators=(",", ":"))}}]}


def documents(n: int = 6) -> list[dict[str, str]]:
    return [{"candidate_id": f"api-{i}", "document": f"API {i}"} for i in range(n)]


def combined_payload(n: int = 6) -> dict:
    docs = documents(n)
    return runner.build_payload(
        query="Use every necessary API.",
        task_type="single_api_recommendation",
        prediction_target="api",
        candidate_documents=docs,
        candidate_ids=[row["candidate_id"] for row in docs],
        contract=contracts.RANKING_AND_SELECTED_SET_V1_10,
        max_tokens=2048,
    )


def test_single_api_with_three_gold_stays_single_api_combined_contract():
    assert contracts.contract_for("native", "single_api_recommendation") == contracts.RANKING_AND_SELECTED_SET_V1_10


def test_combined_output_requires_top5_and_selected_set():
    parsed = contracts.parse_ranking_and_selected_set_response(
        response({"ranked_candidate_ids": [f"api-{i}" for i in range(5)], "selected_candidate_ids": ["api-0", "api-1", "api-2"]}),
        [f"api-{i}" for i in range(6)],
    )
    assert parsed.valid


def test_selected_set_is_not_hard_coded_to_one():
    schema = combined_payload()["response_format"]["json_schema"]["schema"]
    selected = schema["properties"]["selected_candidate_ids"]
    assert selected["maxItems"] == 6
    assert "minItems" not in selected


def test_test_gold_cardinality_cannot_enter_prompt_budget_or_selection():
    assert "gold" not in inspect.signature(runner.build_payload).parameters
    encoded = json.dumps(combined_payload(), sort_keys=True).lower()
    assert "gold_count" not in encoded and "test_gold" not in encoded


def test_candidate_enum_has_no_gold_marker():
    schema = combined_payload()["response_format"]["json_schema"]["schema"]
    assert schema["properties"]["ranked_candidate_ids"]["items"]["enum"] == [f"api-{i}" for i in range(6)]
    assert all("gold" not in key.lower() for key in schema)


def test_selected_set_can_be_larger_than_five():
    parsed = contracts.parse_ranking_and_selected_set_response(
        response({"ranked_candidate_ids": [f"api-{i}" for i in range(5)], "selected_candidate_ids": [f"api-{i}" for i in range(6)]}),
        [f"api-{i}" for i in range(6)],
    )
    assert parsed.valid and len(parsed.data["selected_candidate_ids"]) == 6


def test_synthetic_gold_count_six_can_select_six_apis():
    gold = [{f"api-{i}" for i in range(6)}]
    metrics = scorer.V15.score_selected_set([f"api-{i}" for i in range(6)], gold)
    assert metrics["exact_set_match"] == 1.0 and metrics["cardinality_error"] == 0.0


def test_single_service_remains_ranking_only():
    assert contracts.contract_for("native", "single_service_discovery") == contracts.TOP5_RANKING_V1


def test_v1_9_namespace_cannot_resume_as_correction(tmp_path: Path):
    old = tmp_path / "llm_v0_2_qwen38_sse_structured_selection_v1_9" / "formal"
    with pytest.raises(SystemExit):
        runner.assert_resume_namespace(old)


def test_parse_failure_scores_zero_and_has_no_format_retry():
    ranking = scorer.V15.score_ranking(None, [{"api-0"}], parse_failure=True)
    selected = scorer.V15.score_selected_set(None, [{"api-0"}], parse_failure=True)
    assert ranking["hit_at_1"] == 0.0 and selected["exact_set_match"] == 0.0
    assert ranking["parse_failure"] == 1.0 and selected["parse_failure"] == 1.0
    assert "schema_or_parser_failure" not in runner.RETRYABLE_HTTP


def test_scorer_builds_exact_task_macro_and_micro():
    corrected = []
    for index, task in enumerate(scorer.TASKS):
        corrected.append({
            "request_id": str(index), "task_type": task, "prediction_target": "api",
            "source": "test", "status": "succeeded", "parse_status": "valid",
            "exact_task_success": float(index % 2 == 0), "parse_failure": 0.0,
            "hit_at_1": 1.0, "mrr_at_5": 1.0, "recall_at_5": 1.0, "ndcg_at_5": 1.0,
            "exact_set_match": 1.0, "precision": 1.0, "recall": 1.0, "f1": 1.0,
            "completeness": 1.0, "jaccard": 1.0, "under_selection": 0.0,
            "over_selection": 0.0, "cardinality_error": 0.0,
        })
    old = [{"task_type": task, "metrics": {"task_success": 1.0, "hit_at_1": 1.0, "parse_failure": 0.0}} for task in scorer.TASKS]
    tables = scorer.aggregate(corrected, old)
    assert tables["macro_6_exact_task_success"] == 0.5
    assert tables["micro_exact_task_success"] == 0.5


def test_historical_mixed_contract_is_labeled_and_not_primary():
    corrected = []
    for index, task in enumerate(scorer.TASKS):
        corrected.append({
            "request_id": str(index), "task_type": task, "prediction_target": "api", "source": "test",
            "status": "succeeded", "parse_status": "valid", "exact_task_success": 1.0, "parse_failure": 0.0,
            "hit_at_1": 1.0, "mrr_at_5": 1.0, "recall_at_5": 1.0, "ndcg_at_5": 1.0,
            "exact_set_match": 1.0, "precision": 1.0, "recall": 1.0, "f1": 1.0,
            "completeness": 1.0, "jaccard": 1.0, "under_selection": 0.0, "over_selection": 0.0,
            "cardinality_error": 0.0,
        })
    old = [{"task_type": task, "metrics": {"task_success": 0.0, "hit_at_1": 0.0, "parse_failure": 0.0}} for task in scorer.TASKS]
    comparison = scorer.aggregate(corrected, old)["comparison"][0]
    assert comparison["old_contract"] == "V1.9_HISTORICAL_MIXED_CONTRACT_DIAGNOSTIC"
    assert comparison["new_macro_6_exact_task_success"] == 1.0


def test_r02_all_eight_q0_payloads_and_report_use_frozen_5924_budget():
    items = [
        item
        for round_index in range(1, 3)
        for slot_index in range(1, 5)
        for _, item in q0.synthetic_cases(
            round_index=round_index,
            slot_index=slot_index,
            max_tokens=5924,
        )
    ]
    assert len(items) == 8
    assert {item.payload["max_tokens"] for item in items} == {5924}
    assert q0.q0_request_contract(5924)["max_tokens"] == 5924


def test_r02_q0_changes_only_budget_not_visible_request_contract():
    _, aligned = q0.synthetic_cases(round_index=1, slot_index=1, max_tokens=5924)[0]
    old = dict(aligned.payload)
    old["max_tokens"] = 1024
    new = dict(aligned.payload)
    old_budget = old.pop("max_tokens")
    new_budget = new.pop("max_tokens")
    assert (old_budget, new_budget) == (1024, 5924)
    assert old == new


def test_r02_runtime_budget_mismatch_rejected_and_alignment_passes():
    runtime = SCHEMAS / "QWEN38_SINGLE_API_CORRECTION_RUNTIME_FREEZE_V1_10_R02_TEMPLATE.json"
    with pytest.raises(SystemExit):
        runner.load_runtime_freeze(runtime, expected_q0_max_output_tokens=1024)
    provenance = runner.load_runtime_freeze(runtime, expected_q0_max_output_tokens=5924)
    assert provenance["q0_max_output_tokens"] == 5924
    assert provenance["runtime_patch_id"] == runner.RUNTIME_PATCH_ID


def test_r02_budget_binding_preserves_formal_budget(tmp_path: Path):
    source = tmp_path / "source.jsonl"
    source.write_text("{}\n", encoding="utf-8")
    source_hash = runner.sha256_file(source)
    budget_path = tmp_path / "budget.json"
    budget_path.write_text(json.dumps({
        "status": "PASS",
        "model": runner.MODEL,
        "experiment_revision": runner.REVISION,
        "token_counter_revision": runner.TOKEN_BUDGET_COUNTER_REVISION,
        "tracks": {"native": {
            "frozen_max_tokens": 5924,
            "allowed_source_manifest_sha256": [source_hash],
        }},
    }), encoding="utf-8")
    budget = runner.load_budget(budget_path, "native", source)
    assert budget["frozen_max_tokens"] == 5924
    assert budget["source_manifest_sha256"] == source_hash


def test_r02_q0_six_of_eight_still_fails_original_gate():
    rows = []
    for round_index in range(1, 3):
        for slot_index in range(1, 5):
            succeeded = not (round_index == 2 and slot_index in {3, 4})
            rows.append({
                "round": round_index,
                "key_slot": slot_index,
                "output_contract": runner.CONTRACTS.RANKING_AND_SELECTED_SET_V1_10,
                "request_sha256": f"sha-{round_index}-{slot_index}",
                "status": "succeeded" if succeeded else "parse_failure",
                "http_status": 200,
                "terminal_event_received": True,
                "done_received": True,
                "finish_reason": "stop",
                "response_model": runner.MODEL,
                "response_format_type": "json_schema",
                "response_schema_strict": True,
            })
    result = q0.evaluate_q0_results(rows)
    assert result["status"] == "FAIL"
    assert result["hard_transport_gate_pass"] is True
    assert result["format_feasibility_gate_pass"] is False
