"""LRU rerank score cache keyed by (query, snippet) hash.

Thread-safe. Size bounded by RERANK_CACHE_SIZE. Tracks hits and misses
so telemetry can surface cache efficiency without extra instrumentation.
"""

import hashlib
import threading

from config.retrieval_config import RERANK_CACHE_SIZE

_lock = threading.Lock()
_cache: dict[str, float] = {}
_order: list[str] = []  # insertion order for LRU eviction
_hits: int = 0
_misses: int = 0


def cache_key(query: str, snippet: str) -> str:
    """Return a SHA-256 hex digest for the (query, snippet) pair."""
    raw = f"{query}\x00{snippet}".encode("utf-8", errors="replace")
    return hashlib.sha256(raw).hexdigest()


def cache_get(key: str) -> float | None:
    """Return cached score or None on a miss. Updates hit/miss counters."""
    global _hits, _misses  # noqa: PLW0603
    with _lock:
        if key in _cache:
            _hits += 1
            # promote to most-recently-used
            try:
                _order.remove(key)
            except ValueError:
                pass
            _order.append(key)
            return _cache[key]
        _misses += 1
        return None


def cache_set(key: str, score: float) -> None:
    """Insert or update a cache entry. Evicts LRU entry when over capacity."""
    with _lock:
        if key in _cache:
            try:
                _order.remove(key)
            except ValueError:
                pass
        elif len(_cache) >= RERANK_CACHE_SIZE:
            # evict least-recently-used
            oldest = _order.pop(0)
            _cache.pop(oldest, None)
        _cache[key] = score
        _order.append(key)


def cache_stats() -> dict:
    """Return snapshot of hit/miss counters and current size."""
    with _lock:
        return {
            "hits": _hits,
            "misses": _misses,
            "size": len(_cache),
            "capacity": RERANK_CACHE_SIZE,
        }


def cache_clear() -> None:
    """Reset cache and counters. Primarily for testing."""
    global _hits, _misses  # noqa: PLW0603
    with _lock:
        _cache.clear()
        _order.clear()
        _hits = 0
        _misses = 0
