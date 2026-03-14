"""LRU cache for retrieval results to avoid redundant graph/vector/Serena calls."""

import logging
import os
from functools import lru_cache
from typing import Any

logger = logging.getLogger(__name__)

RETRIEVAL_CACHE_SIZE = int(os.environ.get("RETRIEVAL_CACHE_SIZE", "100"))


def _make_cache_key(query: str, project_root: str) -> tuple[str, str]:
    """Normalize query and root for cache key."""
    q = (query or "").strip().lower()[:200]
    r = (project_root or "").strip()
    return (q, r)


# Module-level cache: (query, project_root) -> {results, query}
_cache: dict[tuple[str, str], dict] = {}
_cache_order: list[tuple[str, str]] = []


def get_cached(query: str, project_root: str | None = None) -> dict | None:
    """
    Return cached result if present. Returns None on miss.
    """
    key = _make_cache_key(query, project_root or "")
    if key in _cache:
        return _cache[key]
    return None


def set_cached(query: str, project_root: str | None, result: dict) -> None:
    """Store result in cache. Evict oldest if over limit."""
    global _cache, _cache_order
    key = _make_cache_key(query, project_root or "")
    if key in _cache:
        _cache_order.remove(key)
    elif len(_cache) >= RETRIEVAL_CACHE_SIZE and _cache_order:
        oldest = _cache_order.pop(0)
        del _cache[oldest]
    _cache[key] = result
    _cache_order.append(key)


def clear_cache() -> None:
    """Clear all cached results."""
    global _cache, _cache_order
    _cache.clear()
    _cache_order.clear()
