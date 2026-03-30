"""Retrieval: query rewriter, context builder, retrieval expander, anchor detector, retrieval pipeline."""

from agent.retrieval.anchor_detector import detect_anchor, detect_anchors
from agent.retrieval.context_builder import build_context, build_context_from_symbols
from agent.retrieval.context_pruner import prune_context
from agent.retrieval.context_ranker import rank_context
from agent.retrieval.query_rewriter import (
    SearchAttempt,
    heuristic_condense_for_retrieval,
    rewrite_query,
    rewrite_query_with_context,
)
from agent.retrieval.retrieval_cache import clear_cache, get_cached, set_cached
from agent.retrieval.retrieval_expander import expand_search_results
from agent.retrieval.retrieval_pipeline import run_retrieval_pipeline
from agent.retrieval.symbol_graph import get_symbol_dependencies
from agent.retrieval.vector_retriever import search_batch, search_by_embedding

__all__ = [
    "heuristic_condense_for_retrieval",
    "rewrite_query",
    "rewrite_query_with_context",
    "SearchAttempt",
    "build_context",
    "build_context_from_symbols",
    "expand_search_results",
    "detect_anchor",
    "detect_anchors",
    "run_retrieval_pipeline",
    "rank_context",
    "prune_context",
    "get_symbol_dependencies",
    "search_by_embedding",
    "search_batch",
    "get_cached",
    "set_cached",
    "clear_cache",
]
