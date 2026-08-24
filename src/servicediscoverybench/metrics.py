"""Evaluation metrics with correct handling of alternative acceptable Gold sets."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values))


def precision_recall_f1(predicted: Iterable[str], gold: Iterable[str]) -> tuple[float, float, float]:
    predicted_set, gold_set = set(predicted), set(gold)
    if not gold_set:
        raise ValueError("gold set must be non-empty")
    overlap = len(predicted_set & gold_set)
    precision = overlap / len(predicted_set) if predicted_set else 0.0
    recall = overlap / len(gold_set)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


def reciprocal_rank(ranking: Sequence[str], gold: Iterable[str]) -> float:
    gold_set = set(gold)
    if not gold_set:
        raise ValueError("gold set must be non-empty")
    for index, item in enumerate(_unique(ranking), start=1):
        if item in gold_set:
            return 1.0 / index
    return 0.0


def ndcg_at_k(ranking: Sequence[str], gold: Iterable[str], k: int) -> float:
    if k <= 0:
        raise ValueError("k must be positive")
    gold_set = set(gold)
    if not gold_set:
        raise ValueError("gold set must be non-empty")
    ranked = _unique(ranking)[:k]
    dcg = sum((1.0 if item in gold_set else 0.0) / math.log2(index + 2) for index, item in enumerate(ranked))
    ideal_hits = min(k, len(gold_set))
    idcg = sum(1.0 / math.log2(index + 2) for index in range(ideal_hits))
    return dcg / idcg if idcg else 0.0


def evaluate_against_gold(
    ranking: Sequence[str],
    gold: Iterable[str],
    *,
    ks: Sequence[int] = (1, 3, 5),
    predicted_set: Iterable[str] | None = None,
) -> dict[str, float]:
    gold_set = set(gold)
    if not gold_set:
        raise ValueError("gold set must be non-empty")
    ranking = _unique(ranking)
    chosen = set(predicted_set) if predicted_set is not None else set(ranking[: len(gold_set)])
    set_precision, set_recall, set_f1 = precision_recall_f1(chosen, gold_set)
    result = {
        "mrr": reciprocal_rank(ranking, gold_set),
        "exact_set_match": float(chosen == gold_set),
        "multi_label_precision": set_precision,
        "multi_label_recall": set_recall,
        "multi_label_f1": set_f1,
    }
    for k in ks:
        top = ranking[:k]
        precision, recall, f1 = precision_recall_f1(top, gold_set)
        result[f"precision@{k}"] = precision
        result[f"recall@{k}"] = recall
        result[f"f1@{k}"] = f1
        result[f"hit@{k}"] = float(bool(set(top) & gold_set))
        result[f"ndcg@{k}"] = ndcg_at_k(ranking, gold_set, k)
    return result


def evaluate_acceptable_gold_sets(
    ranking: Sequence[str],
    acceptable_gold_sets: Iterable[Iterable[str]],
    *,
    ks: Sequence[int] = (1, 3, 5),
    predicted_set: Iterable[str] | None = None,
) -> dict[str, float]:
    """Evaluate each mutually alternative Gold set and take the best per metric.

    This intentionally does not union incompatible alternatives.
    """

    alternatives = [set(values) for values in acceptable_gold_sets]
    alternatives = [values for values in alternatives if values]
    if not alternatives:
        raise ValueError("at least one non-empty acceptable Gold set is required")
    scores = [evaluate_against_gold(ranking, gold, ks=ks, predicted_set=predicted_set) for gold in alternatives]
    return {key: max(score[key] for score in scores) for key in scores[0]}


def mean_metrics(rows: Iterable[dict[str, float]]) -> dict[str, float]:
    values = list(rows)
    if not values:
        return {}
    keys = set(values[0])
    if any(set(row) != keys for row in values):
        raise ValueError("metric rows have inconsistent keys")
    return {key: sum(row[key] for row in values) / len(values) for key in sorted(keys)}
