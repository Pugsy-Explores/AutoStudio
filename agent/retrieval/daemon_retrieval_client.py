"""HTTP client for extended retrieval daemon endpoints (vector, BM25, repo_map, rerank/batch).

When RETRIEVAL_REMOTE_FIRST=1 (default), search paths try the daemon first and fall back
to in-process implementations if the daemon is down or returns an error.

When RETRIEVAL_REMOTE_RERANK_FIRST=1, cross-encoder scoring uses ``POST /rerank/batch`` (batched ONNX on daemon).

The retrieval daemon process sets RETRIEVAL_SKIP_REMOTE=1 so it never calls itself.
"""

from __future__ import annotations

import json
import logging
import os
import time as _time
import urllib.error
import urllib.request

from config.retrieval_config import (
    RETRIEVAL_DAEMON_PORT,
    RETRIEVAL_DAEMON_RERANK_BATCH_MAX_SLOTS,
    RETRIEVAL_DAEMON_VECTOR_BATCH_MAX,
    RETRIEVAL_REMOTE_FIRST,
    RETRIEVAL_REMOTE_RERANK_FIRST,
)

logger = logging.getLogger(__name__)


def remote_retrieval_enabled() -> bool:
    """True when remote-first routing is on and this process is not the daemon."""
    if os.getenv("RETRIEVAL_SKIP_REMOTE", "").lower() in ("1", "true", "yes"):
        return False
    # Read env at call time so tests (monkeypatch) and late env exports work without reimporting config.
    v = os.getenv("RETRIEVAL_REMOTE_FIRST")
    if v is not None:
        return v.lower() in ("1", "true", "yes")
    return RETRIEVAL_REMOTE_FIRST


def remote_rerank_http_enabled() -> bool:
    """True when reranking should use daemon ``POST /rerank/batch`` (and this process is not the daemon)."""
    if os.getenv("RETRIEVAL_SKIP_REMOTE", "").lower() in ("1", "true", "yes"):
        return False
    v = os.getenv("RETRIEVAL_REMOTE_RERANK_FIRST")
    if v is not None:
        return v.lower() in ("1", "true", "yes")
    return RETRIEVAL_REMOTE_RERANK_FIRST


def _post_json(path: str, body: dict, timeout: float = 60.0) -> dict | None:
    try:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{RETRIEVAL_DAEMON_PORT}{path}",
            data=data,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
        out = json.loads(raw)
        if not isinstance(out, dict):
            return None
        return out
    except urllib.error.HTTPError as e:
        logger.debug("[daemon_retrieval_client] HTTP %s: %s", path, e)
        _maybe_invalidate_health_cache()
        return None
    except Exception as e:
        logger.debug("[daemon_retrieval_client] %s failed: %s", path, e)
        _maybe_invalidate_health_cache()
        return None


def _maybe_invalidate_health_cache() -> None:
    try:
        from agent.retrieval.daemon_client import reset_health_cache

        reset_health_cache()
    except Exception:
        pass


def try_daemon_vector_search(
    query: str,
    project_root: str | None,
    top_k: int,
) -> dict | None:
    """POST /retrieve/vector. Returns dict on success, None to fall back."""
    if not remote_retrieval_enabled():
        return None
    root = project_root or os.environ.get("SERENA_PROJECT_DIR") or os.getcwd()
    data = _post_json(
        "/retrieve/vector",
        {"query": query, "project_root": root, "top_k": top_k},
        timeout=90.0,
    )
    if not data:
        return None
    if data.get("error"):
        return None
    if "results" not in data:
        return None
    return {"results": data.get("results") or [], "query": data.get("query", query)}


def _post_vector_batch_chunk(
    queries: list[str],
    project_root: str,
    top_k: int,
) -> list[dict] | None:
    """Single POST /retrieve/vector/batch for at most RETRIEVAL_DAEMON_VECTOR_BATCH_MAX queries."""
    data = None
    for attempt in range(2):
        data = _post_json(
            "/retrieve/vector/batch",
            {"queries": queries, "project_root": project_root, "top_k": top_k},
            timeout=90.0,
        )
        if data:
            break
        if attempt == 0:
            _time.sleep(0.05)
    if not data:
        return None
    if data.get("error"):
        logger.debug("[daemon_retrieval_client] batch vector error: %s", data["error"])
        return None
    results = data.get("results")
    if results is None:
        return None
    if len(results) != len(queries):
        logger.warning(
            "[daemon_retrieval_client] batch shape mismatch: got %d slots, expected %d",
            len(results),
            len(queries),
        )
        return None
    return results


