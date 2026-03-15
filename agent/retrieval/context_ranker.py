"""Context ranker: hybrid scoring (LLM + symbol/filename/reference) for retrieved snippets."""

import logging
import os
import re

from agent.models.model_client import call_reasoning_model
from agent.prompt_system import get_registry
from config.retrieval_config import (
    ENABLE_CONTEXT_RANKING,
    MAX_CANDIDATES_FOR_RANKING,
    MAX_SNIPPET_CHARS_IN_BATCH,
)

logger = logging.getLogger(__name__)

SAME_FILE_PENALTY = 0.1  # Penalty per duplicate snippet from same file

# Hybrid score weights
WEIGHT_LLM = 0.6
WEIGHT_SYMBOL = 0.2
WEIGHT_FILENAME = 0.1
WEIGHT_REFERENCE = 0.1

# Penalty when query suggests implementation (how/route/handle) but candidate is from tests/
TEST_FILE_PENALTY = 0.25
_IMPL_QUERY_PATTERN = re.compile(
    r"\b(how\s+does|routes?|handles?|implementation|step_|def\s+dispatch)\b",
    re.IGNORECASE,
)

# In-memory cache: (query_normalized, snippet) -> llm_score (for batch fallback / single calls)
_llm_score_cache: dict[tuple[str, str], float] = {}


def compute_symbol_match(query: str, symbol: str) -> float:
    """Return 1 if symbol appears in query (case-insensitive), else 0."""
    if not query or not symbol:
        return 0.0
    q = (query or "").strip().lower()
    s = (symbol or "").strip().lower()
    if not s:
        return 0.0
    # Match whole symbol or as identifier (word boundary)
    return 1.0 if s in q or re.search(rf"\b{re.escape(s)}\b", q) else 0.0


def compute_filename_match(query: str, file_path: str) -> float:
    """Return 1 if filename (basename) appears in query, else 0."""
    if not query or not file_path:
        return 0.0
    basename = os.path.basename(file_path or "").strip()
    if not basename:
        return 0.0
    q = (query or "").strip().lower()
    return 1.0 if basename.lower() in q else 0.0


def compute_reference_score(candidate: dict) -> float:
    """Return 0.5 if snippet came from references, else 0."""
    ctype = (candidate.get("type") or "").strip().lower()
    return 0.5 if ctype == "reference" else 0.0


def _get_llm_relevance_single(query: str, snippet: str) -> float:
    """Single-snippet relevance (fallback when batch fails)."""
    q_norm = (query or "").strip()[:500]
    snip_norm = (snippet or "").strip()[:2000]
    cache_key = (q_norm, snip_norm)
    if cache_key in _llm_score_cache:
        return _llm_score_cache[cache_key]
    snip_truncated = (snippet or "")[:1500] + ("..." if len(snippet or "") > 1500 else "")
    prompt = get_registry().get_instructions(
        "context_ranker_single",
        variables={"query": query or "", "snippet": snip_truncated},
    )
    try:
        out = call_reasoning_model(prompt, task_name="context_ranking", max_tokens=10)
        text = (out or "").strip()
        match = re.search(r"0?\.\d+|1\.0|1|0", text)
        score = float(match.group()) if match else 0.5
        score = max(0.0, min(1.0, score))
        _llm_score_cache[cache_key] = score
        return score
    except Exception as e:
        logger.warning("[context_ranker] LLM relevance failed: %s", e)
        return 0.5


