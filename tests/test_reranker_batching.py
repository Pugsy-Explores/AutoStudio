"""Tests for RerankQueue and rerank_batch."""

from unittest.mock import patch

from agent.retrieval.reranker.base_reranker import BaseReranker
from agent.retrieval.reranker.rerank_queue import add, clear, flush, pending_count


class _MockReranker(BaseReranker):
    def _score_pairs(self, pairs):
        return [0.5 + i * 0.01 for i in range(len(pairs))]


def _six(prefix: str) -> list[str]:
    return [f"{prefix}{i}" for i in range(6)]


def test_rerank_batch_returns_one_per_request():
    r = _MockReranker()
    requests = [
        ("q1", _six("a")),
        ("q2", _six("b")),
    ]
    with patch("agent.retrieval.reranker.base_reranker.RERANK_MIN_CANDIDATES", 1):
        results = r.rerank_batch(requests)
    assert len(results) == 2
    assert len(results[0]) == 6
    assert len(results[1]) == 6
    assert results[0][0][1] > results[0][1][1]


def test_rerank_queue_add_flush():
    clear()
    r = _MockReranker()
    add("q1", _six("a"))
    add("q2", _six("b"))
    assert pending_count() == 2
    with patch("agent.retrieval.reranker.base_reranker.RERANK_MIN_CANDIDATES", 1):
        results = flush(r)
    assert pending_count() == 0
    assert len(results) == 2
    assert len(results[0]) == 6
    assert len(results[1]) == 6


def test_rerank_queue_flush_empty():
    clear()
    r = _MockReranker()
    results = flush(r)
    assert results == []
