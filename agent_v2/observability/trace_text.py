"""Truncate prompt/output for trace and UI (Phase 13 — LLM nodes)."""

from __future__ import annotations

import os

_DEFAULT_MAX = 8000


def trace_text_max_chars() -> int:
    raw = os.getenv("TRACE_LLM_TEXT_MAX_CHARS", str(_DEFAULT_MAX))
    try:
        n = int(raw)
        return max(256, min(n, 500_000))
    except ValueError:
        return _DEFAULT_MAX


def truncate_trace_text(text: str | None, *, max_chars: int | None = None) -> str:
    if not text:
        return ""
    limit = max_chars if max_chars is not None else trace_text_max_chars()
    if len(text) <= limit:
        return text
    return text[: limit - 20] + "\n… [truncated]"
