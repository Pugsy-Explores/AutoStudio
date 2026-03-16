"""Tests for the cross-encoder reranker sub-package.

Covers: hardware detection, cache, preprocessor, symbol detector,
factory (warm-start + failure fallback), adaptive gating, GPU/CPU reranker
correctness, deduplicator, score fusion, and telemetry fields.
"""

from __future__ import annotations

import importlib
import sys
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Hardware detection
# ---------------------------------------------------------------------------

class TestHardwareDetection:
    def test_detect_gpu_when_cuda_available(self):
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = True
        with patch.dict(sys.modules, {"torch": mock_torch}):
            with patch("config.retrieval_config.RERANKER_DEVICE", "auto"):
                import agent.retrieval.reranker.hardware as hw
                importlib.reload(hw)
                with patch.object(hw, "RERANKER_DEVICE", "auto"):
                    result = hw.detect_hardware()
        assert result == "gpu"

    def test_detect_cpu_when_torch_absent(self):
        with patch.dict(sys.modules, {"torch": None}):
            import agent.retrieval.reranker.hardware as hw
            with patch.object(hw, "RERANKER_DEVICE", "auto"):
                result = hw.detect_hardware()
        assert result == "cpu"

    def test_explicit_cpu_override(self):
        import agent.retrieval.reranker.hardware as hw
        with patch.object(hw, "RERANKER_DEVICE", "cpu"):
            assert hw.detect_hardware() == "cpu"

    def test_explicit_gpu_override(self):
        import agent.retrieval.reranker.hardware as hw
        with patch.object(hw, "RERANKER_DEVICE", "gpu"):
            assert hw.detect_hardware() == "gpu"


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
        cache_get(k)  # miss
        cache_set(k, 0.5)
        cache_get(k)  # hit
        stats = cache_stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 1

    def test_cache_eviction_respects_capacity(self):
        from agent.retrieval.reranker import cache as cache_module
        from agent.retrieval.reranker.cache import cache_key, cache_set

        original_size = cache_module.RERANK_CACHE_SIZE
        cache_module.RERANK_CACHE_SIZE = 3
        try:
            for i in range(5):
                cache_set(cache_key(f"q{i}", "s"), float(i))
            assert len(cache_module._cache) <= 3
        finally:
            cache_module.RERANK_CACHE_SIZE = original_size


# ---------------------------------------------------------------------------
# Preprocessor
# ---------------------------------------------------------------------------

class TestPreprocessor:
    def test_truncation_limits_snippet_tokens(self):
        from agent.retrieval.reranker.preprocessor import prepare_rerank_pairs
        long_snippet = " ".join([f"tok{i}" for i in range(1000)])
        pairs = prepare_rerank_pairs("query", [long_snippet], max_snippet_tokens=50)
        assert len(pairs[0][1].split()) <= 50

    def test_pair_token_limit_enforced(self):
        from agent.retrieval.reranker.preprocessor import prepare_rerank_pairs
        snippet = " ".join([f"s{i}" for i in range(400)])
        query = " ".join([f"q{i}" for i in range(200)])
        pairs = prepare_rerank_pairs("query", [snippet], max_snippet_tokens=400, max_pair_tokens=64)
        combined = len(pairs[0][0].split()) + len(pairs[0][1].split())
        assert combined <= 64

    def test_window_long_snippet_preserves_first_tokens(self):
        from agent.retrieval.reranker.preprocessor import prepare_rerank_pairs
        tokens = [f"word{i}" for i in range(500)]
        snippet = " ".join(tokens)
        pairs = prepare_rerank_pairs("q", [snippet], max_snippet_tokens=10)
        result_tokens = pairs[0][1].split()
        assert result_tokens == tokens[:10]


# ---------------------------------------------------------------------------
# Symbol query detector
# ---------------------------------------------------------------------------

