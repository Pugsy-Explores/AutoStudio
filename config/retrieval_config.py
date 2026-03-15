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
