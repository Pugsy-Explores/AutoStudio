"""Abstract base class for cross-encoder rerankers.

Subclasses implement _score_pairs() for batched ONNX inference. The only public
entry point is rerank_batch (gating, cache, thresholding).
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod

from agent.retrieval.reranker.cache import cache_get, cache_key, cache_set
from agent.retrieval.reranker.preprocessor import prepare_rerank_pairs
from config.retrieval_config import (
    RERANK_MIN_CANDIDATES,
    RERANK_MIN_RESULTS_AFTER_THRESHOLD,
    RERANK_SCORE_THRESHOLD,
)

logger = logging.getLogger(__name__)


def _score_and_threshold(docs: list[str], scores_by_index: dict[int, float]) -> list[tuple[str, float]]:
    result = [(docs[i], scores_by_index.get(i, 0.0)) for i in range(len(docs))]
    result.sort(key=lambda x: x[1], reverse=True)
    above_threshold = [(d, s) for d, s in result if s >= RERANK_SCORE_THRESHOLD]
    if len(above_threshold) >= RERANK_MIN_RESULTS_AFTER_THRESHOLD:
        return above_threshold
    return result


class BaseReranker(ABC):
    """Cross-encoder reranker: rerank_batch only."""

    def warmup(self) -> None:
        """Cold-start ORT session (single pair)."""
        _ = self._score_pairs([("warmup query", "warmup passage")])

    def rerank_batch(self, requests: list[tuple[str, list[str]]]) -> list[list[tuple[str, float]]]:
        """Score each (query, docs) group. One batched inference for all cache misses."""
        _t0 = time.perf_counter()
        cls_name = self.__class__.__name__
        if not requests:
            return []

        out: list[list[tuple[str, float]] | None] = [None] * len(requests)
        miss_pairs: list[tuple[str, str]] = []
        miss_ref: list[tuple[int, int, str]] = []

        n_gated = 0
        for ri, (query, docs) in enumerate(requests):
            if not docs:
                out[ri] = []
                continue
            if len(docs) < RERANK_MIN_CANDIDATES:
                out[ri] = [(d, 0.0) for d in docs]
                n_gated += 1
                continue
            pairs = prepare_rerank_pairs(query, docs)
            for j, doc in enumerate(docs):
                k = cache_key(query, doc)
                if cache_get(k) is None:
                    miss_pairs.append(pairs[j])
                    miss_ref.append((ri, j, k))

        n_miss = len(miss_pairs)
        _infer_ms = 0.0
        if miss_pairs:
            _ti = time.perf_counter()
            try:
                fresh = self._score_pairs(miss_pairs)
            except Exception as exc:
                raise RuntimeError("Reranking failed") from exc
            _infer_ms = (time.perf_counter() - _ti) * 1000.0
            if len(fresh) != len(miss_pairs):
                raise RuntimeError("Reranking failed")
            for idx, score in enumerate(fresh):
                _ri, _j, k = miss_ref[idx]
                cache_set(k, float(score))

        for ri, (query, docs) in enumerate(requests):
            if out[ri] is not None:
                continue
            scores_by_index: dict[int, float] = {}
            for j, doc in enumerate(docs):
                k = cache_key(query, doc)
                hit = cache_get(k)
                if hit is None:
                    raise RuntimeError("Reranking failed")
                scores_by_index[j] = hit
            out[ri] = _score_and_threshold(docs, scores_by_index)

        _total_ms = (time.perf_counter() - _t0) * 1000.0
        n_pairs = sum(len(d) for _, d in requests)
        logger.info(
            "[reranker.timing] %s.rerank_batch requests=%d pairs=%d gated=%d misses=%d "
            "infer_ms=%.1f total_ms=%.1f",
            cls_name,
            len(requests),
            n_pairs,
            n_gated,
            n_miss,
            _infer_ms,
            _total_ms,
        )
        resolved: list[list[tuple[str, float]]] = []
        for slot in out:
            if slot is None:
                raise RuntimeError("Reranking failed")
            resolved.append(slot)
        return resolved
