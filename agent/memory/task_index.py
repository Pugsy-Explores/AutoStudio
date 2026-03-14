"""Vector index for past tasks: semantic search over task history."""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

AGENT_MEMORY_DIR = ".agent_memory"
TASK_INDEX_DIR = "task_index"
COLLECTION_NAME = "past_tasks"
EMB_MODEL = "all-MiniLM-L6-v2"

_client = None
_model = None


def _check_available() -> bool:
    try:
        import chromadb
        from sentence_transformers import SentenceTransformer
        return True
    except ImportError:
        return False


def _get_client(project_root: str | None = None):
    global _client
    if _client is not None:
        return _client
    if not _check_available():
        return None
    try:
        import chromadb

        root = Path(project_root or ".").resolve()
        path = root / AGENT_MEMORY_DIR / TASK_INDEX_DIR
        path.mkdir(parents=True, exist_ok=True)
        _client = chromadb.PersistentClient(path=str(path))
        return _client
    except Exception as e:
        logger.debug("[task_index] client init failed: %s", e)
        return None


def _get_model():
    global _model
    if _model is not None:
        return _model
    try:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(EMB_MODEL)
        return _model
    except Exception as e:
        logger.debug("[task_index] model load failed: %s", e)
        return None


def index_task(
    task_id: str,
    instruction: str,
    files_modified: list | None = None,
    errors: list | None = None,
    project_root: str | None = None,
) -> bool:
    """Embed and store task for semantic search. Returns True on success."""
    if not _check_available():
        return False
    client = _get_client(project_root)
    model = _get_model()
    if not client or not model:
        return False

    files_str = ", ".join(files_modified or [])[:500]
    errors_str = ", ".join(str(e) for e in (errors or []))[:500]
    doc = f"instruction: {instruction[:1000]}\nfiles: {files_str}\nerrors: {errors_str}"

    try:
        coll = client.get_or_create_collection(COLLECTION_NAME)
        emb = model.encode(doc).tolist()
        coll.add(
            ids=[task_id],
            documents=[doc],
            embeddings=[emb],
            metadatas=[{"instruction": instruction[:500], "task_id": task_id}],
        )
        logger.info("[task_index] task indexed")
        return True
    except Exception as e:
        logger.debug("[task_index] index failed: %s", e)
        return False


def search_similar_tasks(
    query: str,
    project_root: str | None = None,
    top_k: int = 3,
) -> list[dict]:
    """Return past tasks relevant to query. Each dict has task_id, instruction, metadata."""
    if not query or not query.strip():
        return []
    if not _check_available():
        return []
    client = _get_client(project_root)
    model = _get_model()
    if not client or not model:
        return []

    try:
        coll = client.get_collection(COLLECTION_NAME)
    except Exception:
        return []

    try:
        q_emb = model.encode(query.strip()).tolist()
        results = coll.query(query_embeddings=[q_emb], n_results=min(top_k, 10))
    except Exception as e:
        logger.debug("[task_index] search failed: %s", e)
        return []

    out = []
    ids = results.get("ids", [[]])
    metadatas = results.get("metadatas", [[]])
    documents = results.get("documents", [[]])
    for i, tid in enumerate(ids[0] if ids else []):
        meta = metadatas[0][i] if metadatas and metadatas[0] else {}
        doc = documents[0][i] if documents and documents[0] else ""
        out.append({
            "task_id": tid,
            "instruction": meta.get("instruction", ""),
            "document": doc[:300] if doc else "",
        })
    return out
