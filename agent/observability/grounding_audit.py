"""Grounding and exploration audit: token-based information overlap. Fully general, no keywords or language assumptions."""

from __future__ import annotations

import re


def _normalize_tokens(text: str) -> set[str]:
    """Lowercase, split on non-alphanumeric, drop tokens len < 3. Return set."""
    if not text or not isinstance(text, str):
        return set()
    tokens = re.split(r"[^a-zA-Z0-9]+", text.lower())
    return {t for t in tokens if len(t) >= 3}


def _extract_context_tokens(rows: list[dict]) -> set[str]:
    """Extract tokens from snippet/content and symbol. Generic, no language logic."""
    out: set[str] = set()
    for r in rows:
        if not isinstance(r, dict):
            continue
        for key in ("snippet", "content", "symbol"):
            val = r.get(key)
            if val and isinstance(val, str):
                out |= _normalize_tokens(val)
    return out
