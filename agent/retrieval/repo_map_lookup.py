"""Repo map lookup: match query tokens against precomputed symbol map."""

import json
import logging
import os
import re
from pathlib import Path

from config import retrieval_config
from config.repo_graph_config import REPO_MAP_JSON, SYMBOL_GRAPH_DIR

logger = logging.getLogger(__name__)

# Stage 46 — optional typo tier: bounded identifier-like Levenshtein (config-gated).
_MIN_TYPO_TERM_LEN = 6
_TYPO_GENERIC_DENYLIST = frozenset({
    "run", "get", "set", "file", "files", "test", "tests", "util", "utils", "data", "value", "name",
})


def load_repo_map(project_root: str | None = None) -> dict | None:
    """Load repo_map.json; return dict or None."""
    root = Path(project_root or os.environ.get("SERENA_PROJECT_DIR", os.getcwd())).resolve()
    map_path = root / SYMBOL_GRAPH_DIR / REPO_MAP_JSON
    if not map_path.is_file():
        return None
    try:
        with open(map_path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _query_terms(query: str | None) -> set[str]:
    """Normalize query into tokens for matching (alphanumeric, underscores)."""
    if not query or not query.strip():
        return set()
    tokens = re.findall(r"[a-zA-Z0-9_]+", query.strip())
    return {t for t in tokens if len(t) > 1}


def _query_terms_ordered(query: str) -> list[str]:
    """Ordered unique tokens (len > 1) for bigram / canonical candidates."""
    if not query or not query.strip():
        return []
    tokens = re.findall(r"[a-zA-Z0-9_]+", query.strip())
    seen: set[str] = set()
    out: list[str] = []
    for t in tokens:
        if len(t) <= 1:
            continue
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _split_camel_boundaries(segment: str) -> str:
    """Insert underscores between camelCase / number boundaries for tokenization."""
    if not segment:
        return ""
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", segment)
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s)
    return s


def _normalize_identifier_for_match(s: str) -> str:
    """
    Lowercase, map hyphen/space to underscore, collapse repeats.
    Stage 46: deterministic comparable form for single identifier segments.
    """
    if not s or not s.strip():
        return ""
    t = s.strip().lower()
    t = re.sub(r"[\s\-]+", "_", t)
    t = re.sub(r"_+", "_", t)
    return t.strip("_")


def _canonical_identifier(phrase: str) -> str:
    """
    Map StepExecutor / step_executor / step executor / step-executor → step_executor.
    Splits camelCase segments, joins with single underscores.
    """
    if not phrase or not phrase.strip():
        return ""
    parts = re.split(r"[\s\-_]+", phrase.strip())
    out: list[str] = []
    for p in parts:
        if not p:
            continue
        split = _split_camel_boundaries(p)
        for seg in split.split("_"):
            if seg:
                out.append(seg.lower())
    return "_".join(out)


def _identifier_token_forms(query: str) -> set[str]:
    """Canonical string keys for normalized-match tier (singletons + adjacent pairs + full phrase)."""
    forms: set[str] = set()
    ordered = _query_terms_ordered(query)
    for t in ordered:
        c = _canonical_identifier(t)
        if c:
            forms.add(c)
    for i in range(len(ordered) - 1):
        pair = f"{ordered[i]} {ordered[i + 1]}"
        c = _canonical_identifier(pair)
        if c:
            forms.add(c)
    full = _canonical_identifier(query.strip())
    if full:
        forms.add(full)
    return forms


