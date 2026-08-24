from __future__ import annotations

import hashlib
from typing import Iterable


def validate_candidate_space(candidates: Iterable[str], gold: Iterable[str]) -> tuple[bool, str]:
    candidate_list = list(dict.fromkeys(candidates))
    gold_list = list(dict.fromkeys(gold))
    if not gold_list:
        return False, "empty_gold"
    if not set(gold_list).issubset(candidate_list):
        return False, "gold_not_subset"
    if len(candidate_list) <= len(gold_list):
        return False, "no_non_gold_candidate"
    return True, "valid"


def deterministic_negatives(values: Iterable[str], excluded: Iterable[str], seed: str, limit: int = 4) -> list[str]:
    excluded_set = set(excluded)
    eligible = set(values) - excluded_set
    ranked = sorted(eligible, key=lambda value: (hashlib.sha256(f"{seed}\0{value}".encode("utf-8")).hexdigest(), value))
    return ranked[:limit]
