"""SEARCH_CANDIDATES tool: candidate discovery only (BM25, vector, repo_map, grep). No graph expansion or context building."""

import logging
import os

from agent.memory.state import AgentState

logger = logging.getLogger(__name__)


def search_candidates(query: str, state: AgentState | None = None) -> dict:
    """
    Perform candidate discovery only. Returns top 20 candidates.
    Allowed: BM25, vector, repo_map, grep.
    NOT allowed: graph expansion, symbol body read, LLM ranking, context building.
    """
    if not query or not query.strip():
        return {"candidates": []}

    from agent.retrieval.retrieval_pipeline import search_candidates as _search_candidates

    project_root = None
    if state and state.context:
        project_root = state.context.get("project_root")
    if not project_root:
        project_root = os.environ.get("SERENA_PROJECT_DIR") or os.getcwd()

    candidates = _search_candidates(query, project_root=project_root, state=state)
    return {"candidates": candidates}
