"""LRU cache for retrieval results to avoid redundant graph/vector/Serena calls."""

import logging
import os
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
    """Clear all cached search/candidate/context results and Chroma clients keyed by workspace."""
    global _cache, _cache_order, _candidate_cache, _candidate_order, _context_cache, _context_order
    _cache.clear()
    _cache_order.clear()
    _candidate_cache.clear()
    _candidate_order.clear()
    _context_cache.clear()
    _context_order.clear()
    try:
        from agent.retrieval.bm25_retriever import _reset_for_testing as reset_bm25_for_tests

        reset_bm25_for_tests()
    except Exception:
        pass
    try:
        from agent.retrieval.vector_retriever import reset_chroma_clients_for_tests

        reset_chroma_clients_for_tests()
    except Exception:
        pass


# Task 11: candidate_cache (query -> candidate list) and context_cache (symbol -> expanded context), LRU 1024
CANDIDATE_CACHE_SIZE = 1024
CONTEXT_CACHE_SIZE = 1024

_candidate_cache: dict[tuple[str, str], list] = {}
_candidate_order: list[tuple[str, str]] = []

_context_cache: dict[str, list] = {}
_context_order: list[str] = []


def _evict_lru(cache: dict, order: list, max_size: int) -> None:
    """Evict oldest entry if over limit."""
    while len(cache) >= max_size and order:
        oldest = order.pop(0)
        if oldest in cache:
            del cache[oldest]


def get_candidate_cached(query: str, project_root: str | None = None) -> list | None:
    """Return cached candidate list if present. None on miss."""
    key = _make_cache_key(query, project_root or "")
    return _candidate_cache.get(key)


def set_candidate_cached(query: str, project_root: str | None, candidates: list) -> None:
    """Store candidates in cache. Evict oldest if over limit."""
    global _candidate_cache, _candidate_order
    key = _make_cache_key(query, project_root or "")
    if key in _candidate_cache:
        _candidate_order.remove(key)
    else:
        _evict_lru(_candidate_cache, _candidate_order, CANDIDATE_CACHE_SIZE)
    _candidate_cache[key] = candidates
    _candidate_order.append(key)


def _context_key(symbol: str, project_root: str) -> str:
    return f"{project_root}|{symbol}"


def get_context_cached(symbol: str, project_root: str | None = None) -> list | None:
    """Return cached expanded context if present. None on miss."""
    key = _context_key(symbol or "", project_root or "")
    return _context_cache.get(key)


def set_context_cached(symbol: str, project_root: str | None, context_blocks: list) -> None:
    """Store context in cache. Evict oldest if over limit."""
    global _context_cache, _context_order
    key = _context_key(symbol or "", project_root or "")
    if key in _context_cache:
        _context_order.remove(key)
    else:
        _evict_lru(_context_cache, _context_order, CONTEXT_CACHE_SIZE)
    _context_cache[key] = context_blocks
    _context_order.append(key)
