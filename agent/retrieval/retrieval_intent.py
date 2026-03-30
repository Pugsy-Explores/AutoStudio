"""Heuristic retrieval intent classification (no LLM). Used to bias candidate ordering only."""

from __future__ import annotations

import re

INTENT_FILE = "file"
INTENT_SYMBOL = "symbol"
INTENT_REGION = "region"
INTENT_ARCHITECTURE = "architecture"
INTENT_GENERIC = "generic"

_FILE_HINTS = frozenset(
    "file module package entry point entrypoint settings config init path".split()
)
_SYMBOL_HINTS = frozenset(
    "function class method variable field constant handler loader executor".split()
)
# Single-word hints only; avoid "where" alone (too many false positives).
_REGION_HINTS = frozenset(
    "block branch fallback condition loop logic handles".split()
)
_ARCH_PATTERNS = (
    re.compile(r"how does .+ connect", re.I),
    re.compile(r"flow from .+ to", re.I),
    re.compile(r"entry point and", re.I),
    re.compile(r"\bwiring\b", re.I),
    re.compile(r"how .+ connect(s)? to", re.I),
)


def classify_query_intent(query: str) -> str:
    """Return one of INTENT_* constants."""
    if not query or not str(query).strip():
        return INTENT_GENERIC
    q = str(query).strip().lower()
    words = set(re.findall(r"[a-z0-9_]+", q))
    if any(p.search(q) for p in _ARCH_PATTERNS):
        return INTENT_ARCHITECTURE
    if _FILE_HINTS & words:
        return INTENT_FILE
    if _SYMBOL_HINTS & words:
        return INTENT_SYMBOL
    if _REGION_HINTS & words or any(h in q for h in ("branch", "fallback", "condition", "loop")):
        return INTENT_REGION
    if "where do we" in q:
        return INTENT_REGION
    return INTENT_GENERIC


def _token_count(query: str) -> int:
    return len(re.findall(r"\S+", str(query or "")))


def _base_score(c: dict) -> float:
    for k in ("retriever_score", "final_score", "score"):
        if c.get(k) is not None:
            try:
                return float(c.get(k) or 0.0)
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def apply_intent_bias(candidates: list[dict], query: str | None) -> list[dict]:
    """
    Add intent_boost (default 0) and selection_score = base_score + intent_boost.
    Does not mutate retriever_score / final_score / score.
    """
    if not candidates:
        return candidates
    intent = classify_query_intent(query or "")
    short = _token_count(query or "") < 4
    if intent == INTENT_GENERIC or short:
        out = []
        for c in candidates:
            if not isinstance(c, dict):
                out.append(c)
                continue
            nc = dict(c)
            nc.setdefault("intent_boost", 0.0)
            nc["selection_score"] = _base_score(nc) + float(nc.get("intent_boost") or 0.0)
            out.append(nc)
        return out

    out: list[dict] = []
    for c in candidates:
        if not isinstance(c, dict):
            out.append(c)
            continue
        nc = dict(c)
        kind = (nc.get("candidate_kind") or "").strip().lower()
        boost = 0.0
        if intent == INTENT_FILE and kind == "file":
            boost = 0.15
        elif intent == INTENT_SYMBOL and kind == "symbol":
            boost = 0.15
        elif intent == INTENT_REGION and kind == "region":
            boost = 0.10
        elif intent == INTENT_ARCHITECTURE:
            rels = nc.get("relations")
            if isinstance(rels, list) and len(rels) > 0:
                boost = 0.20
        nc["intent_boost"] = boost
        nc["selection_score"] = _base_score(nc) + boost
        out.append(nc)
    return out
