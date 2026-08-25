from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence


RRF_CONSTANT = 60


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[str]], *, constant: int = RRF_CONSTANT, top_k: int | None = None
) -> list[tuple[str, float]]:
    if constant != RRF_CONSTANT:
        raise ValueError("registered RRF constant is fixed at 60")
    if not rankings:
        raise ValueError("at least one ranking is required")
    scores: dict[str, float] = defaultdict(float)
    for ranking in rankings:
        if len(ranking) != len(set(ranking)):
            raise ValueError("each input ranking must contain unique IDs")
        for rank, candidate_id in enumerate(ranking, 1):
            scores[candidate_id] += 1.0 / (constant + rank)
    result = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    if top_k is not None:
        if top_k < 1:
            raise ValueError("top_k must be positive")
        result = result[:top_k]
    return result
