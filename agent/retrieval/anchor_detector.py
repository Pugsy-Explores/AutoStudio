"""Anchor detection: filter search results to symbol/class/function matches before expansion."""

import logging
import re

from config.retrieval_config import FALLBACK_TOP_N

logger = logging.getLogger(__name__)


def _query_terms(query: str | None) -> set[str]:
    """Normalize query into tokens for matching (alphanumeric, underscores)."""
    if not query or not query.strip():
        return set()
    tokens = re.findall(r"[a-zA-Z0-9_]+", query.strip().lower())
    return {t for t in tokens if len(t) > 1}


def _query_terms_preserve_case(query: str | None) -> set[str]:
    """Query terms preserving case (for symbol matching)."""
    if not query or not query.strip():
        return set()
    tokens = re.findall(r"[a-zA-Z0-9_]+", query.strip())
    return {t for t in tokens if len(t) > 1}


def detect_anchor(query: str, repo_map: dict | None = None) -> dict | None:
    """
    Detect single anchor from query using repo_map.
    Returns {symbol, confidence} or None.
    1. Exact symbol match -> confidence 1.0
    2. Fuzzy (substring, case-insensitive) -> confidence 0.9
    3. Fallback -> None
    """
    if not repo_map or not query or not query.strip():
        return None
    symbols = repo_map.get("symbols") or {}
    if not symbols:
        return None
    terms = _query_terms_preserve_case(query)
    if not terms:
        return None

    # Exact match
    for term in terms:
        if term in symbols:
            logger.info("[repo_map] anchor=%s", term)
            return {"symbol": term, "confidence": 1.0}

    # Fuzzy: substring match (case-insensitive)
    term_lower = {t: t.lower() for t in terms}
    for sym_name in symbols:
        sym_lower = sym_name.lower()
        for term, t_lower in term_lower.items():
            if t_lower in sym_lower or sym_lower in t_lower:
                logger.info("[repo_map] anchor=%s", sym_name)
                return {"symbol": sym_name, "confidence": 0.9}

    return None


def _snippet_has_definition(snippet: str, query_terms: set[str]) -> bool:
    """True if snippet looks like a class/function definition matching query terms."""
    if not snippet or not query_terms:
        return False
    s = (snippet or "").strip()
    # class ClassName / def function_name
    class_match = re.search(r"\bclass\s+([a-zA-Z0-9_]+)", s)
    if class_match and class_match.group(1).lower() in query_terms:
        return True
    def_match = re.search(r"\bdef\s+([a-zA-Z0-9_]+)", s)
    if def_match and def_match.group(1).lower() in query_terms:
        return True
    # Any query term appearing as identifier
    for term in query_terms:
        if re.search(rf"\b{re.escape(term)}\b", s, re.IGNORECASE):
            return True
    return False


def detect_anchors(
    search_results: list[dict],
    query: str | None = None,
) -> list[dict]:
    """
    Filter search results to anchor matches: symbol present, or snippet contains
    class/def matching query, or definition-like content.
    When no anchors found, return top FALLBACK_TOP_N results.
    """
    if not search_results or not isinstance(search_results, list):
        logger.debug("[anchor_detector] empty results")
        return []
    terms = _query_terms(query)
    anchors = []
    for r in search_results:
        if not isinstance(r, dict):
            continue
        has_symbol = bool(r.get("symbol") or r.get("name_path"))
        snippet = r.get("snippet") or ""
        if has_symbol:
            anchors.append(r)
            continue
        if _snippet_has_definition(snippet, terms):
            anchors.append(r)
            continue
    if not anchors:
        anchors = list(search_results[:FALLBACK_TOP_N])
        logger.info("[anchor_detector] no anchors from %d results, fallback to top %d", len(search_results), len(anchors))
    else:
        logger.info("[anchor_detector] %d anchors from %d results", len(anchors), len(search_results))
    return anchors
