"""BUILD_CONTEXT tool: graph expansion, symbol body read, reranking, context pruning. Heavy operations only."""

import hashlib
import logging

from agent.memory.state import AgentState
from agent.retrieval.retrieval_cache import get_context_cached, set_context_cached

logger = logging.getLogger(__name__)


def _context_cache_key(candidates: list[dict], project_root: str) -> str:
    """Derive cache key from top candidates."""
    parts = [f"{c.get('file','')}|{c.get('symbol','')}" for c in (candidates or [])[:5]]
    h = hashlib.sha256(";".join(parts).encode()).hexdigest()[:32]
    return f"{project_root}|{h}"


def build_context(candidates: list[dict] | None = None, state: AgentState | None = None) -> dict:
    """
    Build context from candidates. Pipeline: graph expansion → symbol body read → reranker → context pruning.
    Candidates can be passed directly or read from state.context["candidates"] (from prior SEARCH_CANDIDATES step).
    Returns {context_blocks: [...]}.
    """
    if state is None:
        return {"context_blocks": []}

    if not candidates:
        candidates = state.context.get("candidates") or []
    if not candidates:
        return {"context_blocks": []}

    project_root = state.context.get("project_root") or ""
    cache_key = _context_cache_key(candidates, project_root)
    cached = get_context_cached(cache_key, project_root)
    if cached is not None:
        return {"context_blocks": cached}

    # Convert candidates to search_results format expected by run_retrieval_pipeline
    search_results = []
    for c in candidates:
        if isinstance(c, dict):
            search_results.append({
                "file": c.get("file", ""),
                "symbol": c.get("symbol", ""),
                "snippet": c.get("snippet", ""),
                "line": c.get("line"),
            })

    query = state.context.get("query") or state.instruction or ""
    from agent.retrieval.retrieval_pipeline import run_retrieval_pipeline

    run_retrieval_pipeline(search_results, state, query=query)

    context_blocks = state.context.get("ranked_context") or []
    set_context_cached(cache_key, project_root, context_blocks)
    return {"context_blocks": context_blocks}
