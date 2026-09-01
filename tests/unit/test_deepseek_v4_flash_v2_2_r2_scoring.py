from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


S = load("sdb_deepseek_r2_scorer_test", ROOT / "scripts/evaluation/score_deepseek_full_v2_2.py")
CMP = load("sdb_deepseek_r2_comparison_test", ROOT / "scripts/evaluation/build_deepseek_native_machine_comparison_v2_2.py")


def machine_scope():
    manifests = {}
    statuses = {}
    provenance = {
        "provider": S.PROVIDER, "experiment_revision": S.REVISION,
        "implementation_revision": S.IMPLEMENTATION_REVISION,
        "requested_model": "deepseek-v4-flash", "model_version_mapping": "DeepSeek-V4-Flash-0731",
        "runtime_freeze_sha256": "1" * 64, "budget_freeze_sha256": "2" * 64,
        "runner_sha256": "3" * 64, "parser_sha256": "4" * 64,
        "endpoint_sha256": "5" * 64, "git_commit_sha": "6" * 40,
    }
    for index in range(197):
        request_id = f"m-{index:03d}"
        source = {"request_id": request_id, "task_type": "single_service_discovery", "prediction_target": "service", "candidate_ids": ["a", "b"], "acceptable_gold_sets": [["a"]]}
        manifests[request_id] = source
        statuses[request_id] = {**provenance, "request_id": request_id, "track": "machine", "task_type": source["task_type"], "output_contract": "TOP5_RANKING_V1", "status": "parse_failure", "parse_status": "invalid", "candidate_count": 2, "source_row_sha256": S.sha256_text(S.stable_json(source)), "source_manifest_sha256": "7" * 64, "response_model": "deepseek-v4-flash"}
    return manifests, statuses


