"""Cross-encoder reranker sub-package for the AutoStudio retrieval pipeline."""

from agent.retrieval.reranker.base_reranker import BaseReranker
from agent.retrieval.reranker.cache import cache_stats
from agent.retrieval.reranker.constants import MODEL_NAME as RERANKER_MODEL_NAME
from agent.retrieval.reranker.deduplicator import deduplicate_candidates
from agent.retrieval.reranker.preprocessor import prepare_rerank_pairs
from agent.retrieval.reranker.reranker_factory import create_reranker, init_reranker
from agent.retrieval.reranker.symbol_query_detector import is_symbol_query

__all__ = [
    "BaseReranker",
    "RERANKER_MODEL_NAME",
    "cache_stats",
    "create_reranker",
    "deduplicate_candidates",
    "init_reranker",
    "is_symbol_query",
    "prepare_rerank_pairs",
]
