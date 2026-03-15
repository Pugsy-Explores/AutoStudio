"""Hybrid parallel retrieval: run graph, vector, grep simultaneously; merge and rank."""

import concurrent.futures
import logging
import os

from agent.memory.state import AgentState
from config.retrieval_config import (
    ENABLE_HYBRID_RETRIEVAL,
    ENABLE_VECTOR_SEARCH,
    MAX_SEARCH_RESULTS,
)

logger = logging.getLogger(__name__)


def _merge_results(
    graph_out: dict,
    vector_out: dict | None,
    grep_out: dict,
) -> list[dict]:
    """Dedupe by (file, symbol, line); concatenate; return up to MAX_SEARCH_RESULTS."""
    seen: set[tuple[str, str, int]] = set()
    merged: list[dict] = []

    def add(r: dict) -> None:
        if not r or not isinstance(r, dict):
            return
        file_path = (r.get("file") or r.get("path") or "").strip()
        symbol = (r.get("symbol") or "").strip()
        line = r.get("line")
        line_int = int(line) if line is not None and isinstance(line, (int, float)) else 0
        key = (file_path, symbol, line_int)
        if key in seen:
            return
        seen.add(key)
        merged.append(r)

    for out in (graph_out, vector_out, grep_out):
        if not out or not isinstance(out, dict):
            continue
        for r in out.get("results") or []:
            add(r)
            if len(merged) >= MAX_SEARCH_RESULTS:
                break
        if len(merged) >= MAX_SEARCH_RESULTS:
            break

    result = merged[:MAX_SEARCH_RESULTS]
    logger.info("[hybrid_retrieval] merged %d results from graph/vector/grep", len(result))
    return result


def hybrid_retrieve(query: str, state: AgentState) -> dict:
    """
    Run graph, vector, grep in parallel; merge and return top MAX_SEARCH_RESULTS.
    Returns {results: [...], query}.
    """
    if not query or not query.strip():
        return {"results": [], "query": query or ""}

    project_root = state.context.get("project_root") or os.environ.get("SERENA_PROJECT_DIR") or os.getcwd()

    graph_out: dict = {"results": []}
    vector_out: dict | None = None
    grep_out: dict = {"results": []}

    def run_graph():
        try:
            from agent.retrieval.graph_retriever import retrieve_symbol_context
            # Use repo_map anchor when available (confidence >= 0.9)
            anchor = state.context.get("repo_map_anchor")
            graph_query = query
            if anchor and isinstance(anchor, dict):
                sym = anchor.get("symbol")
                conf = anchor.get("confidence", 0)
                if sym and conf >= 0.9:
                    graph_query = sym
            out = retrieve_symbol_context(graph_query, project_root)
            return out or {"results": []}
        except Exception as e:
            logger.debug("[hybrid_retrieval] graph failed: %s", e)
            return {"results": []}

    def run_vector():
        if not ENABLE_VECTOR_SEARCH:
            return {"results": []}
        try:
            from agent.retrieval.vector_retriever import search_by_embedding
            out = search_by_embedding(query, project_root, top_k=10)
            return out or {"results": []}
        except Exception as e:
            logger.debug("[hybrid_retrieval] vector failed: %s", e)
            return {"results": []}

    def run_grep():
        try:
            from agent.tools import search_code
            out = search_code(query, tool_hint="search_for_pattern")
            return out or {"results": []}
        except Exception as e:
            logger.debug("[hybrid_retrieval] grep failed: %s", e)
            return {"results": []}

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        f_graph = ex.submit(run_graph)
        f_vector = ex.submit(run_vector)
        f_grep = ex.submit(run_grep)
        graph_out = f_graph.result()
        vector_out = f_vector.result()
        grep_out = f_grep.result()

    results = _merge_results(graph_out, vector_out, grep_out)
    return {"results": results, "query": query}
