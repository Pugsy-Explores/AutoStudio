"""Tests for the cross-encoder reranker sub-package (MiniLM ONNX CPU)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

class TestCache:
    def setup_method(self):
        from agent.retrieval.reranker.cache import cache_clear

        cache_clear()

    def test_cache_miss_returns_none(self):
        from agent.retrieval.reranker.cache import cache_get, cache_key

        assert cache_get(cache_key("q", "doc")) is None

    def test_cache_hit_returns_score(self):
        from agent.retrieval.reranker.cache import cache_get, cache_key, cache_set

        k = cache_key("query", "snippet text")
        cache_set(k, 0.95)
        assert cache_get(k) == pytest.approx(0.95)

    def test_cache_stats_tracks_hits_and_misses(self):
        from agent.retrieval.reranker.cache import (
            cache_get,
            cache_key,
            cache_set,
            cache_stats,
        )

        k = cache_key("q", "s")
        cache_get(k)
        cache_set(k, 0.5)
        cache_get(k)
        stats = cache_stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 1


# ---------------------------------------------------------------------------
# Preprocessor
# ---------------------------------------------------------------------------

class TestPreprocessor:
    def test_truncation_limits_snippet_tokens(self):
        from agent.retrieval.reranker.preprocessor import prepare_rerank_pairs

        long_snippet = " ".join([f"tok{i}" for i in range(1000)])
        pairs = prepare_rerank_pairs("query", [long_snippet], max_snippet_tokens=50)
        assert len(pairs[0][1].split()) <= 50


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

class TestRerankerFactory:
    def setup_method(self):
        import agent.retrieval.reranker.reranker_factory as factory

        factory._reset_for_testing()

    def test_warm_start_singleton(self):
        import agent.retrieval.reranker.reranker_factory as factory

        mock_reranker = MagicMock()
        with patch("agent.retrieval.reranker.reranker_factory.RERANKER_ENABLED", True):
            with patch.object(factory, "_build_reranker", return_value=mock_reranker):
                factory.init_reranker()
        assert factory._reranker_instance is mock_reranker
        mock_reranker.warmup.assert_called_once()

    def test_init_reranker_raises_on_build_failure(self):
        import agent.retrieval.reranker.reranker_factory as factory

        with patch("agent.retrieval.reranker.reranker_factory.RERANKER_ENABLED", True):
            with patch.object(factory, "_build_reranker", side_effect=RuntimeError("no model")):
                with pytest.raises(RuntimeError):
                    factory.init_reranker()

    def test_create_raises_on_build_failure(self):
        import agent.retrieval.reranker.reranker_factory as factory

        factory._reset_for_testing()
        with patch("agent.retrieval.reranker.reranker_factory.RERANKER_ENABLED", True):
            with patch.object(factory, "_build_reranker", side_effect=RuntimeError("fail")):
                with pytest.raises(RuntimeError):
                    factory.create_reranker()


# ---------------------------------------------------------------------------
# Adaptive gating (rerank_batch)
# ---------------------------------------------------------------------------

class TestAdaptiveGating:
    def test_below_min_candidates_skips_inference(self):
        from agent.retrieval.reranker.base_reranker import BaseReranker

        class _Stub(BaseReranker):
            called = False

            def _score_pairs(self, pairs):
                _Stub.called = True
                return [0.5] * len(pairs)

        stub = _Stub()
        result = stub.rerank_batch([("q", ["a", "b", "c"])])
        assert _Stub.called is False
        assert len(result[0]) == 3
        assert all(score == 0.0 for _, score in result[0])


# ---------------------------------------------------------------------------
# MiniLM logits
# ---------------------------------------------------------------------------

class TestMiniLMLogits:
    def test_2d_last_column(self):
        from agent.retrieval.reranker.minilm_reranker import MiniLMReranker

        r = MiniLMReranker.__new__(MiniLMReranker)
        logits = np.array([[0.1, 0.9], [0.3, 0.7]], dtype=np.float32)
        scores = r._logits_to_scores(logits)
        assert scores == pytest.approx([0.9, 0.7])

    def test_single_logit(self):
        from agent.retrieval.reranker.minilm_reranker import MiniLMReranker

        r = MiniLMReranker.__new__(MiniLMReranker)
        logits = np.array([[0.42], [-0.1]], dtype=np.float32)
        scores = r._logits_to_scores(logits)
        assert scores == pytest.approx([0.42, -0.1])


# ---------------------------------------------------------------------------
# rerank_batch ordering (stub)
# ---------------------------------------------------------------------------

class TestRerankBatchOrder:
    def test_sorted_descending(self):
        from agent.retrieval.reranker.base_reranker import BaseReranker

        class _Mock(BaseReranker):
            def _score_pairs(self, pairs):
                return [float(i) for i in range(len(pairs))]

        reranker = _Mock()
        docs = [f"doc {i}" for i in range(8)]
        result = reranker.rerank_batch([("test", docs)])[0]
        scores = [s for _, s in result]
        assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# Score fusion
# ---------------------------------------------------------------------------

class TestScoreFusion:
    def test_fusion_weights_applied(self):
        from agent.retrieval.retrieval_pipeline import _apply_reranker_scores

        candidates = [
            {"snippet": "doc A", "retriever_score": 0.5},
            {"snippet": "doc B", "retriever_score": 0.2},
        ]
        scored = [("doc A", 0.6), ("doc B", 0.9)]
        result = _apply_reranker_scores(candidates, scored, top_k=2)
        assert result[0]["snippet"] == "doc B"
        assert result[1]["snippet"] == "doc A"


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------

class TestTelemetry:
    def _make_state(self):
        state = MagicMock()
        state.context = {}
        state.instruction = "test query"
        return state

    def test_telemetry_fields_populated(self):
        from agent.retrieval.retrieval_pipeline import _log_rerank_telemetry

        state = self._make_state()
        _log_rerank_telemetry(state, 42, "cpu", 20, 18, 10, 512, skipped_reason=None)
        metrics = state.context["retrieval_metrics"]
        assert metrics["rerank_latency_ms"] == 42
        assert metrics["rerank_device"] == "cpu"
        assert metrics["ranking_method"] == "reranker"
