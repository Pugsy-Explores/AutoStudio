"""RerankQueue: batch multiple (query, docs) requests for single model inference.

Collects add(query, docs) calls and on flush() runs one batched inference.
Configurable via RERANK_BATCH_WINDOW_MS (future: time-based flush).
"""

from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger(__name__)

_queue: list[tuple[str, list[str]]] = []
_lock = threading.Lock()


def add(query: str, docs: list[str]) -> None:
    """Add (query, docs) to the rerank queue."""
    if not query or not docs:
        return
    with _lock:
        _queue.append((query, docs))


def flush(reranker) -> list[list[tuple[str, float]]]:
    """Process all queued (query, docs) with one batched inference; return results per request."""
    with _lock:
        requests = list(_queue)
        _queue.clear()
    if not requests:
        return []
    if not hasattr(reranker, "rerank_batch"):
        # Fallback: sequential rerank
        return [reranker.rerank(q, d) for q, d in requests]
    return reranker.rerank_batch(requests)


def pending_count() -> int:
    """Return number of queued requests (for tests)."""
    with _lock:
        return len(_queue)


def clear() -> None:
    """Clear queue (for tests)."""
    with _lock:
        _queue.clear()