def try_daemon_vector_search_batch(
    queries: list[str],
    project_root: str | None,
    top_k: int,
) -> list[dict] | None:
    """POST /retrieve/vector/batch (chunked to match daemon max). Preserves input order.

    Each element corresponds to the input query at the same index.
    Returns None if daemon is unavailable or any chunk returns error.
    """
    if not remote_retrieval_enabled():
        return None
    if not queries:
        return []
    root = project_root or os.environ.get("SERENA_PROJECT_DIR") or os.getcwd()
    max_chunk = max(1, RETRIEVAL_DAEMON_VECTOR_BATCH_MAX)
    combined: list[dict] = []
    for i in range(0, len(queries), max_chunk):
        chunk = queries[i : i + max_chunk]
        part = _post_vector_batch_chunk(chunk, root, top_k)
        if part is None:
            return None
        combined.extend(part)
    return combined


def try_daemon_bm25_search(
    query: str,
    project_root: str | None,
    top_k: int,
) -> list | None:
    """POST /retrieve/bm25. Returns list on success, None to fall back."""
    if not remote_retrieval_enabled():
        return None
    root = project_root or os.environ.get("SERENA_PROJECT_DIR") or os.getcwd()
    data = _post_json(
        "/retrieve/bm25",
        {"query": query, "project_root": root, "top_k": top_k},
        timeout=60.0,
    )
    if not data:
        return None
    if data.get("error"):
        return None
    if "results" not in data:
        return None
    return data["results"]


def _post_rerank_batch_chunk(
    requests: list[tuple[str, list[str]]],
) -> list[list[tuple[str, float]]] | None:
    """Single POST /rerank/batch for at most RETRIEVAL_DAEMON_RERANK_BATCH_MAX_SLOTS (query, docs) slots."""
    body = {
        "requests": [{"query": q, "docs": docs} for q, docs in requests],
    }
    data = None
    for attempt in range(2):
        data = _post_json("/rerank/batch", body, timeout=180.0)
        if data:
            break
        if attempt == 0:
            _time.sleep(0.05)
    if not data:
        return None
    if data.get("error"):
        logger.debug("[daemon_retrieval_client] rerank/batch error: %s", data["error"])
        return None
    raw = data.get("results")
    if raw is None:
        return None
    if len(raw) != len(requests):
        logger.warning(
            "[daemon_retrieval_client] rerank batch shape mismatch: got %d slots, expected %d",
            len(raw),
            len(requests),
        )
        return None
    out: list[list[tuple[str, float]]] = []
    for slot in raw:
        if not isinstance(slot, list):
            return None
        parsed: list[tuple[str, float]] = []
        for pair in slot:
            if not isinstance(pair, (list, tuple)) or len(pair) < 2:
                return None
            parsed.append((str(pair[0]), float(pair[1])))
        out.append(parsed)
    return out


def try_daemon_rerank_batch(
    requests: list[tuple[str, list[str]]],
) -> list[list[tuple[str, float]]] | None:
    """POST /rerank/batch (chunked). One daemon call runs ``MiniLMReranker.rerank_batch`` (batched ONNX).

    Returns None if remote rerank is disabled, daemon is down, or any chunk fails.
    """
    if not remote_rerank_http_enabled():
        return None
    if not requests:
        return []
    max_slots = max(1, RETRIEVAL_DAEMON_RERANK_BATCH_MAX_SLOTS)
    combined: list[list[tuple[str, float]]] = []
    for i in range(0, len(requests), max_slots):
        chunk = requests[i : i + max_slots]
        part = _post_rerank_batch_chunk(chunk)
        if part is None:
            return None
        combined.extend(part)
    return combined


def try_daemon_repo_map_lookup(query: str, project_root: str | None) -> list | None:
    """POST /retrieve/repo_map. Returns list on success, None to fall back."""
    if not remote_retrieval_enabled():
        return None
    root = project_root or os.environ.get("SERENA_PROJECT_DIR") or os.getcwd()
    data = _post_json(
        "/retrieve/repo_map",
        {"query": query, "project_root": root},
        timeout=30.0,
    )
    if not data:
        return None
    if data.get("error"):
        return None
    if "results" not in data:
        return None
    return data["results"]
