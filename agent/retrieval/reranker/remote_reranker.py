"""Offload cross-encoder reranking to retrieval daemon ``POST /rerank/batch`` (batched ONNX on server)."""

from __future__ import annotations

import logging

from agent.retrieval.daemon_retrieval_client import try_daemon_rerank_batch
from agent.retrieval.reranker.base_reranker import BaseReranker

logger = logging.getLogger(__name__)


class RemoteMiniLMReranker(BaseReranker):
    """Delegates ``rerank_batch`` to daemon; one HTTP request per chunk (see RETRIEVAL_DAEMON_RERANK_BATCH_MAX_SLOTS)."""

    def _score_pairs(self, pairs: list[tuple[str, str]]) -> list[float]:
        raise RuntimeError("RemoteMiniLMReranker: internal error — use rerank_batch only")

    def warmup(self) -> None:
        logger.debug("[RemoteMiniLMReranker] warmup skipped (daemon loads ONNX on first /rerank/batch)")

    def rerank_batch(self, requests: list[tuple[str, list[str]]]) -> list[list[tuple[str, float]]]:
        out = try_daemon_rerank_batch(requests)
        if out is None:
            raise RuntimeError(
                "Reranking failed: remote reranker unavailable "
                "(set RETRIEVAL_REMOTE_RERANK_FIRST=0 for in-process MiniLM or fix daemon /rerank/batch)"
            )
        return out
