"""Retrieval pipeline configuration."""

import os


def _bool_env(name: str, default: str) -> bool:
    return os.getenv(name, default).lower() in ("1", "true", "yes")


MAX_CONTEXT_SNIPPETS = int(os.getenv("MAX_CONTEXT_SNIPPETS", "6"))
DEFAULT_MAX_SNIPPETS = int(os.getenv("DEFAULT_MAX_SNIPPETS", "6"))
DEFAULT_MAX_CHARS = int(os.getenv("DEFAULT_MAX_CHARS", "8000"))
DEFAULT_MAX_CONTEXT_CHARS = int(os.getenv("DEFAULT_MAX_CONTEXT_CHARS", "16000"))

MAX_SEARCH_RESULTS = int(os.getenv("MAX_SEARCH_RESULTS", "20"))
MAX_SYMBOL_EXPANSION = int(os.getenv("MAX_SYMBOL_EXPANSION", "10"))
GRAPH_EXPANSION_DEPTH = int(os.getenv("GRAPH_EXPANSION_DEPTH", "2"))

ENABLE_HYBRID_RETRIEVAL = _bool_env("ENABLE_HYBRID_RETRIEVAL", "1")
ENABLE_VECTOR_SEARCH = _bool_env("ENABLE_VECTOR_SEARCH", "1")
ENABLE_CONTEXT_RANKING = _bool_env("ENABLE_CONTEXT_RANKING", "1")

RETRIEVAL_CACHE_SIZE = int(os.getenv("RETRIEVAL_CACHE_SIZE", "100"))

MAX_CANDIDATES_FOR_RANKING = int(os.getenv("MAX_CANDIDATES_FOR_RANKING", "20"))
MAX_SNIPPET_CHARS_IN_BATCH = int(os.getenv("MAX_SNIPPET_CHARS_IN_BATCH", "400"))

FALLBACK_TOP_N = int(os.getenv("FALLBACK_TOP_N", "3"))

MAX_SYMBOLS = int(os.getenv("MAX_SYMBOLS", "15"))
MAX_RETRIEVED_SYMBOLS = int(os.getenv("MAX_RETRIEVED_SYMBOLS", "15"))

# Graph dependency expansion (retrieval precision)
RETRIEVAL_GRAPH_EXPANSION_DEPTH = int(os.getenv("RETRIEVAL_GRAPH_EXPANSION_DEPTH", "2"))
RETRIEVAL_GRAPH_MAX_NODES = int(os.getenv("RETRIEVAL_GRAPH_MAX_NODES", "20"))
RETRIEVAL_MAX_SYMBOL_EXPANSIONS = int(os.getenv("RETRIEVAL_MAX_SYMBOL_EXPANSIONS", "8"))

# Phase 10.5 — Graph-Guided Localization
MAX_GRAPH_DEPTH = int(os.getenv("MAX_GRAPH_DEPTH", "3"))
MAX_DEPENDENCY_NODES = int(os.getenv("MAX_DEPENDENCY_NODES", "100"))
MAX_EXECUTION_PATHS = int(os.getenv("MAX_EXECUTION_PATHS", "10"))
ENABLE_LOCALIZATION_ENGINE = _bool_env("ENABLE_LOCALIZATION_ENGINE", "1")

# --- Reranker core ---
RERANKER_ENABLED = _bool_env("RERANKER_ENABLED", "1")
RERANKER_DEVICE = os.getenv("RERANKER_DEVICE", "auto")  # auto | cpu | gpu
RERANKER_TOP_K = int(os.getenv("RERANKER_TOP_K", "10"))
RERANKER_BATCH_SIZE = int(os.getenv("RERANKER_BATCH_SIZE", "16"))
RERANKER_GPU_MODEL = os.getenv("RERANKER_GPU_MODEL", "Qwen/Qwen3-Reranker-0.6B")
RERANKER_CPU_MODEL = os.getenv("RERANKER_CPU_MODEL", "models/reranker/qwen3_reranker_int8.onnx")

# --- Alternate models (registry) ---
RERANKER_ALTERNATE_MODELS = [
    "BAAI/bge-reranker-v2-gemma",
    "jinaai/jina-reranker-v3",
    "Qwen/Qwen3-Reranker-0.6B",  # default
]

# --- Preprocessing ---
MAX_RERANK_SNIPPET_TOKENS = int(os.getenv("MAX_RERANK_SNIPPET_TOKENS", "256"))
MAX_RERANK_PAIR_TOKENS = int(os.getenv("MAX_RERANK_PAIR_TOKENS", "512"))

# --- Adaptive gating ---
RERANK_MIN_CANDIDATES = int(os.getenv("RERANK_MIN_CANDIDATES", "6"))
MAX_RERANK_CANDIDATES = int(os.getenv("MAX_RERANK_CANDIDATES", "50"))

# --- Cache ---
RERANK_CACHE_SIZE = int(os.getenv("RERANK_CACHE_SIZE", "2048"))

# --- Score fusion ---
SCORE_FUSION_RERANKER_WEIGHT = float(os.getenv("SCORE_FUSION_RERANKER_WEIGHT", "0.8"))
SCORE_FUSION_RETRIEVER_WEIGHT = float(os.getenv("SCORE_FUSION_RETRIEVER_WEIGHT", "0.2"))
RERANK_FUSION_WEIGHT = float(os.getenv("RERANK_FUSION_WEIGHT", "0.8"))
RETRIEVER_FUSION_WEIGHT = float(os.getenv("RETRIEVER_FUSION_WEIGHT", "0.2"))

# --- Reranker threshold ---
RERANK_SCORE_THRESHOLD = float(os.getenv("RERANK_SCORE_THRESHOLD", "0.15"))
RERANK_MIN_RESULTS_AFTER_THRESHOLD = int(os.getenv("RERANK_MIN_RESULTS_AFTER_THRESHOLD", "3"))

# --- BM25 ---
ENABLE_BM25_SEARCH = _bool_env("ENABLE_BM25_SEARCH", "1")
BM25_TOP_K = int(os.getenv("BM25_TOP_K", "30"))

# --- Rank fusion (RRF) ---
ENABLE_RRF_FUSION = _bool_env("ENABLE_RRF_FUSION", "1")
RRF_TOP_N = int(os.getenv("RRF_TOP_N", "100"))
RRF_K = int(os.getenv("RRF_K", "60"))

# --- Reranker batching ---
RERANK_BATCH_WINDOW_MS = int(os.getenv("RERANK_BATCH_WINDOW_MS", "5"))
