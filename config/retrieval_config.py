"""Retrieval pipeline configuration."""

import json
import os
from pathlib import Path


def _reranker_from_models_config(key: str) -> str:
    """Reranker model paths from models_config.json; env vars override."""
    defaults = {
        "gpu_model": "Qwen/Qwen3-Reranker-0.6B",
        "cpu_model": "models/reranker/model.onnx",
        "cpu_tokenizer": "models/reranker",
    }
    config_path = Path(__file__).resolve().parent.parent / "agent" / "models" / "models_config.json"
    if config_path.is_file():
        try:
            with open(config_path, encoding="utf-8") as f:
                data = json.load(f)
            r = data.get("reranker") or {}
            return str(r.get(key, defaults[key])).strip() or defaults[key]
        except (json.JSONDecodeError, OSError):
            pass
    return defaults[key]


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

ENABLE_ANSWER_EVAL = _bool_env("ENABLE_ANSWER_EVAL", "1")
ANSWER_EVAL_SAMPLE_RATE = float(os.getenv("ANSWER_EVAL_SAMPLE_RATE", "1.0"))

# Stage 46 — repo_map typo fallback (tiers 1–3 unchanged; optional tier 4 when no hits)
ENABLE_REPO_MAP_TYPO_FALLBACK = _bool_env("ENABLE_REPO_MAP_TYPO_FALLBACK", "0")
REPO_MAP_TYPO_MAX_MATCHES = int(os.getenv("REPO_MAP_TYPO_MAX_MATCHES", "3"))

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
RERANKER_STARTUP = _bool_env("RERANKER_STARTUP", "1")  # Auto-init reranker at service startup (default ON)
RERANKER_DEVICE = os.getenv("RERANKER_DEVICE", "auto")  # auto | cpu | gpu
RERANKER_USE_INT8 = _bool_env("RERANKER_USE_INT8", "1")  # Use ONNX INT8 for both CPU and GPU (default ON)
RERANKER_DAEMON_PORT = int(os.getenv("RERANKER_DAEMON_PORT", "9004"))  # Reranker daemon HTTP port
RETRIEVAL_DAEMON_PORT = int(os.getenv("RETRIEVAL_DAEMON_PORT", "9004"))  # Unified retrieval daemon (reranker + embedding)
RERANKER_USE_DAEMON = _bool_env("RERANKER_USE_DAEMON", "1")  # Prefer daemon when reachable (default ON)
EMBEDDING_USE_DAEMON = _bool_env("EMBEDDING_USE_DAEMON", "1")  # Prefer daemon /embed when reachable (default ON)
RETRIEVAL_DAEMON_AUTO_START = _bool_env("RETRIEVAL_DAEMON_AUTO_START", "1")  # Start daemon if not running (default ON)
RETRIEVAL_DAEMON_START_TIMEOUT_SECONDS = int(os.getenv("RETRIEVAL_DAEMON_START_TIMEOUT_SECONDS", "90"))
RERANKER_TOP_K = int(os.getenv("RERANKER_TOP_K", "10"))
RERANKER_BATCH_SIZE = int(os.getenv("RERANKER_BATCH_SIZE", "16"))
RERANKER_GPU_MODEL = os.getenv("RERANKER_GPU_MODEL") or _reranker_from_models_config("gpu_model")
RERANKER_CPU_MODEL = os.getenv("RERANKER_CPU_MODEL") or _reranker_from_models_config("cpu_model")
RERANKER_CPU_TOKENIZER = os.getenv("RERANKER_CPU_TOKENIZER") or _reranker_from_models_config("cpu_tokenizer")

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

# --- Service dir filtering (default OFF — can hide code in monorepos) ---
RETRIEVAL_SERVICE_DIRS = os.getenv(
    "RETRIEVAL_SERVICE_DIRS",
    "agent,config,editing,planner,src,lib,app,services",
).strip().split(",")
RETRIEVAL_USE_SERVICE_DIRS = _bool_env("RETRIEVAL_USE_SERVICE_DIRS", "0")  # default OFF
RETRIEVAL_AUTO_DETECT_SERVICE_DIRS = _bool_env(
    "RETRIEVAL_AUTO_DETECT_SERVICE_DIRS",
    "0",
)  # detect src/, lib/, app/, services/
RETRIEVAL_TEST_DOWNWEIGHT = float(os.getenv("RETRIEVAL_TEST_DOWNWEIGHT", "0.2"))

# Kind-aware expansion: choose read_symbol_body vs read_file from candidate_kind when set (default OFF for safe rollout).
ENABLE_KIND_AWARE_EXPANSION = _bool_env("ENABLE_KIND_AWARE_EXPANSION", "0")

# --- LLM bundle selector (data-path only; default off) ---
ENABLE_LLM_BUNDLE_SELECTOR = _bool_env("ENABLE_LLM_BUNDLE_SELECTOR", "0")
MAX_SELECTOR_CANDIDATE_POOL = int(os.getenv("MAX_SELECTOR_CANDIDATE_POOL", "12"))
MIN_SELECTOR_CANDIDATE_POOL = int(os.getenv("MIN_SELECTOR_CANDIDATE_POOL", "4"))
BUNDLE_SELECTOR_MAX_KEEP = int(os.getenv("BUNDLE_SELECTOR_MAX_KEEP", "4"))
FORCE_SELECTOR_IN_EVAL = _bool_env("FORCE_SELECTOR_IN_EVAL", "0")
ENABLE_BUNDLE_SELECTION = _bool_env("ENABLE_BUNDLE_SELECTION", "0")
ENABLE_EXPLORATION = _bool_env("ENABLE_EXPLORATION", "0")
# Deep expansion budgets (region bounded read, file header join)
MAX_LINES_PER_EXPANDED_UNIT = int(os.getenv("MAX_LINES_PER_EXPANDED_UNIT", "80"))
MAX_FILE_HEADER_LINES = int(os.getenv("MAX_FILE_HEADER_LINES", "60"))
