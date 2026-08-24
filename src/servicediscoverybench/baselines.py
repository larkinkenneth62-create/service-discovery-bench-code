"""Dependency-free deterministic retrieval baselines."""

from __future__ import annotations

import hashlib
import math
import random
import re
from collections import Counter
from collections.abc import Mapping, Sequence

from .normalize import normalize_text


TOKEN_RE = re.compile(r"[\w]+|[^\w\s]", re.UNICODE)


def tokens(text: object) -> list[str]:
    return TOKEN_RE.findall(normalize_text(str(text or ""), casefold=True))


def random_ranking(candidate_ids: Sequence[str], *, seed: int, task_id: str) -> list[str]:
    values = list(dict.fromkeys(candidate_ids))
    digest = hashlib.sha256(f"{seed}\0{task_id}".encode("utf-8")).digest()
    rng = random.Random(int.from_bytes(digest[:8], "big"))
    rng.shuffle(values)
    return values


def bm25_ranking(query: str, candidate_ids: Sequence[str], documents: Mapping[str, str], *, k1: float = 1.5, b: float = 0.75) -> list[str]:
    ids = list(dict.fromkeys(candidate_ids))
    docs = {identifier: tokens(documents.get(identifier, identifier)) for identifier in ids}
    query_terms = tokens(query)
    average_length = sum(map(len, docs.values())) / len(docs) if docs else 1.0
    document_frequency = Counter()
    for terms in docs.values():
        document_frequency.update(set(terms))
    count = len(ids)
    scores = {}
    for identifier in ids:
        terms = docs[identifier]
        frequencies = Counter(terms)
        score = 0.0
        for term in query_terms:
            frequency = frequencies[term]
            if not frequency:
                continue
            df = document_frequency[term]
            idf = math.log(1.0 + (count - df + 0.5) / (df + 0.5))
            denominator = frequency + k1 * (1.0 - b + b * len(terms) / max(average_length, 1.0))
            score += idf * frequency * (k1 + 1.0) / denominator
        scores[identifier] = score
    return sorted(ids, key=lambda identifier: (-scores[identifier], identifier))


def _hashed_ngram_vector(text: str, dimensions: int = 2048) -> dict[int, float]:
    normalized = normalize_text(text, casefold=True)
    padded = f"  {normalized}  "
    counts: Counter[int] = Counter()
    for size in (2, 3, 4):
        for index in range(max(0, len(padded) - size + 1)):
            gram = padded[index:index + size]
            bucket = int.from_bytes(hashlib.sha256(gram.encode("utf-8")).digest()[:8], "big") % dimensions
            counts[bucket] += 1
    norm = math.sqrt(sum(value * value for value in counts.values()))
    return {key: value / norm for key, value in counts.items()} if norm else {}


def _cosine(left: Mapping[int, float], right: Mapping[int, float]) -> float:
    if len(left) > len(right):
        left, right = right, left
    return sum(value * right.get(key, 0.0) for key, value in left.items())


def local_embedding_ranking(query: str, candidate_ids: Sequence[str], documents: Mapping[str, str], *, dimensions: int = 2048) -> list[str]:
    """Rank by deterministic character n-gram hashing embeddings and cosine."""

    ids = list(dict.fromkeys(candidate_ids))
    query_vector = _hashed_ngram_vector(query, dimensions)
    scores = {identifier: _cosine(query_vector, _hashed_ngram_vector(documents.get(identifier, identifier), dimensions)) for identifier in ids}
    return sorted(ids, key=lambda identifier: (-scores[identifier], identifier))