def _get_llm_relevance_batch(query: str, snippets: list[str]) -> list[float]:
    """
    Batch LLM relevance: score multiple snippets in one prompt.
    Reduces latency vs N separate calls.
    Returns list of scores (0-1); fills missing with 0.5.
    """
    if not snippets:
        return []
    snippets_formatted_parts = []
    for i, snip in enumerate(snippets, 1):
        truncated = (snip or "").strip()[:MAX_SNIPPET_CHARS_IN_BATCH]
        if len(snip or "") > MAX_SNIPPET_CHARS_IN_BATCH:
            truncated += "..."
        snippets_formatted_parts.append(f"{i}. {truncated}\n")
    snippets_formatted = "".join(snippets_formatted_parts)
    prompt = get_registry().get_instructions(
        "context_ranker_batch",
        variables={"query": query or "", "snippets": snippets_formatted},
    )
    try:
        max_tokens = min(256, 10 * len(snippets) + 20)
        out = call_reasoning_model(prompt, task_name="context_ranking", max_tokens=max_tokens)
        text = (out or "").strip()
        scores: list[float] = []
        for line in text.splitlines():
            line = line.strip()
            match = re.search(r"0?\.\d+|1\.0|1|0", line)
            if match:
                s = float(match.group())
                scores.append(max(0.0, min(1.0, s)))
            if len(scores) >= len(snippets):
                break
        while len(scores) < len(snippets):
            scores.append(0.5)
        return scores[:len(snippets)]
    except Exception as e:
        logger.warning("[context_ranker] batch LLM relevance failed: %s, falling back to single calls", e)
        return [_get_llm_relevance_single(query, s) for s in snippets]


def _apply_diversity_penalty(scored: list[tuple[dict, float]]) -> list[tuple[dict, float]]:
    """
    Apply same-file penalty: subtract SAME_FILE_PENALTY for each duplicate from the same file.
    Ordinal is by hybrid score rank: first from each file gets no penalty, second -0.1, etc.
    Re-sorts by adjusted score to improve context diversity.
    """
    scored_sorted = sorted(scored, key=lambda x: x[1], reverse=True)
    file_ordinal: dict[str, int] = {}
    adjusted: list[tuple[dict, float]] = []
    for c, score in scored_sorted:
        file_path = c.get("file") or ""
        ordinal = file_ordinal.get(file_path, 0)
        file_ordinal[file_path] = ordinal + 1
        penalty = SAME_FILE_PENALTY * ordinal
        adjusted.append((c, score - penalty))
    adjusted.sort(key=lambda x: x[1], reverse=True)
    return adjusted


def rank_context(query: str, candidates: list[dict]) -> list[dict]:
    """
    Rank candidates by hybrid score.
    score = 0.6*llm + 0.2*symbol + 0.1*filename + 0.1*reference - same_file_penalty
    Uses batch LLM call for lower latency. Applies diversity penalty for same-file duplicates.
    Returns candidates sorted by score descending. Limits to first 20 candidates before ranking.
    """
    if not candidates:
        return []
    if len(candidates) > MAX_CANDIDATES_FOR_RANKING:
        logger.info("[search_budget] truncated to %d candidates (had %d)", MAX_CANDIDATES_FOR_RANKING, len(candidates))
        candidates = candidates[:MAX_CANDIDATES_FOR_RANKING]
    logger.info("[context_ranker] ranking %d candidates (batch LLM)", len(candidates))
    snippets = [c.get("snippet") or "" for c in candidates]
    llm_scores = _get_llm_relevance_batch(query, snippets)
    query_lower = (query or "").strip().lower()
    wants_implementation = bool(_IMPL_QUERY_PATTERN.search(query_lower))
    scored: list[tuple[dict, float]] = []
    for i, c in enumerate(candidates):
        file_path = c.get("file") or ""
        symbol = c.get("symbol") or ""
        llm_score = llm_scores[i] if i < len(llm_scores) else 0.5
        sym_score = compute_symbol_match(query, symbol)
        file_score = compute_filename_match(query, file_path)
        ref_score = compute_reference_score(c)
        total = WEIGHT_LLM * llm_score + WEIGHT_SYMBOL * sym_score + WEIGHT_FILENAME * file_score + WEIGHT_REFERENCE * ref_score
        # Bias toward implementation: penalize test files when query asks about implementation
        path_lower = file_path.lower()
        is_test_file = (
            "/tests/" in path_lower or "\\tests\\" in path_lower
            or path_lower.endswith("/tests") or os.path.basename(path_lower).startswith("test_")
        )
        if wants_implementation and is_test_file:
            total -= TEST_FILE_PENALTY
        scored.append((dict(c), total))
    scored = _apply_diversity_penalty(scored)
    scored.sort(key=lambda x: x[1], reverse=True)
    result = [s[0] for s in scored]
    for c, sc in scored:
        logger.info("[context_ranker] candidate=%s score=%.3f", c.get("file") or "(no file)", sc)
    return result
