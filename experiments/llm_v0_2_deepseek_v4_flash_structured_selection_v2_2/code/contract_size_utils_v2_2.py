from __future__ import annotations

import json
from typing import Any


TOP5_RANKING_V1 = "TOP5_RANKING_V1"
SELECTED_SET_V1 = "SELECTED_SET_V1"
RANKING_AND_SELECTED_SET_V1_10 = "RANKING_AND_SELECTED_SET_V1_10"


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def array_bound_bytes(field: str, ids: list[str]) -> int:
    """Return the exact UTF-8 size of the largest legal one-array object."""
    if not isinstance(field, str) or not field:
        raise ValueError("field must be a non-empty string")
    if any(not isinstance(value, str) for value in ids):
        raise ValueError("candidate IDs must be strings")
    return len(stable_json({field: ids}).encode("utf-8"))


def legal_answer_bound_bytes(contract: str, candidate_ids: list[str]) -> int:
    """Conservative UTF-8 byte upper bound over the full legal answer space."""
    if not candidate_ids or len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("candidate IDs must be non-empty and unique")
    longest = sorted(
        candidate_ids,
        key=lambda value: (-len(stable_json(value).encode("utf-8")), value),
    )[: min(5, len(candidate_ids))]
    ranked = array_bound_bytes("ranked_candidate_ids", longest)
    selected = array_bound_bytes("selected_candidate_ids", candidate_ids)
    if contract == TOP5_RANKING_V1:
        return ranked
    if contract == SELECTED_SET_V1:
        return selected
    if contract == RANKING_AND_SELECTED_SET_V1_10:
        # The two separately bounded objects deliberately over-count their
        # braces.  The additional separator allowance keeps this a simple,
        # auditable upper bound rather than a provider-token estimate.
        return ranked + selected + 8
    raise ValueError(f"unknown output contract: {contract}")
