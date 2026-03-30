"""Normalize retrieval snippets to bounded plain strings (reranker, telemetry, pruning)."""

from __future__ import annotations

from typing import Any


def coerce_snippet_text(obj: Any, max_len: int = 20_000) -> str:
    """
    Coerce a snippet field to a UTF-8 string capped at ``max_len``.

    Search hits may occasionally carry non-str payloads; ``str()`` on exotic objects can
    recurse or explode. Lists/tuples of strings are joined; other types use a guarded str().
    """
    if obj is None:
        return ""
    if isinstance(obj, str):
        return obj[:max_len]
    if isinstance(obj, (bytes, bytearray)):
        return bytes(obj[:max_len]).decode("utf-8", errors="replace")
    if isinstance(obj, (list, tuple)):
        parts: list[str] = []
        for x in obj[:80]:
            if isinstance(x, str):
                parts.append(x)
            elif isinstance(x, (bytes, bytearray)):
                parts.append(bytes(x).decode("utf-8", errors="replace"))
            else:
                try:
                    parts.append(str(x))
                except Exception:
                    continue
        joined = " ".join(parts)
        return joined[:max_len]
    try:
        return str(obj)[:max_len]
    except Exception:
        return ""