def test_source_row_hash_mismatch_blocks_scoring():
    manifests, statuses = machine_scope(); statuses["m-000"]["source_row_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="BLOCKED_SOURCE_ROW_HASH_MISMATCH"):
        S.validate_scope("machine", manifests, statuses, "7" * 64)


def test_manifest_file_hash_mismatch_blocks_scoring():
    manifests, statuses = machine_scope()
    with pytest.raises(ValueError, match="BLOCKED_SOURCE_MANIFEST_HASH_MISMATCH"):
        S.validate_scope("machine", manifests, statuses, "8" * 64)


def test_candidate_count_mismatch_blocks_scoring():
    manifests, statuses = machine_scope(); statuses["m-000"]["candidate_count"] = 3
    with pytest.raises(ValueError, match="BLOCKED_CANDIDATE_COUNT_MISMATCH"):
        S.validate_scope("machine", manifests, statuses, "7" * 64)


@pytest.mark.parametrize("field", ["runtime_freeze_sha256", "budget_freeze_sha256"])
def test_mixed_freeze_hash_blocks_scoring(field: str):
    manifests, statuses = machine_scope(); statuses["m-000"][field] = "9" * 64
    with pytest.raises(ValueError, match="BLOCKED_MIXED_PROVENANCE"):
        S.validate_scope("machine", manifests, statuses, "7" * 64)


def test_unknown_git_commit_blocks_scoring():
    manifests, statuses = machine_scope()
    for row in statuses.values(): row["git_commit_sha"] = "UNKNOWN"
    with pytest.raises(ValueError, match="BLOCKED_GIT_COMMIT_UNKNOWN"):
        S.validate_scope("machine", manifests, statuses, "7" * 64)


def test_response_model_mismatch_blocks_scoring():
    manifests, statuses = machine_scope(); statuses["m-000"]["response_model"] = "other"
    with pytest.raises(ValueError, match="BLOCKED_RESPONSE_MODEL_MISMATCH"):
        S.validate_scope("machine", manifests, statuses, "7" * 64)


@pytest.mark.parametrize("status", ["infra_error", "api_error", "future_status"])
def test_non_capability_status_blocks_scoring(status: str):
    manifests, statuses = machine_scope(); statuses["m-000"]["status"] = status
    with pytest.raises(ValueError, match="BLOCKED_SCORING_STATUS"):
        S.validate_scope("machine", manifests, statuses, "7" * 64)


def test_parse_failure_scores_zero_and_keeps_denominator(tmp_path: Path):
    manifest = [{"request_id": "x", "task_type": "single_service_discovery", "prediction_target": "service", "acceptable_gold_sets": [["a"]]}]
    status = [{"request_id": "x", "status": "parse_failure", "parse_status": "invalid", "output_contract": "TOP5_RANKING_V1", "candidate_count": 2}]
    row = S.score_rows(manifest, status, tmp_path, "machine", enforce_scope=False)[0]
    assert row["exact_task_success"] == 0.0 and row["ranking_metrics"]["parse_failure"] == 1.0


def test_single_api_uses_set_esm_not_hit_at_1(tmp_path: Path):
    prediction = tmp_path / "pred.json"
    prediction.write_text(json.dumps({"ranked_candidate_ids": ["a", "b"], "selected_candidate_ids": ["a"]}), encoding="utf-8")
    manifest = [{"request_id": "x", "task_type": "single_api_recommendation", "prediction_target": "api", "acceptable_gold_sets": [["a", "b"]]}]
    status = [{"request_id": "x", "status": "succeeded", "parse_status": "valid", "output_contract": S.COMBINED, "candidate_count": 2, "parsed_prediction_path": "pred.json"}]
    row = S.score_rows(manifest, status, tmp_path, "native", enforce_scope=False)[0]
    assert row["ranking_metrics"]["hit_at_1"] == 1.0
    assert row["set_metrics"]["exact_set_match"] == 0.0
    assert row["exact_task_success"] == 0.0


def synthetic_scored():
    rows = []
    for task_index, task in enumerate(S.TASKS):
        for row_index in range(task_index + 1):
            is_set = task != "single_service_discovery"
            rows.append({"task_type": task, "task_family": task.split("_", 1)[0], "prediction_target": "api" if "api" in task else "service", "candidate_count": 5, "gold_count": 2, "parse_status": "valid", "exact_task_success": float(task_index % 2), "parse_failure": 0.0, "ranking_metrics": {name: 1.0 for name in S.RANKING_FIELDS} if task in {"single_service_discovery", "single_api_recommendation"} else None, "set_metrics": {name: float(task_index % 2) for name in S.SET_FIELDS} if is_set else None, "source_dataset": None, "core_expansion": None, "evidence_tier": None})
    return rows


def test_macro_6_is_six_task_equal_not_row_weighted():
    result = S.aggregate(synthetic_scored(), "native")
    assert result["macro_6_exact_task_success"]["task_count"] == 6
    assert result["macro_6_exact_task_success"]["exact_task_success"] != result["micro_exact_task_success"]["exact_task_success"]


def test_set_selection_macro_is_five_task_equal():
    result = S.aggregate(synthetic_scored(), "native")
    assert result["set_selection_macro_task_equal"]["task_count"] == 5
    assert result["set_selection_macro_task_equal"]["aggregation"] == "TASK_EQUAL"


def test_set_macro_and_micro_have_distinct_names_and_weights():
    result = S.aggregate(synthetic_scored(), "native")
    assert result["set_selection_micro_row_weighted"]["aggregation"] == "ROW_WEIGHTED"
    assert result["set_selection_macro_task_equal"] != result["set_selection_micro_row_weighted"]


def test_not_applicable_set_metrics_are_not_zeroed():
    only_ranking = [row for row in synthetic_scored() if row["task_type"] == "single_service_discovery"]
    result = S.aggregate(only_ranking, "machine")
    assert result["set_selection_macro_task_equal"]["finding"] == "NOT_AVAILABLE"
    assert result["set_selection_micro_row_weighted"]["finding"] == "NOT_AVAILABLE"


def test_missing_metadata_dimensions_emit_not_available():
    result = S.aggregate(synthetic_scored(), "native")
    assert result["tables"]["BY_SOURCE_DATASET.csv"][0]["finding"] == "NOT_AVAILABLE"
    assert result["tables"]["BY_EVIDENCE_TIER.csv"][0]["finding"] == "NOT_AVAILABLE"


def ranking_row(request_id: str, value: float):
    return {"request_id": request_id, "task_type": "single_service_discovery", "prediction_target": "service", "parse_failure": 0.0, "ranking_metrics": {"hit_at_1": value, "mrr_at_5": value, "recall_at_5": value, "ndcg_at_5": value, "parse_failure": 0.0}}


def test_pairing_not_available_does_not_fabricate_delta():
    rows, validation = CMP.build_comparison([ranking_row("n", 1)], [ranking_row("m", 0)], None)
    assert rows == [] and validation["status"] == "PAIRING_NOT_AVAILABLE"


def test_explicit_pairing_builds_ranking_deltas():
    rows, validation = CMP.build_comparison([ranking_row("n", 1)], [ranking_row("m", 0)], [{"pairing_id": "p", "native_request_id": "n", "machine_request_id": "m"}])
    assert validation["status"] == "PASS" and rows[0]["delta_hit_at_1"] == 1.0


def test_pairing_semantic_mismatch_is_blocked():
    machine = ranking_row("m", 0); machine["prediction_target"] = "api"
    with pytest.raises(ValueError, match="semantics differ"):
        CMP.build_comparison([ranking_row("n", 1)], [machine], [{"pairing_id": "p", "native_request_id": "n", "machine_request_id": "m"}])


def test_pairing_requires_explicit_known_ids():
    with pytest.raises(ValueError, match="unknown request"):
        CMP.build_comparison([ranking_row("n", 1)], [ranking_row("m", 0)], [{"pairing_id": "p", "native_request_id": "missing", "machine_request_id": "m"}])
