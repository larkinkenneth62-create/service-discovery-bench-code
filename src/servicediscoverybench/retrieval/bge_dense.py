from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence


@dataclass(frozen=True)
class BGEConfig:
    model_id: str = "BAAI/bge-small-en-v1.5"
    revision: str = "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"
    query_instruction: str = "Represent this sentence for searching relevant passages: "
    document_instruction: str = ""
    pooling: str = "CLS_LAST_HIDDEN_STATE"
    normalization: str = "L2"
    similarity: str = "INNER_PRODUCT"
    dtype: str = "float32"
    max_length: int = 512
    top_depth: int = 200


def cls_pool(last_hidden_state: Any) -> Any:
    """Return the first-token embedding from [batch, tokens, dimensions]."""
    if getattr(last_hidden_state, "ndim", None) != 3:
        raise ValueError("last_hidden_state must have three dimensions")
    return last_hidden_state[:, 0, :]


def l2_normalize(vectors: Any) -> Any:
    """L2-normalize NumPy or Torch vectors without changing row order."""
    module = type(vectors).__module__
    if module.startswith("torch"):
        import torch

        norms = torch.linalg.vector_norm(vectors, ord=2, dim=-1, keepdim=True)
        return vectors / torch.clamp(norms, min=torch.finfo(vectors.dtype).eps)
    import numpy as np

    values = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(values, axis=-1, keepdims=True)
    return values / np.maximum(norms, np.finfo(np.float32).eps)


def rank_scores(candidate_ids: Sequence[str], scores: Sequence[float], top_k: int) -> list[tuple[str, float]]:
    if len(candidate_ids) != len(scores):
        raise ValueError("candidate and score lengths differ")
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("candidate IDs must be unique")
    if top_k < 1:
        raise ValueError("top_k must be positive")
    return sorted(zip(candidate_ids, (float(score) for score in scores), strict=True), key=lambda item: (-item[1], item[0]))[:top_k]


def exact_inner_product(query_vectors: Any, document_vectors: Any) -> Any:
    if type(query_vectors).__module__.startswith("torch"):
        return query_vectors @ document_vectors.T
    import numpy as np

    return np.asarray(query_vectors, dtype=np.float32) @ np.asarray(document_vectors, dtype=np.float32).T


def split_by_prediction_target(rows: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    partitions = {"service": [], "api": []}
    for row in rows:
        target = row.get("prediction_target")
        if target not in partitions:
            raise ValueError(f"unsupported prediction_target: {target}")
        partitions[target].append(row)
    return partitions


class BGEDenseRetriever:
    """Registered BGE_DENSE_V2 encoder with exact inner-product ranking."""

    def __init__(self, config: BGEConfig = BGEConfig(), *, device: str = "cpu") -> None:
        if config != BGEConfig():
            raise ValueError("the registered BGE_DENSE_V2 configuration is immutable")
        from transformers import AutoModel, AutoTokenizer

        self.config = config
        self.device = device
        self.tokenizer = AutoTokenizer.from_pretrained(config.model_id, revision=config.revision, trust_remote_code=False)
        self.model = AutoModel.from_pretrained(config.model_id, revision=config.revision, trust_remote_code=False).to(device)
        self.model.eval()

    def encode(self, texts: Sequence[str], *, query: bool, batch_size: int = 32) -> Any:
        import torch

        prefix = self.config.query_instruction if query else self.config.document_instruction
        outputs = []
        for start in range(0, len(texts), batch_size):
            batch = [prefix + text for text in texts[start : start + batch_size]]
            encoded = self.tokenizer(batch, padding=True, truncation=True, max_length=self.config.max_length, return_tensors="pt")
            encoded = {name: value.to(self.device) for name, value in encoded.items()}
            with torch.inference_mode():
                hidden = self.model(**encoded).last_hidden_state
            outputs.append(l2_normalize(cls_pool(hidden)).to(dtype=torch.float32).cpu())
        return torch.cat(outputs, dim=0)

    def retrieve(self, query: str, candidate_ids: Sequence[str], documents: Sequence[str], *, top_k: int = 200) -> list[tuple[str, float]]:
        if len(candidate_ids) != len(documents):
            raise ValueError("candidate and document lengths differ")
        query_vector = self.encode([query], query=True)
        document_vectors = self.encode(documents, query=False)
        scores = exact_inner_product(query_vector, document_vectors)[0].tolist()
        return rank_scores(candidate_ids, scores, min(top_k, len(candidate_ids)))
