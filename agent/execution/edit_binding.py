"""EDIT_BINDING layer: explicit binding between location, evidence, and justification.

Provides accountability: "This edit is made HERE because of THIS evidence."
No heuristics, no keyword matching, no domain logic. Only reuses ranked_context ordering.
"""

from __future__ import annotations

from typing import Any


def build_edit_binding(state: Any) -> dict[str, Any] | None:
    """
    Build edit binding from top-ranked context row.
    Returns None when no context; otherwise {file, symbol, evidence, justification}.
    """
    if state is None:
        return None
    ctx = getattr(state, "context", None) or {}
    rows = ctx.get("ranked_context") or []

    if not rows:
        return None

    row = rows[0]
    if not isinstance(row, dict):
        return None

    binding: dict[str, Any] = {
        "file": row.get("file"),
        "symbol": row.get("symbol"),
        "evidence": [],
        "justification": "",
    }

    content = row.get("content") or row.get("snippet") or ""
    if content:
        binding["evidence"].append(content[:300])

    binding["justification"] = "Edit derived from retrieved implementation context"

    return binding
