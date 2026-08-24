"""V9 metric ingestion, preserving the accepted v0.1.1 OR-aware semantics."""
from __future__ import annotations

import math
from typing import Sequence


def relevant_union(alternatives: Sequence[Sequence[str]]) -> set[str]:
    return {str(item) for solution in alternatives for item in solution}


def reciprocal_rank(ranking: Sequence[str], alternatives: Sequence[Sequence[str]]) -> float:
    relevant = relevant_union(alternatives)
    for rank, candidate in enumerate(ranking, 1):
        if candidate in relevant:
            return 1.0 / rank
    return 0.0


def recall_at_k(ranking: Sequence[str], alternatives: Sequence[Sequence[str]], k: int) -> float:
    relevant = relevant_union(alternatives)
    return len(set(ranking[:k]) & relevant) / len(relevant) if relevant else 0.0


def completeness_at_k(ranking: Sequence[str], alternatives: Sequence[Sequence[str]], k: int) -> float:
    top = set(ranking[:k])
    return float(bool(alternatives) and any(set(solution).issubset(top) for solution in alternatives))


def ndcg_at_k(ranking: Sequence[str], alternatives: Sequence[Sequence[str]], k: int) -> float:
    relevant = relevant_union(alternatives)
    if not relevant:
        return 0.0
    dcg = sum(1.0 / math.log2(i + 1) for i, c in enumerate(ranking[:k], 1) if c in relevant)
    ideal = sum(1.0 / math.log2(i + 1) for i in range(1, min(k, len(relevant)) + 1))
    return dcg / ideal if ideal else 0.0


def best_set_metrics(predicted: Sequence[str], alternatives: Sequence[Sequence[str]]) -> dict[str, float]:
    pred = set(predicted)
    best = {"precision": 0.0, "recall": 0.0, "f1": 0.0, "jaccard": 0.0,
            "exact_set_match": 0.0, "completeness": 0.0,
            "over_selection": float(len(pred)), "under_selection": 0.0}
    for gold_values in alternatives:
        gold = set(gold_values)
        if not gold:
            continue
        overlap = len(pred & gold)
        precision = overlap / len(pred) if pred else 0.0
        recall = overlap / len(gold)
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        union = len(pred | gold)
        candidate = {"precision": precision, "recall": recall, "f1": f1,
                     "jaccard": overlap / union if union else 0.0,
                     "exact_set_match": float(pred == gold), "completeness": float(gold.issubset(pred)),
                     "over_selection": float(len(pred - gold)), "under_selection": float(len(gold - pred))}
        if (candidate["exact_set_match"], candidate["f1"], candidate["jaccard"]) > (
                best["exact_set_match"], best["f1"], best["jaccard"]):
            best = candidate
    return best


def evaluate(ranking: Sequence[str], alternatives: Sequence[Sequence[str]], selected: Sequence[str] | None) -> dict[str, float]:
    result = {"mrr": reciprocal_rank(ranking, alternatives)}
    for k in (1, 3, 5):
        result[f"recall_at_{k}"] = recall_at_k(ranking, alternatives, k)
        result[f"hit_at_{k}"] = float(recall_at_k(ranking, alternatives, k) > 0)
        result[f"ndcg_at_{k}"] = ndcg_at_k(ranking, alternatives, k)
    relevant = relevant_union(alternatives)
    result["gold_rank"] = float(next((i for i, c in enumerate(ranking, 1) if c in relevant), 0))
    if selected is None:
        result.update({k: 0.0 for k in ("precision", "recall", "f1", "jaccard", "exact_set_match", "completeness", "over_selection", "under_selection")})
        result["selected_set_parse_failure"] = 1.0
    else:
        result.update(best_set_metrics(selected, alternatives))
        result["selected_set_parse_failure"] = 0.0
    return result
