"""SEARCH_CANDIDATES tool: candidate discovery only (BM25, vector, repo_map, grep). No graph expansion or context building."""

import logging
import os

from agent.memory.state import AgentState
from agent.observability.trace_logger import log_event

logger = logging.getLogger(__name__)


def search_candidates(
    query: str,
    state: AgentState | None = None,
    artifact_mode: str = "code",
) -> dict:
    """
    Perform candidate discovery only. Returns top 20 candidates.
    Allowed: BM25, vector, repo_map, grep.
    NOT allowed: graph expansion, symbol body read, LLM ranking, context building.
    """
    if artifact_mode not in ("code", "docs"):
        raise ValueError(f"Invalid artifact_mode: {artifact_mode!r} (allowed: 'code', 'docs')")
    return search_candidates_with_mode(query, state=state, artifact_mode=artifact_mode)


def search_candidates_with_mode(
    query: str,
    state: AgentState | None = None,
    artifact_mode: str = "code",
) -> dict:
    """
    Phase 5A: Explicit retrieval lane selection via artifact_mode.

    - artifact_mode="code": existing behavior (BM25/vector/repo_map/grep via retrieval_pipeline.search_candidates)
    - artifact_mode="docs": deterministic filesystem docs scan (agent.retrieval.docs_retriever)
    """
    if not query or not query.strip():
        return {"candidates": []}

    project_root = None
    if state and state.context:
        project_root = state.context.get("project_root")
    if not project_root:
        project_root = os.environ.get("SERENA_PROJECT_DIR") or os.getcwd()

    if artifact_mode == "docs":
        from agent.retrieval.docs_retriever import search_docs_candidates_with_stats

        candidates, stats = search_docs_candidates_with_stats(query, project_root=project_root)
        if state and state.context and state.context.get("trace_id"):
            trace_id = state.context.get("trace_id")
            log_event(
                trace_id,
                "docs_candidates",
                {
                    "artifact_mode": "docs",
                    "scanned": int(stats.get("scanned", 0)),
                    "included": int(stats.get("included", 0)),
                    "excluded": int(stats.get("excluded", 0)),
                    "top_ranked": stats.get("top_ranked", [])[:8],
                },
            )
        return {"candidates": candidates}

    # Default: code lane (preserve existing behavior)
    from agent.retrieval.retrieval_pipeline import search_candidates as _search_candidates

    candidates = _search_candidates(query, project_root=project_root, state=state)
    return {"candidates": candidates}
