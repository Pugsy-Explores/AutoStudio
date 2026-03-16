"""Single source of truth for retrieval daemon availability and routing.

The daemon hosts both the embedding model and the reranker. When the daemon is
available, the application must route all retrieval through it: embedding
generation (candidate retrieval) and reranking. This module explicitly detects
daemon availability via GET /health and exposes predicates so callers use the
daemon-backed inference path instead of falling back to local or partial logic.
"""

from __future__ import annotations

import json
import logging
import urllib.request

from config.retrieval_config import (
    EMBEDDING_USE_DAEMON,
    RETRIEVAL_DAEMON_PORT,
    RERANKER_USE_DAEMON,
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


def retrieval_daemon_available() -> bool:
    """
    True when the retrieval daemon is active and has both stages we need per config.
    When True, application logic must route retrieval through the daemon: embedding
    for candidate retrieval and reranker for refining results. No local fallback.
    """
    data = retrieval_daemon_health()
    if not data:
        return False
    reranker_loaded = data.get("reranker_loaded", False)
    embedding_loaded = data.get("embedding_loaded", False)
    need_reranker = RERANKER_USE_DAEMON
    need_embedding = EMBEDDING_USE_DAEMON
    if need_reranker and not reranker_loaded:
        return False
    if need_embedding and not embedding_loaded:
        return False
    return True


def daemon_reranker_available() -> bool:
    """True when daemon is reachable and has reranker loaded. Use for reranker routing."""
    data = retrieval_daemon_health()
    if not data:
        return False
    return data.get("reranker_loaded", False)


def daemon_embed_available() -> bool:
    """True when daemon is reachable and has embedding loaded. Use for embedding routing."""
    if not EMBEDDING_USE_DAEMON:
        return False
    data = retrieval_daemon_health()
    return bool(data and data.get("embedding_loaded", False))


def reset_health_cache() -> None:
    """Clear cached health (e.g. for tests). Next call will refetch."""
    global _health_cache
    _health_cache = _NOT_FETCHED
