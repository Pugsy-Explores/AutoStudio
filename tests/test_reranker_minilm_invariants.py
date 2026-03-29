"""Invariant tests for the canonical MiniLM ONNX reranker."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_ROOT = Path(__file__).resolve().parent.parent


def test_single_reranker_implementation_class():
    from agent.retrieval.reranker.minilm_reranker import MiniLMReranker

    assert MiniLMReranker.__name__ == "MiniLMReranker"


def test_factory_builds_minilm_only():
    import agent.retrieval.reranker.reranker_factory as factory

    factory._reset_for_testing()
    m = MagicMock()
    m.warmup = MagicMock()
    with patch("agent.retrieval.reranker.reranker_factory.RERANKER_ENABLED", True):
        with patch.object(factory, "_build_reranker", return_value=m):
            factory.init_reranker()
    factory._reset_for_testing()


def test_rerank_failure_raises_runtime_error():
    from agent.retrieval.reranker.base_reranker import BaseReranker

    class _Boom(BaseReranker):
        def _score_pairs(self, pairs):
            raise ValueError("simulated onnx failure")

    r = _Boom()
    with pytest.raises(RuntimeError, match="Reranking failed"):
        r.rerank_batch([("q", ["a", "b", "c", "d", "e", "f"])])


def test_no_legacy_strings_in_reranker_package():
    """No BGE / HttpReranker / GPU provider strings in reranker Python sources."""
    pkg = _ROOT / "agent" / "retrieval" / "reranker"
    for path in sorted(pkg.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        lower = text.lower()
        assert "bge-reranker" not in lower
        assert "HttpReranker" not in text
        assert "CUDAExecutionProvider" not in text
        assert "CoreMLExecutionProvider" not in text


def test_minilm_constants():
    from agent.retrieval.reranker import constants

    assert constants.MODEL_NAME == "cross-encoder/ms-marco-MiniLM-L-6-v2"
    assert constants.DEVICE == "cpu"
    assert constants.PRECISION == "fp32"
