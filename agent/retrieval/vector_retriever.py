"""Embedding-based code search. Uses ChromaDB + sentence-transformers for semantic queries.

When EMBEDDING_USE_DAEMON=1 and retrieval daemon is reachable, uses daemon /embed
(via agent.retrieval.daemon_embed) instead of loading SentenceTransformer in-process.
"""

import logging
import os
from pathlib import Path
from typing import Any

from agent.retrieval.daemon_embed import daemon_embed_available, encode_via_daemon
from agent.retrieval.retrieval_expander import normalize_file_path

logger = logging.getLogger(__name__)

EMBEDDINGS_DIR = ".symbol_graph"
EMBEDDINGS_SUBDIR = "embeddings"
COLLECTION_NAME = "codebase"
EMB_MODEL = "all-MiniLM-L6-v2"
DEFAULT_TOP_K = 5

_VECTOR_AVAILABLE: bool | None = None
_model = None
# Chroma PersistentClient must be scoped per workspace: a singleton ignores later project_root
# and returns hits whose file metadata points at the first indexed tree, so filter_and_rank_search_results
# drops every path under a different root (search_viable_raw_hits > 0, search_viable_file_hits == 0).
_chroma_clients: dict[str, Any] = {}


def reset_chroma_clients_for_tests() -> None:
    """Drop cached Chroma clients (e.g. multiple workspaces in one process, agent_eval A/B)."""
    global _chroma_clients
    _chroma_clients.clear()


def _check_vector_available() -> bool:
    """Lazy check if ChromaDB and sentence-transformers are available."""
    global _VECTOR_AVAILABLE
    if _VECTOR_AVAILABLE is not None:
        return _VECTOR_AVAILABLE
    try:
        import chromadb  # noqa: F401
        from sentence_transformers import SentenceTransformer  # noqa: F401
        _VECTOR_AVAILABLE = True
    except ImportError:
        _VECTOR_AVAILABLE = False
    except RecursionError:
        logger.warning(
            "[vector_retriever] chromadb/sentence_transformers import failed with RecursionError; "
            "vector search unavailable"
        )
        _VECTOR_AVAILABLE = False
    return _VECTOR_AVAILABLE


def _get_client(project_root: str):
    """Get or create persistent ChromaDB client for this workspace's embeddings directory."""
    global _chroma_clients
    try:
        import chromadb

        root = Path(project_root or ".").resolve()
        path = (root / EMBEDDINGS_DIR / EMBEDDINGS_SUBDIR).resolve()
        path.mkdir(parents=True, exist_ok=True)
        key = str(path)
        if key not in _chroma_clients:
            _chroma_clients[key] = chromadb.PersistentClient(path=key)
        return _chroma_clients[key]
    except Exception as e:
        logger.debug("[vector_retriever] client init failed: %s", e)
        return None


def _get_model():
    """Lazy-load sentence-transformers model."""
    global _model
    if _model is not None:
        return _model
    try:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(EMB_MODEL)
        return _model
    except Exception as e:
        logger.debug("[vector_retriever] model load failed: %s", e)
        return None


def search_by_embedding(
    query: str,
    project_root: str | None = None,
    top_k: int = DEFAULT_TOP_K,
) -> dict | None:
    """
    Semantic code search via embeddings.
    Returns {results: [{file, symbol, line, snippet}], query} or None when unavailable.
    """
    if not query or not query.strip():
        return {"results": [], "query": query}

    if not _check_vector_available():
        return None

    root = Path(project_root or os.environ.get("SERENA_PROJECT_DIR", os.getcwd())).resolve()
    client = _get_client(str(root))
    if not client:
        return None

    use_daemon = daemon_embed_available()

    model = _get_model() if not use_daemon else None
    if not use_daemon and not model:
        return None

    try:
        coll = client.get_collection(COLLECTION_NAME)
    except Exception:
        logger.debug("[vector_retriever] collection not found")
        return None

    try:
        if use_daemon:
            emb_list = encode_via_daemon([query.strip()])
            q_emb = emb_list[0] if emb_list else None
        else:
            q_emb = model.encode(query.strip()).tolist()

        if q_emb is None:
            return None
        results_raw = coll.query(query_embeddings=[q_emb], n_results=min(top_k, 20))
    except Exception as e:
        logger.debug("[vector_retriever] query failed: %s", e)
        return None

    documents = results_raw.get("documents", [[]])
    metadatas = results_raw.get("metadatas", [[]])
    if not documents or not documents[0]:
        return {"results": [], "query": query}

    results = []
    for doc, meta in zip(documents[0], metadatas[0] if metadatas else []):
        meta = meta or {}
        path = normalize_file_path(meta.get("path", ""))
        if not path:
            continue
        symbol = meta.get("symbol", "")
        line = meta.get("line", 0)
        snippet = (doc or "")[:500] if isinstance(doc, str) else str(doc)[:500]
        results.append({
            "file": path,
            "symbol": symbol,
            "line": line,
            "snippet": snippet,
        })

    logger.info("[vector_retriever] results=%d", len(results))
    return {"results": results, "query": query}


def search_batch(
    queries: list[str],
    project_root: str | None = None,
    top_k: int = DEFAULT_TOP_K,
) -> list[dict | None]:
    """
    Batch semantic search. Returns list of {results, query} per query.
    """
    out = []
    for q in queries:
        out.append(search_by_embedding(q, project_root, top_k))
    return out
