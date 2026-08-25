"""Registered retrieval implementations used by ServiceDiscoveryBench."""

from .bge_dense import BGEConfig, BGEDenseRetriever
from .rrf import reciprocal_rank_fusion

__all__ = ["BGEConfig", "BGEDenseRetriever", "reciprocal_rank_fusion"]
