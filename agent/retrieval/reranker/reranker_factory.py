"""Reranker singleton: in-process MiniLM ONNX or remote daemon ``/rerank/batch``."""

from __future__ import annotations

import logging

from agent.retrieval.daemon_retrieval_client import remote_rerank_http_enabled
from agent.retrieval.reranker.base_reranker import BaseReranker
from config.retrieval_config import RERANKER_ENABLED

logger = logging.getLogger(__name__)

_reranker_instance: BaseReranker | None = None


def create_reranker() -> BaseReranker | None:
    """Return the singleton reranker, or None when ``RERANKER_ENABLED=0``."""
    global _reranker_instance  # noqa: PLW0603

    if not RERANKER_ENABLED:
        return None

    if _reranker_instance is None:
        _reranker_instance = _build_reranker()

    return _reranker_instance


def init_reranker() -> None:
    """Build the reranker singleton and run a warmup inference pass. Raises on failure."""
    global _reranker_instance  # noqa: PLW0603

    if not RERANKER_ENABLED:
        return

    logger.debug("[init_reranker]")
    instance = _build_reranker()
    instance.warmup()
    _reranker_instance = instance
    if remote_rerank_http_enabled():
        logger.info("[reranker] warm-start complete (remote /rerank/batch)")
    else:
        logger.info("[reranker] warm-start complete (MiniLM ONNX CPU)")


def _build_reranker() -> BaseReranker:
    if remote_rerank_http_enabled():
        from agent.retrieval.reranker.remote_reranker import RemoteMiniLMReranker  # noqa: PLC0415

        return RemoteMiniLMReranker()

    from agent.retrieval.reranker.minilm_reranker import MiniLMReranker  # noqa: PLC0415

    r = MiniLMReranker()
    assert r.model_name == "cross-encoder/ms-marco-MiniLM-L-6-v2"
    assert r.device == "cpu"
    return r


def _reset_for_testing() -> None:
    """Reset factory state. Only for use in tests."""
    global _reranker_instance  # noqa: PLW0603
    _reranker_instance = None
