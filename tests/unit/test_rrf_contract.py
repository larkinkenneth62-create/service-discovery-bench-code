from __future__ import annotations

import pytest

from servicediscoverybench.retrieval.rrf import reciprocal_rank_fusion


def test_rrf_constant_and_deterministic_tie_break():
    result = reciprocal_rank_fusion([["b", "a"], ["a", "b"]])
    assert [item[0] for item in result] == ["a", "b"]
    with pytest.raises(ValueError):
        reciprocal_rank_fusion([["a"]], constant=61)
