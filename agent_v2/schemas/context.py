"""
Context schemas — ContextItem, ContextWindow.

Retrieved context must always be ranked, pruned, and bounded before passing to models.
"""
from __future__ import annotations

from pydantic import BaseModel


class ContextItem(BaseModel):
    source: str
    content_summary: str
    relevance_score: float


class ContextWindow(BaseModel):
    items: list[ContextItem]
    max_tokens: int
