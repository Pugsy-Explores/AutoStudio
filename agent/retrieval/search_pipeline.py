"""Hybrid parallel retrieval: run BM25, graph, vector, grep; merge via RRF or concat."""

import concurrent.futures
import logging
import os

from agent.memory.state import AgentState
from config.retrieval_config import (
    ENABLE_BM25_SEARCH,
    ENABLE_GRAPH_LOOKUP,
    ENABLE_GREP_SEARCH,
    ENABLE_RRF_FUSION,
    ENABLE_VECTOR_SEARCH,
    MAX_SEARCH_RESULTS,
    RRF_TOP_N,
)

logger = logging.getLogger(__name__)


def _merge_results(
    graph_out: dict,
    vector_out: dict | None,
    grep_out: dict,
    bm25_out: dict | None = None,
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

    for out in (graph_out, vector_out, grep_out, bm25_out):
        if not out or not isinstance(out, dict):
            continue
        for r in out.get("results") or []:
            add(r)
            if len(merged) >= MAX_SEARCH_RESULTS:
                break
        if len(merged) >= MAX_SEARCH_RESULTS:
            break

    result = merged[:MAX_SEARCH_RESULTS]
    logger.info("[hybrid_retrieval] merged %d results from graph/vector/grep/bm25", len(result))
    return result


def _merge_results_rrf(
    bm25_out: dict,
    graph_out: dict,
    vector_out: dict | None,
    grep_out: dict,
) -> list[dict]:
    """Merge via Reciprocal Rank Fusion; return up to RRF_TOP_N."""
    from agent.retrieval.rank_fusion import reciprocal_rank_fusion  # noqa: PLC0415

    lists = []
    for out in (bm25_out, graph_out, vector_out, grep_out):
        if out and isinstance(out, dict):
            results = out.get("results") or []
            if results:
                lists.append(results)
    if not lists:
        return []
    merged = reciprocal_rank_fusion(lists, top_n=RRF_TOP_N)
    result = merged[:MAX_SEARCH_RESULTS]
    logger.info("[hybrid_retrieval] RRF merged %d lists -> %d results", len(lists), len(result))
    return result


def hybrid_retrieve(query: str, state: AgentState) -> dict:
    """
    Run BM25, graph, vector, grep in parallel; merge via RRF or concat; return top MAX_SEARCH_RESULTS.
    Returns {results: [...], query}.
    """
    if not query or not query.strip():
        return {"results": [], "query": query or ""}

    project_root = state.context.get("project_root") or os.environ.get("SERENA_PROJECT_DIR") or os.getcwd()

    bm25_out: dict = {"results": []}
    graph_out: dict = {"results": []}
    vector_out: dict | None = None
    grep_out: dict = {"results": []}

    def run_bm25():
        if not ENABLE_BM25_SEARCH:
            return {"results": []}
        try:
            from agent.retrieval.bm25_retriever import search_bm25  # noqa: PLC0415
            from config.retrieval_config import BM25_TOP_K  # noqa: PLC0415

            results = search_bm25(query, project_root, top_k=BM25_TOP_K)
            return {"results": results}
        except Exception as e:
            logger.debug("[hybrid_retrieval] bm25 failed: %s", e)
            return {"results": []}

    def run_graph():
        if not ENABLE_GRAPH_LOOKUP:
            return {"results": []}
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
        if not ENABLE_GREP_SEARCH:
            return {"results": []}
        try:
            from agent.tools import search_code
            out = search_code(query, tool_hint="search_for_pattern")
            return out or {"results": []}
        except Exception as e:
            logger.debug("[hybrid_retrieval] grep failed: %s", e)
            return {"results": []}

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        f_bm25 = ex.submit(run_bm25)
        f_graph = ex.submit(run_graph)
        f_vector = ex.submit(run_vector)
        f_grep = ex.submit(run_grep)
        bm25_out = f_bm25.result()
        graph_out = f_graph.result()
        vector_out = f_vector.result()
        grep_out = f_grep.result()

    if ENABLE_RRF_FUSION and (bm25_out.get("results") or graph_out.get("results") or (vector_out and vector_out.get("results")) or grep_out.get("results")):
        results = _merge_results_rrf(bm25_out, graph_out, vector_out, grep_out)
    else:
        results = _merge_results(graph_out, vector_out, grep_out, bm25_out)
    return {"results": results, "query": query}
