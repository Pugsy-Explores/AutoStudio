"""Abstract base class for cross-encoder rerankers.

Owns the cache integration, adaptive gating, and preprocessing so
subclasses only need to implement _score_pairs() with batched inference.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from agent.retrieval.reranker.cache import cache_get, cache_key, cache_set
from agent.retrieval.reranker.preprocessor import prepare_rerank_pairs
from config.retrieval_config import (
    RERANK_MIN_CANDIDATES,
    RERANK_MIN_RESULTS_AFTER_THRESHOLD,
    RERANK_SCORE_THRESHOLD,
)


class BaseReranker(ABC):
    """Cross-encoder reranker interface.

    rerank() handles the full pipeline:
      adaptive gating → preprocessing → cache → batched inference → merge.

    Subclasses implement _score_pairs(pairs) which receives only the
    cache-miss pairs and must return one float score per pair.
    """

    def rerank(self, query: str, docs: list[str]) -> list[tuple[str, float]]:
        """Score and sort docs by relevance to query.

        Returns list of (doc, score) sorted descending. When the adaptive
        gate fires (too few docs), returns docs with score 0.0 in original
        order so callers can proceed without branching.
        """
        if not docs:
            return []

        # Adaptive gating — skip inference when the candidate set is tiny
        if len(docs) < RERANK_MIN_CANDIDATES:
            return [(d, 0.0) for d in docs]

        pairs = prepare_rerank_pairs(query, docs)

        # Split pairs into cache hits and misses
        keys = [cache_key(query, doc) for doc in docs]
        cached_scores: dict[int, float] = {}
        miss_indices: list[int] = []
        miss_pairs: list[tuple[str, str]] = []

        for i, k in enumerate(keys):
            hit = cache_get(k)
            if hit is not None:
                cached_scores[i] = hit
            else:
                miss_indices.append(i)
                miss_pairs.append(pairs[i])

        # Batch-score cache misses
        if miss_pairs:
            fresh_scores = self._score_pairs(miss_pairs)
            for idx, score in zip(miss_indices, fresh_scores):
                cache_set(keys[idx], score)
                cached_scores[idx] = score

        # Reconstruct full scored list
        result = [(docs[i], cached_scores.get(i, 0.0)) for i in range(len(docs))]
        result.sort(key=lambda x: x[1], reverse=True)

        # Score threshold filter: discard low-relevance results
        above_threshold = [(d, s) for d, s in result if s >= RERANK_SCORE_THRESHOLD]
        if len(above_threshold) >= RERANK_MIN_RESULTS_AFTER_THRESHOLD:
            return above_threshold
        # Fallback: keep top_k when too few pass threshold
        return result

    def rerank_batch(self, requests: list[tuple[str, list[str]]]) -> list[list[tuple[str, float]]]:
        """Batch rerank multiple (query, docs) requests in one inference pass.

        Returns one list of (doc, score) per request, each sorted descending.
        """
        if not requests:
            return []
        all_pairs: list[tuple[str, str]] = []
        all_docs: list[list[str]] = []
        for query, docs in requests:
            if not docs:
                all_docs.append([])
                continue
            pairs = prepare_rerank_pairs(query, docs)
            all_docs.append(docs)
            for q, s in pairs:
                all_pairs.append((q, s))
        if not all_pairs:
            return [[(d, 0.0) for d in docs] for _, docs in requests]

        # Single batched inference
        scores = self._score_pairs(all_pairs)
        # Split scores back by request
        idx = 0
        results: list[list[tuple[str, float]]] = []
        for docs in all_docs:
            n = len(docs)
            if n == 0:
                results.append([])
                continue
            chunk = list(zip(docs, scores[idx : idx + n]))
            idx += n
            chunk.sort(key=lambda x: x[1], reverse=True)
            results.append(chunk)
        return results

    @abstractmethod
    def _score_pairs(self, pairs: list[tuple[str, str]]) -> list[float]:
        """Score a batch of (query, snippet) pairs.

        Must return exactly len(pairs) float scores. Higher is more relevant.
        Implementations must use batched inference — no per-pair loops.
        """
        ...
