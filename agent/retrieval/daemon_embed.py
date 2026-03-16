"""Retrieval-daemon embedding: encode via daemon when available.

When the daemon is available (daemon_client.daemon_embed_available()), all
embedding usage (vector_retriever, task_index) must go through encode_via_daemon
so the agent process never loads SentenceTransformer in-process.
"""

from __future__ import annotations

import json
import logging
import urllib.request

from agent.retrieval.daemon_client import daemon_embed_available
from config.retrieval_config import RETRIEVAL_DAEMON_PORT

logger = logging.getLogger(__name__)


def encode_via_daemon(texts: list[str]) -> list[list[float]] | None:
    """Encode texts via retrieval daemon POST /embed. Returns embeddings or None on failure."""
    if not texts or not daemon_embed_available():
        return None
    try:
        body = json.dumps({"texts": texts}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{RETRIEVAL_DAEMON_PORT}/embed",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        emb = data.get("embeddings", [])
        if data.get("error") or not emb:
            return None
        return emb
    except Exception as e:
        logger.debug("[daemon_embed] encode failed: %s", e)
        return None
