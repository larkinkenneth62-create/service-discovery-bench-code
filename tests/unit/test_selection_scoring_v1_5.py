from __future__ import annotations

from scripts.evaluation.score_native_machine_selection_v1_5 import score_ranking, score_selected_set


def test_ranking_metrics_and_parse_failure_denominator():
    metrics = score_ranking(["a", "x", "b"], [{"a", "b"}])
    assert metrics["hit_at_1"] == 1 and metrics["mrr_at_5"] == 1
    failed = score_ranking(None, [{"a"}], parse_failure=True)
    assert failed["parse_failure"] == 1 and failed["ndcg_at_5"] == 0


def test_selected_set_uses_best_acceptable_gold_without_union():
    metrics = score_selected_set(["a", "b"], [{"a", "b"}, {"c"}])
    assert metrics["exact_set_match"] == 1 and metrics["f1"] == 1


def test_empty_selected_set_is_model_answer_not_parse_failure():
    metrics = score_selected_set([], [{"a"}])
    assert metrics["parse_failure"] == 0 and metrics["under_selection"] == 1
