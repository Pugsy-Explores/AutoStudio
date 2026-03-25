"""HTTP client for extended retrieval daemon endpoints (vector, BM25, repo_map).

When RETRIEVAL_REMOTE_FIRST=1 (default), search paths try the daemon first and fall back
to in-process implementations if the daemon is down or returns an error.

The retrieval daemon process sets RETRIEVAL_SKIP_REMOTE=1 so it never calls itself.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request

from config.retrieval_config import RETRIEVAL_DAEMON_PORT, RETRIEVAL_REMOTE_FIRST

logger = logging.getLogger(__name__)


def remote_retrieval_enabled() -> bool:
    """True when remote-first routing is on and this process is not the daemon."""
    if os.getenv("RETRIEVAL_SKIP_REMOTE", "").lower() in ("1", "true", "yes"):
        return False
    return RETRIEVAL_REMOTE_FIRST


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
