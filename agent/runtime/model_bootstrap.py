"""Model warm-start: preload MiniLM reranker (in-process) and optionally local embeddings."""

import logging

logger = logging.getLogger(__name__)


def initialize_models() -> None:
    """
    Load reranker (ONNX CPU) when enabled. Load SentenceTransformer locally only when
    the retrieval daemon is not used for embeddings.
    """
    from config.retrieval_config import EMBEDDING_USE_DAEMON, RERANKER_ENABLED

    if RERANKER_ENABLED:
        try:
            from agent.retrieval.reranker.reranker_factory import create_reranker, init_reranker

            init_reranker()
            r = create_reranker()
            if r:
                logger.info("[model_bootstrap] reranker ready")
            else:
                logger.debug("[model_bootstrap] reranker disabled (RERANKER_ENABLED=0)")
        except Exception as e:
            logger.warning("[model_bootstrap] reranker init failed: %s", e)

    def _daemon_embed_ok() -> bool:
        try:
            from agent.retrieval.daemon_client import daemon_embed_available

            return daemon_embed_available()
        except Exception:
            return False

    if EMBEDDING_USE_DAEMON and _daemon_embed_ok():
        logger.info("[model_bootstrap] embedding via daemon; skipping local SentenceTransformer load")
        logger.info("[model_bootstrap] warm-start complete")
        return

    try:
        from sentence_transformers import SentenceTransformer

        _ = SentenceTransformer("all-MiniLM-L6-v2")
        logger.info("[model_bootstrap] embedding model ready")
    except ImportError:
        logger.debug("[model_bootstrap] sentence_transformers not installed; embedding warmup skipped")
    except Exception as e:
        logger.debug("[model_bootstrap] embedding warmup skipped: %s", e)

    logger.info("[model_bootstrap] warm-start complete")
