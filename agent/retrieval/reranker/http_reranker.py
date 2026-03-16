"""HTTP client for retrieval daemon reranker.

When RERANKER_USE_DAEMON=1 and daemon is reachable, the factory returns
this client instead of in-process reranker. Avoids cold-start in the agent process.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from agent.retrieval.reranker.base_reranker import BaseReranker
from config.retrieval_config import RETRIEVAL_DAEMON_PORT

logger = logging.getLogger(__name__)

_DAEMON_TIMEOUT = 30


def _daemon_url(path: str, port: int = RETRIEVAL_DAEMON_PORT, host: str = "127.0.0.1") -> str:
    return f"http://{host}:{port}{path}"


def _check_daemon_health(port: int = RETRIEVAL_DAEMON_PORT, host: str = "127.0.0.1") -> bool:
    """Return True if daemon /health returns 200 and reranker_loaded."""
    try:
        req = urllib.request.Request(_daemon_url("/health", port, host), method="GET")
        with urllib.request.urlopen(req, timeout=2) as resp:
            if resp.status != 200:
                return False
            data = json.loads(resp.read().decode())
            return data.get("reranker_loaded", False)
    except Exception as e:
        logger.debug("[http_reranker] health check failed: %s", e)
        return False


def _sanitize_rerank_payload(query: str, docs: list[str]) -> tuple[str, list[str]]:
    """Ensure query and docs are valid for RerankRequest (query: str, docs: list[str]).
    Coerces None to empty string; ensures all docs are strings. Prevents 422 validation errors.
    """
    q = query if query is not None else ""
    if not isinstance(q, str):
        q = str(q) if q is not None else ""
    sanitized_docs: list[str] = []
    for d in docs:
        if d is None:
            sanitized_docs.append("")
        elif isinstance(d, str):
            sanitized_docs.append(d)
        else:
            sanitized_docs.append(str(d))
    return q, sanitized_docs


class HttpRerankerClient(BaseReranker):
    """Reranker that delegates to retrieval daemon via HTTP."""

    def __init__(self, port: int = RETRIEVAL_DAEMON_PORT, host: str = "127.0.0.1") -> None:
        self.port = port
        self.host = host
        self._url = _daemon_url("/rerank", port, host)

    def _score_pairs(self, pairs: list[tuple[str, str]]) -> list[float]:
        """Score pairs by calling daemon /rerank. Groups by query for batch requests."""
        if not pairs:
            return []
        from collections import defaultdict

        by_query: dict[str, list[tuple[int, str]]] = defaultdict(list)
        for i, (q, s) in enumerate(pairs):
            by_query[q].append((i, s))

        scores = [0.0] * len(pairs)
        for query, items in by_query.items():
            indices = [i for i, _ in items]
            docs = [s for _, s in items]
            query_safe, docs_safe = _sanitize_rerank_payload(query, docs)
            try:
                payload = {"query": query_safe, "docs": docs_safe}
                body = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(
                    self._url,
                    data=body,
                    method="POST",
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=_DAEMON_TIMEOUT) as resp:
                    data = json.loads(resp.read().decode())
                results = data.get("results", [])
                if data.get("error"):
                    logger.warning("[http_reranker] daemon error: %s", data["error"])
                for (idx, _), (_, score) in zip(items, results):
                    scores[idx] = float(score)
            except urllib.error.HTTPError as e:
                body_str = ""
                try:
                    body_str = e.read().decode("utf-8", errors="replace")
                except Exception:
                    pass
                logger.warning(
                    "[http_reranker] request failed for query %r: HTTP %s — %s. Body: %s",
                    query[:50] if query else "",
                    e.code,
                    e.reason,
                    body_str[:500] if body_str else "(empty)",
                )
                # Leave scores at 0.0 for failed batch
            except Exception as e:
                logger.warning("[http_reranker] request failed for query %r: %s", query[:50], e)
        return scores