class TestSymbolQueryDetector:
    def _detect(self, query: str):
        from agent.retrieval.reranker.symbol_query_detector import is_symbol_query
        return is_symbol_query(query)

    def test_camel_case_bypasses(self):
        bypass, reason = self._detect("RetrievalPipeline")
        assert bypass is True
        assert "camel" in reason

    def test_filename_bypasses(self):
        bypass, reason = self._detect("retrieval_pipeline.py")
        assert bypass is True
        assert reason == "filename_pattern"

    def test_snake_case_symbol_bypasses(self):
        bypass, reason = self._detect("run_retrieval_pipeline")
        assert bypass is True

    def test_natural_language_query_does_not_bypass(self):
        bypass, _ = self._detect("how does the retrieval pipeline handle duplicate results")
        assert bypass is False

    def test_keyword_prefix_bypasses(self):
        bypass, reason = self._detect("def run_pipeline")
        assert bypass is True
        assert reason == "keyword_prefix"


# ---------------------------------------------------------------------------
# Deduplicator
# ---------------------------------------------------------------------------

class TestDeduplicator:
    def test_removes_identical_snippets(self):
        from agent.retrieval.reranker.deduplicator import deduplicate_candidates
        candidates = [
            {"snippet": "foo bar", "file": "a.py"},
            {"snippet": "foo bar", "file": "b.py"},  # duplicate snippet
            {"snippet": "baz qux", "file": "c.py"},
        ]
        result = deduplicate_candidates(candidates)
        assert len(result) == 2
        assert result[0]["file"] == "a.py"
        assert result[1]["file"] == "c.py"

    def test_preserves_original_order(self):
        from agent.retrieval.reranker.deduplicator import deduplicate_candidates
        candidates = [
            {"snippet": f"snippet {i}", "file": f"{i}.py"}
            for i in range(5)
        ]
        result = deduplicate_candidates(candidates)
        assert [c["file"] for c in result] == [f"{i}.py" for i in range(5)]

    def test_empty_list(self):
        from agent.retrieval.reranker.deduplicator import deduplicate_candidates
        assert deduplicate_candidates([]) == []


# ---------------------------------------------------------------------------
# Factory: warm start and failure fallback
# ---------------------------------------------------------------------------

class TestRerankerFactory:
    def setup_method(self):
        import agent.retrieval.reranker.reranker_factory as factory
        factory._reset_for_testing()

    def test_warm_start_singleton(self):
        import agent.retrieval.reranker.reranker_factory as factory
        mock_reranker = MagicMock()
        mock_reranker.rerank.return_value = [("warmup snippet", 0.5)]
        with patch.object(factory, "_build_reranker", return_value=mock_reranker):
            factory.init_reranker()
        assert factory._reranker_instance is mock_reranker
        assert factory._RERANKER_DISABLED is False

    def test_warmup_called_on_init(self):
        import agent.retrieval.reranker.reranker_factory as factory
        mock_reranker = MagicMock()
        mock_reranker.rerank.return_value = [("warmup snippet", 0.5)]
        with patch.object(factory, "_build_reranker", return_value=mock_reranker):
            factory.init_reranker()
        mock_reranker.rerank.assert_called_once_with("warmup query", ["warmup snippet"])

    def test_load_failure_sets_disabled(self):
        import agent.retrieval.reranker.reranker_factory as factory
        with patch.object(factory, "_build_reranker", side_effect=RuntimeError("no model")):
            factory.init_reranker()
        assert factory._RERANKER_DISABLED is True
        assert factory.create_reranker() is None

    def test_warmup_failure_disables_reranker(self):
        import agent.retrieval.reranker.reranker_factory as factory
        mock_reranker = MagicMock()
        mock_reranker.rerank.side_effect = RuntimeError("CUDA OOM")
        with patch.object(factory, "_build_reranker", return_value=mock_reranker):
            factory.init_reranker()
        assert factory._RERANKER_DISABLED is True

    def test_create_returns_none_when_disabled(self):
        import agent.retrieval.reranker.reranker_factory as factory
        factory._RERANKER_DISABLED = True
        assert factory.create_reranker() is None


# ---------------------------------------------------------------------------
# Adaptive gating (via BaseReranker)
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
        # RERANK_MIN_CANDIDATES default is 6 — pass only 3
        result = stub.rerank("q", ["a", "b", "c"])
        assert _Stub.called is False
        assert len(result) == 3
        assert all(score == 0.0 for _, score in result)


