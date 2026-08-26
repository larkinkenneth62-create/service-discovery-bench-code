from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.evaluation.score_native_machine_selection_v1_5 import (
    build_tables,
    score_ranking,
    score_rows,
    score_selected_set,
)


def test_ranking_metrics_and_parse_failure_denominator():
    metrics = score_ranking(["a", "x", "b"], [{"a", "b"}])
    assert metrics["task_success"] == metrics["hit_at_1"] == 1
    failed = score_ranking(None, [{"a"}], parse_failure=True)
    assert failed["task_success"] == 0 and failed["parse_failure"] == 1 and failed["ndcg_at_5"] == 0


def test_selected_set_uses_best_acceptable_gold_without_union():
    metrics = score_selected_set(["a", "b"], [{"a", "b"}, {"c"}])
    assert metrics["task_success"] == metrics["exact_set_match"] == 1 and metrics["f1"] == 1


def test_empty_selected_set_is_model_answer_not_parse_failure():
    metrics = score_selected_set([], [{"a"}])
    assert metrics["parse_failure"] == 0 and metrics["under_selection"] == 1


def test_infrastructure_and_api_errors_block_scoring(tmp_path):
    manifest = [{"benchmark_task_id": "a", "task_type": "single_service_discovery", "prediction_target": "service", "gold": ["x"]}]
    for status in ("infra_error", "api_error"):
        statuses = [{"request_id": "a", "status": status, "candidate_count": 1, "output_contract": "TOP5_RANKING_V1"}]
        with pytest.raises(ValueError, match="blocking"):
            score_rows(manifest, statuses, tmp_path)


def test_duplicate_or_nonmatching_status_ids_are_rejected(tmp_path):
    manifest = [{"benchmark_task_id": "a", "task_type": "single_service_discovery", "prediction_target": "service", "gold": ["x"]}]
    duplicate = [
        {"request_id": "a", "status": "parse_failure", "candidate_count": 1, "output_contract": "TOP5_RANKING_V1"},
        {"request_id": "a", "status": "parse_failure", "candidate_count": 1, "output_contract": "TOP5_RANKING_V1"},
    ]
    with pytest.raises(ValueError, match="duplicate"):
        score_rows(manifest, duplicate, tmp_path)
    extra = [{"request_id": "b", "status": "parse_failure", "candidate_count": 1, "output_contract": "TOP5_RANKING_V1"}]
    with pytest.raises(ValueError, match="task sets differ"):
        score_rows(manifest, extra, tmp_path)


def synthetic_scored_rows():
    rows = []
    for task in ("single_service_discovery", "single_api_recommendation"):
        rows.append({
            "task_type": task, "prediction_target": "service" if "service" in task else "api",
            "task_family": "single", "contract": "TOP5_RANKING_V1", "status": "succeeded",
            "candidate_count_bucket": "0-5", "gold_count_bucket": "0-1", "parse_status": "valid",
            "metrics": score_ranking(["a"], [{"a"}]),
        })
    for task in ("multi_service_discovery", "multi_api_recommendation", "composable_service_discovery", "composable_api_recommendation"):
        rows.append({
            "task_type": task, "prediction_target": "service" if "service" in task else "api",
            "task_family": task.split("_", 1)[0], "contract": "SELECTED_SET_V1", "status": "succeeded",
            "candidate_count_bucket": "0-5", "gold_count_bucket": "0-1", "parse_status": "valid",
            "metrics": score_selected_set(["a"], [{"a"}]),
        })
    return rows


def test_macro_6_uses_only_common_task_success_and_parse_failure():
    tables = build_tables(synthetic_scored_rows())
    macro = tables["macro_6"][0]
    assert set(macro) == {"aggregation", "task_count", "n", "task_success", "parse_failure"}
    assert macro["task_count"] == 6 and macro["task_success"] == 1
    assert len(tables["single_macro"]) == 1 and len(tables["set_selection_macro"]) == 1
