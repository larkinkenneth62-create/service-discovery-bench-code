from __future__ import annotations

import numpy as np

from servicediscoverybench.retrieval.bge_dense import BGEConfig, cls_pool, exact_inner_product, l2_normalize, rank_scores, split_by_prediction_target


def test_registered_bge_contract_is_exact():
    config = BGEConfig()
    assert config.model_id == "BAAI/bge-small-en-v1.5"
    assert config.revision == "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"
    assert config.pooling == "CLS_LAST_HIDDEN_STATE"
    assert config.normalization == "L2" and config.similarity == "INNER_PRODUCT"
    assert config.top_depth == 200


def test_cls_l2_inner_product_and_tie_break():
    hidden = np.array([[[3.0, 4.0], [9.0, 9.0]]], dtype=np.float32)
    pooled = l2_normalize(cls_pool(hidden))
    assert np.allclose(pooled, [[0.6, 0.8]])
    scores = exact_inner_product(pooled, np.array([[0.6, 0.8], [0.6, 0.8]], dtype=np.float32))[0]
    assert [item[0] for item in rank_scores(["b", "a"], scores, 2)] == ["a", "b"]


def test_service_api_corpora_are_separate():
    parts = split_by_prediction_target([{"prediction_target": "service"}, {"prediction_target": "api"}])
    assert len(parts["service"]) == len(parts["api"]) == 1