# ---------------------------------------------------------------------------
# Reranker correctness (GPU and CPU paths via mock)
# ---------------------------------------------------------------------------

class TestRerankerCorrectness:
    def _make_gpu_reranker_with_mock(self, scores):
        """Patch CrossEncoder so GPUReranker works without a GPU."""
        mock_model = MagicMock()
        mock_model.predict.return_value = scores
        mock_ce_cls = MagicMock(return_value=mock_model)
        mock_ce_cls.return_value = mock_model

        with patch.dict(sys.modules, {
            "sentence_transformers": MagicMock(CrossEncoder=mock_ce_cls),
            "torch": MagicMock(cuda=MagicMock(
                is_available=MagicMock(return_value=False),
                get_device_capability=MagicMock(return_value=(6, 0)),
            )),
        }):
            import agent.retrieval.reranker.gpu_reranker as gm
            importlib.reload(gm)
            reranker = gm.GPUReranker.__new__(gm.GPUReranker)
            reranker.model_name = "test"
            reranker.model = mock_model
        return reranker

    def test_rerank_order_gpu(self):
        from agent.retrieval.reranker.base_reranker import BaseReranker

        class _MockGPU(BaseReranker):
            def _score_pairs(self, pairs):
                # doc "b" gets highest score
                return [0.3 if "a" in p[1] else 0.9 if "b" in p[1] else 0.6 for p in pairs]

        reranker = _MockGPU()
        docs = ["snippet a", "snippet b", "snippet c", "snippet d", "snippet e", "snippet f"]
        result = reranker.rerank("test query", docs)
        scores = [s for _, s in result]
        assert scores == sorted(scores, reverse=True)

    def test_rerank_order_cpu(self):
        from agent.retrieval.reranker.base_reranker import BaseReranker

        class _MockCPU(BaseReranker):
            def _score_pairs(self, pairs):
                return [float(i) for i in range(len(pairs))]

        reranker = _MockCPU()
        docs = [f"doc {i}" for i in range(8)]
        result = reranker.rerank("test", docs)
        scores = [s for _, s in result]
        assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# Failure fallback
# ---------------------------------------------------------------------------

class TestFailureFallback:
    def test_inference_failure_handled_gracefully(self):
        """reranker_factory.create_reranker returns None after repeated failures."""
        import agent.retrieval.reranker.reranker_factory as factory
        factory._reset_for_testing()
        with patch.object(factory, "_build_reranker", side_effect=RuntimeError("fail")):
            result = factory.create_reranker()
        assert result is None


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
        # doc A: 0.6*0.8 + 0.5*0.2 = 0.48 + 0.10 = 0.58
        # doc B: 0.9*0.8 + 0.2*0.2 = 0.72 + 0.04 = 0.76
        assert result[0]["snippet"] == "doc B"
        assert result[1]["snippet"] == "doc A"

    def test_missing_retriever_score_defaults_to_zero(self):
        from agent.retrieval.retrieval_pipeline import _apply_reranker_scores
        candidates = [{"snippet": "doc X"}]  # no retriever_score
        scored = [("doc X", 0.8)]
        result = _apply_reranker_scores(candidates, scored, top_k=1)
        assert result[0]["final_score"] == pytest.approx(0.8 * 0.8)


# ---------------------------------------------------------------------------
# Telemetry fields
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
        assert metrics["candidates_in"] == 20
        assert metrics["rerank_dedup_removed"] == 2
        assert metrics["candidates_out"] == 10
        assert metrics["rerank_tokens"] == 512
        assert metrics["rerank_skipped_reason"] is None

    def test_skipped_reason_symbol_query(self):
        from agent.retrieval.retrieval_pipeline import _log_rerank_telemetry
        state = self._make_state()
        _log_rerank_telemetry(state, 0, "none", 10, 10, 10, 0, skipped_reason="symbol_query:camel_case_identifier")
        assert "symbol_query" in state.context["retrieval_metrics"]["rerank_skipped_reason"]
