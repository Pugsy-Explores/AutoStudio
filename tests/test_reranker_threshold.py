"""Tests for reranker score threshold filtering."""

from unittest.mock import patch

from agent.retrieval.reranker.base_reranker import BaseReranker


class _MockReranker(BaseReranker):
    def _score_pairs(self, pairs):
        # Return scores: first two above 0.15, rest below
        return [0.8, 0.5, 0.1, 0.05, 0.02, 0.01]


def test_threshold_filters_low_scores():
    with patch("agent.retrieval.reranker.base_reranker.RERANK_SCORE_THRESHOLD", 0.15):
        with patch("agent.retrieval.reranker.base_reranker.RERANK_MIN_RESULTS_AFTER_THRESHOLD", 2):
            r = _MockReranker()
            docs = ["d1", "d2", "d3", "d4", "d5", "d6"]
            result = r.rerank("q", docs)
            # Only d1 (0.8) and d2 (0.5) pass threshold; we have 2 >= MIN_RESULTS_AFTER_THRESHOLD
            assert len(result) == 2
            assert result[0][1] == 0.8
            assert result[1][1] == 0.5


def test_threshold_fallback_when_too_few_pass():
    with patch("agent.retrieval.reranker.base_reranker.RERANK_SCORE_THRESHOLD", 0.9):
        with patch("agent.retrieval.reranker.base_reranker.RERANK_MIN_RESULTS_AFTER_THRESHOLD", 3):
            r = _MockReranker()
            docs = ["d1", "d2", "d3", "d4", "d5", "d6"]
            result = r.rerank("q", docs)
            # All scores < 0.9, so above_threshold has 0 items < 3 -> fallback to full list
            assert len(result) == 6
