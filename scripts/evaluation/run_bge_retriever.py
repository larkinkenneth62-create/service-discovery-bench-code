from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from servicediscoverybench.retrieval.bge_dense import BGEConfig, BGEDenseRetriever, exact_inner_product, rank_scores


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def resolve_document(row: dict[str, Any]) -> tuple[str, str]:
    candidate_id = row.get("candidate_id", row.get("unified_candidate_id"))
    document = row.get("model_visible_document", row.get("document"))
    if not isinstance(candidate_id, str) or not candidate_id or not isinstance(document, str):
        raise ValueError("corpus row requires candidate_id/unified_candidate_id and model_visible_document/document")
    return candidate_id, document


def resolve_query(row: dict[str, Any]) -> tuple[str, str, str]:
    request_id = row.get("benchmark_task_id", row.get("request_id"))
    query = row.get("query", row.get("query_text"))
    target = row.get("prediction_target")
    if not all(isinstance(value, str) and value for value in (request_id, query, target)):
        raise ValueError("query row requires request ID, query text, and prediction_target")
    if target not in {"service", "api"}:
        raise ValueError(f"unsupported prediction_target: {target}")
    return request_id, query, target


def encode_or_load(
    retriever: BGEDenseRetriever,
    corpus_path: Path,
    cache_dir: Path,
    label: str,
    batch_size: int,
) -> tuple[list[str], np.ndarray]:
    corpus_hash = sha256_file(corpus_path)
    vectors_path = cache_dir / f"{label}_{corpus_hash[:16]}_vectors.npy"
    ids_path = cache_dir / f"{label}_{corpus_hash[:16]}_ids.json"
    registry_path = cache_dir / f"{label}_{corpus_hash[:16]}_registry.json"
    cache_dir.mkdir(parents=True, exist_ok=True)
    rows = load_jsonl(corpus_path)
    resolved = [resolve_document(row) for row in rows]
    candidate_ids = [item[0] for item in resolved]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError(f"duplicate {label} candidate IDs")
    if vectors_path.is_file() and ids_path.is_file() and registry_path.is_file():
        cached_ids = json.loads(ids_path.read_text(encoding="utf-8"))
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        if cached_ids == candidate_ids and registry.get("corpus_sha256") == corpus_hash:
            vectors = np.load(vectors_path, mmap_mode="r")
            if vectors.shape[0] != len(candidate_ids):
                raise ValueError(f"{label} embedding cache row mismatch")
            return candidate_ids, vectors
    documents = [item[1] for item in resolved]
    tensor = retriever.encode(documents, query=False, batch_size=batch_size)
    vectors = tensor.numpy().astype(np.float32, copy=False)
    np.save(vectors_path, vectors)
    ids_path.write_text(json.dumps(candidate_ids, ensure_ascii=False) + "\n", encoding="utf-8")
    registry_path.write_text(json.dumps({
        "schema_version": 1,
        "retriever": "BGE_DENSE_V2",
        "corpus_level": label,
        "corpus_sha256": corpus_hash,
        "rows": len(candidate_ids),
        "model_id": BGEConfig().model_id,
        "revision": BGEConfig().revision,
        "vectors_file": vectors_path.name,
    }, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return candidate_ids, vectors


def main() -> None:
    parser = argparse.ArgumentParser(description="Run registered BGE_DENSE_V2@200 with one-time corpus encoding")
    parser.add_argument("--service-corpus", type=Path, required=True)
    parser.add_argument("--api-corpus", type=Path, required=True)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--batch-size", type=int, default=32)
    args = parser.parse_args()
    if args.batch_size < 1:
        raise SystemExit("--batch-size must be positive")

    retriever = BGEDenseRetriever(BGEConfig(), device=args.device)
    service_ids, service_vectors = encode_or_load(retriever, args.service_corpus, args.cache_dir, "service", args.batch_size)
    api_ids, api_vectors = encode_or_load(retriever, args.api_corpus, args.cache_dir, "api", args.batch_size)
    corpora = {
        "service": (service_ids, service_vectors),
        "api": (api_ids, api_vectors),
    }

    query_rows = load_jsonl(args.queries)
    resolved = [resolve_query(row) for row in query_rows]
    request_ids = [item[0] for item in resolved]
    if len(request_ids) != len(set(request_ids)):
        raise ValueError("duplicate query request IDs")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as handle:
        for request_id, query, target in resolved:
            candidate_ids, document_vectors = corpora[target]
            query_vector = retriever.encode([query], query=True, batch_size=1).numpy().astype(np.float32, copy=False)
            scores = exact_inner_product(query_vector, document_vectors)[0]
            ranking = rank_scores(candidate_ids, scores, min(BGEConfig().top_depth, len(candidate_ids)))
            handle.write(json.dumps({
                "request_id": request_id,
                "prediction_target": target,
                "retriever": "BGE_DENSE_V2",
                "k": len(ranking),
                "ranking": ranking,
            }, ensure_ascii=False, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
