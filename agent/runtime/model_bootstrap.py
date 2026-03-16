"""Model warm-start: preload embedding, reranker when not using daemon.

When retrieval daemon is reachable (RERANKER_USE_DAEMON / EMBEDDING_USE_DAEMON),
skips in-process load to avoid cold-start. Daemon holds warm models.
"""

import json
import logging
import urllib.request

logger = logging.getLogger(__name__)


def _daemon_reachable() -> bool:
    """Return True if retrieval daemon /health returns 200."""
    try:
        from config.retrieval_config import RETRIEVAL_DAEMON_PORT

        req = urllib.request.Request(f"http://127.0.0.1:{RETRIEVAL_DAEMON_PORT}/health", method="GET")
        with urllib.request.urlopen(req, timeout=2) as resp:
            data = json.loads(resp.read().decode())
        return data.get("reranker_loaded", False) or data.get("embedding_loaded", False)
    except Exception:
        return False


def initialize_models() -> None:
    """
    Load embedding model, reranker at startup when daemon is not used.
    When retrieval daemon is reachable, skips in-process load (daemon holds warm models).
    """
    from config.retrieval_config import EMBEDDING_USE_DAEMON, RERANKER_USE_DAEMON

    use_daemon = (RERANKER_USE_DAEMON or EMBEDDING_USE_DAEMON) and _daemon_reachable()

    if use_daemon:
        logger.info("[model_bootstrap] using retrieval daemon; skipping in-process model load")
        logger.info("[model_bootstrap] warm-start complete")
        return

    # Reranker (in-process)
    try:
        from agent.retrieval.reranker.reranker_factory import create_reranker, init_reranker

        init_reranker()
        r = create_reranker()
        if r:
            logger.info("[model_bootstrap] reranker ready")
        else:
            logger.debug("[model_bootstrap] reranker disabled or unavailable")
    except Exception as e:
        logger.debug("[model_bootstrap] reranker init skipped: %s", e)

    # Embedding model (used by vector_retriever)
    try:
        from sentence_transformers import SentenceTransformer

        _ = SentenceTransformer("all-MiniLM-L6-v2")
        logger.info("[model_bootstrap] embedding model ready")
    except ImportError:
        logger.debug("[model_bootstrap] sentence_transformers not installed; embedding warmup skipped")
    except Exception as e:
        logger.debug("[model_bootstrap] embedding warmup skipped: %s", e)

    logger.info("[model_bootstrap] warm-start complete")
