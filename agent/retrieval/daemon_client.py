"""Retrieval daemon availability for embedding-backed remote retrieval.

Reranking is always in-process (MiniLM ONNX CPU). The daemon may still host
embeddings and vector/BM25 routes when configured.
"""

from __future__ import annotations

import json
import logging
import urllib.request

from config.retrieval_config import (
    EMBEDDING_USE_DAEMON,
    RETRIEVAL_DAEMON_PORT,
)

logger = logging.getLogger(__name__)

_HEALTH_URL = "http://127.0.0.1"
_NOT_FETCHED = object()  # single sentinel for "cache not yet populated"
_health_cache: dict | None | object = _NOT_FETCHED


def retrieval_daemon_health() -> dict | None:
    """GET /health from retrieval daemon. Cached per process (one fetch). Returns None if unreachable."""
    global _health_cache
    if _health_cache is not _NOT_FETCHED:
        return _health_cache
    try:
        req = urllib.request.Request(
            f"{_HEALTH_URL}:{RETRIEVAL_DAEMON_PORT}/health",
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=2) as resp:
            if resp.status != 200:
                _health_cache = None
                return None
            data = json.loads(resp.read().decode())
            _health_cache = data
            return data
    except Exception as e:
        logger.debug("[daemon_client] health check failed: %s", e)
        _health_cache = None
        return None


def _embedding_satisfies_daemon_routing(data: dict) -> bool:
    """True if daemon can handle embedding routes (loaded, or lazy-load enabled)."""
    if data.get("embedding_routing_ok"):
        return True
    return bool(data.get("embedding_loaded", False))


def retrieval_daemon_available() -> bool:
    """
    True when the retrieval daemon is active and satisfies embedding routing per config.
    """
    data = retrieval_daemon_health()
    if not data:
        return False
    need_embedding = EMBEDDING_USE_DAEMON
    if need_embedding and not _embedding_satisfies_daemon_routing(data):
        return False
    return True


def daemon_embed_available() -> bool:
    """True when daemon is reachable and can serve embeddings (loaded or lazy)."""
    if not EMBEDDING_USE_DAEMON:
        return False
    data = retrieval_daemon_health()
    return bool(data and _embedding_satisfies_daemon_routing(data))


def reset_health_cache() -> None:
    """Clear cached health (e.g. for tests). Next call will refetch."""
    global _health_cache
    _health_cache = _NOT_FETCHED
