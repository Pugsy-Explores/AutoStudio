"""Embedding-based code search. Uses ChromaDB + sentence-transformers for semantic queries.

When EMBEDDING_USE_DAEMON=1 and retrieval daemon is reachable, uses daemon /embed
(via agent.retrieval.daemon_embed) instead of loading SentenceTransformer in-process.
"""

import logging
import os
from pathlib import Path
from collections.abc import Callable
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
    from agent.retrieval.chroma_utils import try_persistent_chroma_client  # noqa: PLC0415

    root = Path(project_root or ".").resolve()
    path = (root / EMBEDDINGS_DIR / EMBEDDINGS_SUBDIR).resolve()
    key = str(path)
    if key not in _chroma_clients:
        client = try_persistent_chroma_client(path)
        if client is None:
            return None
        _chroma_clients[key] = client
    return _chroma_clients[key]


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


def vector_search_with_embedder(
    query: str,
    project_root: str | None,
    top_k: int,
    embed_fn: Callable[[str], list[float] | None],
) -> dict | None:
    """
    Chroma query using a caller-supplied embedder (daemon ST model or local).
    Returns {results, query} or None when Chroma unavailable or query fails.
    """
    if not query or not query.strip():
        return {"results": [], "query": query}

    if not _check_vector_available():
        return None

    root = Path(project_root or os.environ.get("SERENA_PROJECT_DIR", os.getcwd())).resolve()
    client = _get_client(str(root))
    if not client:
        return None

    try:
        coll = client.get_collection(COLLECTION_NAME)
    except Exception:
        logger.debug("[vector_retriever] collection not found")
        return None

    try:
        q_emb = embed_fn(query.strip())
        if q_emb is None:
            return None
        results_raw = coll.query(query_embeddings=[q_emb], n_results=min(top_k, 20))
    except BaseException as e:  # noqa: BLE001 - harden against rust panics
        logger.warning("[vector_retriever] query failed; returning no vector hits: %s", e)
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


def search_by_embedding(
    query: str,
    project_root: str | None = None,
    top_k: int = DEFAULT_TOP_K,
) -> dict | None:
    """
    Semantic code search via embeddings.
    Returns {results: [{file, symbol, line, snippet}], query} or None when unavailable.

    Order: (1) full vector search via retrieval daemon HTTP when RETRIEVAL_REMOTE_FIRST
    and daemon up; (2) local Chroma + daemon /embed or in-process SentenceTransformer.
    """
    if not query or not query.strip():
        return {"results": [], "query": query}

    from agent.retrieval.daemon_retrieval_client import remote_retrieval_enabled, try_daemon_vector_search

    if remote_retrieval_enabled():
        remote = try_daemon_vector_search(query, project_root, top_k)
        if remote is not None:
            return remote
        logger.warning(
            "[vector_retriever] [CHROMA FALLBACK BLOCKED] "
            "daemon returned None but RETRIEVAL_REMOTE_FIRST=1. "
            "Returning empty results. Check daemon health."
        )
        return {"results": [], "query": query, "error": "retrieval daemon unavailable"}

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

    def _embed(q: str) -> list[float] | None:
        if use_daemon:
            emb_list = encode_via_daemon([q])
            return emb_list[0] if emb_list else None
        assert model is not None
        return model.encode(q).tolist()

    return vector_search_with_embedder(query, str(root), top_k, _embed)


def search_batch(
    queries: list[str],
    project_root: str | None = None,
    top_k: int = DEFAULT_TOP_K,
) -> list[dict | None]:
    """Batch semantic search. Returns list of {results, query} per query.

    Prefers a single POST /retrieve/vector/batch when remote-first; falls back to
    local Chroma + batched daemon embed, then sequential search_by_embedding.
    """
    if not queries:
        return []

    from agent.retrieval.daemon_retrieval_client import (
        remote_retrieval_enabled,
        try_daemon_vector_search_batch,
    )

    if remote_retrieval_enabled():
        batch_results = try_daemon_vector_search_batch(queries, project_root, top_k)
        if batch_results is not None:
            if not isinstance(batch_results, list) or len(batch_results) != len(queries):
                logger.warning(
                    "[vector_retriever] search_batch: invalid batch response "
                    "shape=%s expected=%s — falling back",
                    type(batch_results).__name__
                    if not isinstance(batch_results, list)
                    else len(batch_results),
                    len(queries),
                )
            else:
                out: list[dict | None] = []
                for item in batch_results:
                    if isinstance(item, dict) and "results" in item:
                        out.append(
                            {"results": item["results"], "query": item.get("query", "")}
                        )
                    else:
                        out.append(None)
                return out

    if _check_vector_available() and daemon_embed_available():
        root = Path(project_root or os.environ.get("SERENA_PROJECT_DIR", os.getcwd())).resolve()
        client = _get_client(str(root))
        if client:
            try:
                coll = client.get_collection(COLLECTION_NAME)
                valid_queries = [q.strip() for q in queries if q and q.strip()]
                if valid_queries:
                    embeddings = encode_via_daemon(valid_queries)
                    if embeddings:
                        raw = coll.query(
                            query_embeddings=embeddings,
                            n_results=min(top_k, 20),
                        )
                        out = []
                        docs_list = raw.get("documents", [])
                        metas_list = raw.get("metadatas", [])
                        for i, q in enumerate(valid_queries):
                            docs = docs_list[i] if i < len(docs_list) else []
                            metas = metas_list[i] if i < len(metas_list) else []
                            hits = []
                            for doc, meta in zip(docs, metas or []):
                                meta = meta or {}
                                path = normalize_file_path(meta.get("path", ""))
                                if path:
                                    hits.append({
                                        "file": path,
                                        "symbol": meta.get("symbol", ""),
                                        "line": meta.get("line", 0),
                                        "snippet": (doc or "")[:500],
                                    })
                            out.append({"results": hits, "query": q})
                        return out
            except Exception as e:
                logger.debug(
                    "[vector_retriever] batch local path failed, falling back: %s",
                    e,
                )

    logger.debug(
        "[vector_retriever] search_batch using sequential fallback for %d queries",
        len(queries),
    )
    if remote_retrieval_enabled():
        # Batch HTTP already failed; do not issue N× POST /retrieve/vector (single-query mode).
        logger.warning(
            "[vector_retriever] search_batch: remote batch unavailable — returning empty per query "
            "(no single-query daemon fallback)"
        )
        return [
            {"results": [], "query": q, "error": "retrieval daemon unavailable"}
            for q in queries
        ]
    return [search_by_embedding(q, project_root, top_k) for q in queries]
