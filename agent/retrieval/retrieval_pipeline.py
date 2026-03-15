"""Retrieval pipeline: anchor detection → expand → read → find_references → build_context. Dispatcher calls this only."""

import logging
import os
from pathlib import Path

from agent.memory.state import AgentState
from agent.retrieval.anchor_detector import detect_anchors
from agent.retrieval.context_builder import build_context_from_symbols
from agent.retrieval.context_pruner import prune_context
from agent.retrieval.context_ranker import rank_context
from agent.retrieval.retrieval_expander import expand_search_results
from agent.retrieval.symbol_expander import expand_from_anchors
from agent.tools import find_referencing_symbols, read_file, read_symbol_body
from config.retrieval_config import (
    DEFAULT_MAX_CHARS,
    ENABLE_CONTEXT_RANKING,
    ENABLE_LOCALIZATION_ENGINE,
    MAX_CONTEXT_SNIPPETS,
    MAX_SEARCH_RESULTS,
)

logger = logging.getLogger(__name__)


def _resolve_path(path: str, project_root: str | None) -> str:
    """Resolve path to absolute; use project_root when path is relative."""
    if not path:
        return path
    p = Path(path)
    if not p.is_absolute() and project_root:
        p = Path(project_root) / path
    return str(p.resolve())


def _build_candidates_from_context(built: dict) -> list[dict]:
    """Build ranker candidates from context_builder output. Snippets are {file, symbol, snippet}."""
    candidates: list[dict] = []
    for s in built.get("symbols") or []:
        if isinstance(s, dict):
            candidates.append({
                "file": s.get("file") or "",
                "symbol": s.get("symbol") or "",
                "snippet": s.get("snippet") or "",
                "type": "symbol",
                **({"line": s["line"]} if s.get("line") is not None else {}),
            })
    for r in built.get("references") or []:
        if isinstance(r, dict):
            snippet = r.get("snippet") or f"{r.get('symbol', '')} at line {r.get('line', '?')}"
            candidates.append({
                "file": r.get("file") or "",
                "symbol": r.get("symbol") or "",
                "snippet": snippet,
                "type": "reference",
                **({"line": r["line"]} if r.get("line") is not None else {}),
            })
    for snip in built.get("snippets") or []:
        if isinstance(snip, dict):
            candidates.append({
                "file": snip.get("file") or "",
                "symbol": snip.get("symbol") or "",
                "snippet": snip.get("snippet") or "",
                "type": "file",
            })
        elif isinstance(snip, str) and snip:
            candidates.append({"file": "", "symbol": "", "snippet": snip, "type": "file"})
    return candidates


def run_retrieval_pipeline(
    search_results: list[dict],
    state: AgentState,
    query: str | None = None,
) -> dict:
    """
    Anchor detection → expand → read_symbol_body/read_file → find_referencing_symbols → build_context.
    Updates state.context (retrieved_*, context_snippets as list of {file, symbol, snippet}, ranked_context).
    Returns aggregated result for the SEARCH step.
    """
    results = (search_results or [])[:MAX_SEARCH_RESULTS]
    if not results:
        return {"results": [], "query": query or "", "anchors": 0}

    anchors = detect_anchors(results, query)
    if not anchors:
        return {"results": results, "query": query or "", "anchors": 0}

    project_root = state.context.get("project_root") or os.environ.get("SERENA_PROJECT_DIR") or os.getcwd()

    localization_candidates: list[dict] = []
    if ENABLE_LOCALIZATION_ENGINE and anchors:
        from agent.retrieval.localization.localization_engine import localize_issue

        trace_id = state.context.get("trace_id") or ""
        localization_candidates = localize_issue(
            query or "", anchors, project_root, trace_id=trace_id
        )
        state.context["localization_candidates"] = localization_candidates

    symbol_snippets = expand_from_anchors(anchors, query or "", project_root)

    expanded = expand_search_results(anchors)
    symbol_results = []
    reference_results = []
    file_snippets = []
    for item in expanded:
        path = _resolve_path(item.get("file") or "", project_root)
        symbol = item.get("symbol") or ""
        action_type = item.get("action") or "read_file"
        line = item.get("line")
        try:
            if action_type == "read_symbol_body" and symbol:
                body = read_symbol_body(symbol, path, line=line)
                file_snippets.append({"file": path, "snippet": body, "symbol": symbol})
                sr = {"file": path, "symbol": symbol, "snippet": body[:500]}
                if line is not None:
                    sr["line"] = line
                symbol_results.append(sr)
            else:
                content = read_file(path)
                snip = (content or "")[:2000]
                file_snippets.append({"file": path, "snippet": snip, "symbol": ""})
            refs = find_referencing_symbols(symbol or path, path)
            reference_results.extend(refs)
        except Exception as e:
            logger.warning("[retrieval_pipeline] expand %s: %s", path, e)

    built = build_context_from_symbols(symbol_results, reference_results, file_snippets)
    state.context["retrieved_symbols"] = built.get("symbols", [])
    state.context["retrieved_references"] = built.get("references", [])
    state.context["retrieved_files"] = built.get("files", [])
    # context_snippets: list of {file, symbol, snippet} per plan
    state.context["context_snippets"] = built.get("snippets", [])

    candidates = _build_candidates_from_context(built)
    if symbol_snippets:
        candidates = symbol_snippets + candidates
    candidates = localization_candidates + candidates
    state.context["context_candidates"] = candidates
    if ENABLE_CONTEXT_RANKING and candidates:
        rank_query = query or state.instruction or ""
        ranked = rank_context(rank_query, candidates)
        final_context = prune_context(
            ranked, max_snippets=MAX_CONTEXT_SNIPPETS, max_chars=DEFAULT_MAX_CHARS
        )
        # Phase 10: optional context compression in repo-scale mode
        if state.context.get("repo_summary"):
            from agent.repo_intelligence.context_compressor import compress_context
            from config.repo_intelligence_config import MAX_CONTEXT_TOKENS

            final_context, compression_ratio = compress_context(
                final_context,
                repo_summary=state.context.get("repo_summary"),
                task_goal=state.instruction or "",
                max_tokens=MAX_CONTEXT_TOKENS,
            )
            state.context["context_compression_ratio"] = compression_ratio
        state.context["ranked_context"] = final_context
        state.context["ranking_scores"] = []
    else:
        state.context["ranked_context"] = []

    search_memory = state.context.get("search_memory") or {}
    if isinstance(search_memory, dict):
        search_memory = dict(search_memory)
        existing = search_memory.get("results") or []
        for s in built.get("snippets", [])[:5]:
            snip = s.get("snippet", "") if isinstance(s, dict) else str(s)
            existing.append({"file": "", "snippet": snip[:500]})
        search_memory["results"] = existing
        state.context["search_memory"] = search_memory

    return {
        "results": results,
        "query": query or "",
        "anchors": len(anchors),
        "expanded": len(expanded),
        "symbols": len(built.get("symbols", [])),
        "references": len(built.get("references", [])),
    }