def _normalize_for_typo_compare(s: str) -> str:
    """Lowercase alphanumerics only — identifier-style string for edit distance."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _typo_term_eligible(term: str) -> bool:
    nt = _normalize_for_typo_compare(term)
    if len(nt) < _MIN_TYPO_TERM_LEN:
        return False
    if term.lower() in _TYPO_GENERIC_DENYLIST:
        return False
    return True


def _typo_fallback_candidates(query: str, symbols: dict) -> list[dict]:
    """
    Levenshtein distance ≤ 1 on normalized identifier strings; capped and sorted.
    Only invoked when tiers 1–3 produced no candidates and typo fallback is enabled.
    """
    try:
        from rapidfuzz.distance import Levenshtein
    except ImportError:
        logger.warning("[repo_map] typo fallback: rapidfuzz not available; skipping")
        return []

    max_n = max(0, retrieval_config.REPO_MAP_TYPO_MAX_MATCHES)
    if max_n == 0:
        return []

    ordered_terms = _query_terms_ordered(query)
    if not any(_typo_term_eligible(t) for t in ordered_terms):
        return []

    best: dict[str, tuple[int, dict]] = {}

    for term in ordered_terms:
        if not _typo_term_eligible(term):
            continue
        nt = _normalize_for_typo_compare(term)
        for sym_name in sorted(symbols.keys()):
            ns = _normalize_for_typo_compare(sym_name)
            d = Levenshtein.distance(nt, ns)
            if d > 1:
                continue
            info = symbols[sym_name]
            prev = best.get(sym_name)
            if prev is None or d < prev[0]:
                best[sym_name] = (d, info)

    ranked = sorted(best.items(), key=lambda kv: (kv[1][0], kv[0]))
    out: list[dict] = []
    for sym_name, (_d, info) in ranked[:max_n]:
        out.append({
            "anchor": sym_name,
            "file": info.get("file", ""),
        })
        logger.info("[repo_map] anchor=%s (typo≤1)", sym_name)
    return out


def lookup_repo_map(query: str, project_root: str | None = None) -> list[dict]:
    """
    Match query tokens against repo_map symbols; return anchor candidates.
    Returns [{"anchor": "StepExecutor", "file": "agent/execution/executor.py"}, ...].
    Returns [] if no repo_map or no matches.

    Order: (1) exact key (2) case-insensitive substring (3) canonical identifier
    equality. (4) Optional typo fallback (config): Levenshtein ≤ 1 on normalized
    identifier strings — only if (1)–(3) yield no matches.
    """
    root = Path(project_root or os.environ.get("SERENA_PROJECT_DIR", os.getcwd())).resolve()
    map_path = root / SYMBOL_GRAPH_DIR / REPO_MAP_JSON
    if not map_path.is_file():
        return []

    try:
        with open(map_path, encoding="utf-8") as f:
            repo_map = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.debug("[repo_map] lookup failed to load: %s", e)
        return []

    symbols = repo_map.get("symbols") or {}
    if not symbols:
        return []

    terms = _query_terms(query)
    if not terms:
        return []

    candidates: list[dict] = []
    seen: set[str] = set()

    # Tier 1: exact key match
    for term in terms:
        if term in symbols and term not in seen:
            seen.add(term)
            info = symbols[term]
            candidates.append({
                "anchor": term,
                "file": info.get("file", ""),
            })
            logger.info("[repo_map] anchor=%s", term)

    # Tier 2: case-insensitive substring (original behavior)
    term_lower = {t: t.lower() for t in terms}
    for sym_name, info in symbols.items():
        if sym_name in seen:
            continue
        sym_lower = sym_name.lower()
        for term, t_lower in term_lower.items():
            if t_lower in sym_lower or sym_lower in t_lower:
                seen.add(sym_name)
                candidates.append({
                    "anchor": sym_name,
                    "file": info.get("file", ""),
                })
                logger.info("[repo_map] anchor=%s", sym_name)
                break

    # Tier 3: normalized identifier equality (camel / snake / spaced / hyphen)
    canon_forms = _identifier_token_forms(query)
    sym_canon = {s: _canonical_identifier(s) for s in symbols}
    for sym_name, info in symbols.items():
        if sym_name in seen:
            continue
        sc = sym_canon.get(sym_name) or ""
        if not sc:
            continue
        if sc in canon_forms:
            seen.add(sym_name)
            candidates.append({
                "anchor": sym_name,
                "file": info.get("file", ""),
            })
            logger.info("[repo_map] anchor=%s (canonical)", sym_name)

    if candidates:
        return candidates

    if not retrieval_config.ENABLE_REPO_MAP_TYPO_FALLBACK:
        return []

    return _typo_fallback_candidates(query, symbols)
