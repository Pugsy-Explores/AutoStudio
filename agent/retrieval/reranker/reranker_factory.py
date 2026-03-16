"""Reranker factory with warm-start initialization and failure fallback.

Public API:
  init_reranker()    — call once at process start
  create_reranker()  — returns singleton BaseReranker or None if disabled

When RERANKER_USE_DAEMON=1 and retrieval daemon is reachable (daemon_client),
returns HttpRerankerClient so reranking uses the daemon. Otherwise builds in-process.
"""

from __future__ import annotations

import logging

from agent.retrieval.daemon_client import daemon_reranker_available
from agent.retrieval.reranker.base_reranker import BaseReranker
from agent.retrieval.reranker.hardware import detect_hardware
from agent.retrieval.reranker.http_reranker import HttpRerankerClient
from config.retrieval_config import RERANKER_USE_DAEMON, RERANKER_USE_INT8, RETRIEVAL_DAEMON_PORT

logger = logging.getLogger(__name__)

_reranker_instance: BaseReranker | None = None
_RERANKER_DISABLED: bool = False


def create_reranker() -> BaseReranker | None:
    """Return the singleton reranker instance, or None when disabled.

    When RERANKER_USE_DAEMON=1, tries retrieval daemon first. If reachable,
    returns HttpRerankerClient. Otherwise falls back to in-process build.
    """
    global _reranker_instance, _RERANKER_DISABLED  # noqa: PLW0603

    if _RERANKER_DISABLED:
        return None

    if _reranker_instance is None:
        if RERANKER_USE_DAEMON and daemon_reranker_available():
            _reranker_instance = HttpRerankerClient(port=RETRIEVAL_DAEMON_PORT)
            logger.info("[reranker] using retrieval daemon (HTTP)")
        else:
            try:
                _reranker_instance = _build_reranker()
            except Exception as exc:
                logger.warning("[reranker] create failed — disabling reranker: %s", exc)
                _RERANKER_DISABLED = True
                return None

    return _reranker_instance


def init_reranker() -> None:
    """Build the reranker singleton and run a warmup inference pass.

    Call once at process startup to absorb CUDA kernel compilation,
    model graph creation, and memory allocation before the first real query.

    On any exception (load failure or warmup failure) the reranker is
    permanently disabled and all subsequent create_reranker() calls
    return None so the pipeline falls back to the LLM ranker.
    """
    global _reranker_instance, _RERANKER_DISABLED  # noqa: PLW0603

    if _RERANKER_DISABLED:
        return

    try:
        instance = _build_reranker()
        if instance is None:
            return
        # Warmup: discard result, absorb cold-start cost
        instance.rerank("warmup query", ["warmup snippet"])
        _reranker_instance = instance
        logger.info("[reranker] warm-start complete on %s", detect_hardware())
    except Exception as exc:
        logger.warning("[reranker] init failed — disabling reranker: %s", exc)
        _RERANKER_DISABLED = True
        _reranker_instance = None


def _build_reranker() -> BaseReranker | None:
    """Instantiate the correct reranker for the detected hardware.

    When RERANKER_USE_INT8: both CPU and GPU use ONNX INT8 (lower memory, consistent quality).
    When INT8 disabled: GPU uses sentence-transformers FP16, CPU uses ONNX INT8.
    """
    global _RERANKER_DISABLED  # noqa: PLW0603

    device = detect_hardware()
    try:
        if device == "gpu" and RERANKER_USE_INT8:
            try:
                from agent.retrieval.reranker.onnx_gpu_reranker import OnnxGPUReranker  # noqa: PLC0415

                return OnnxGPUReranker()
            except Exception as gpu_exc:
                logger.info("[reranker] INT8 GPU failed, falling back to CPU INT8: %s", gpu_exc)
                from agent.retrieval.reranker.cpu_reranker import CPUReranker  # noqa: PLC0415

                return CPUReranker()
        if device == "gpu":
            from agent.retrieval.reranker.gpu_reranker import GPUReranker  # noqa: PLC0415

            return GPUReranker()
        from agent.retrieval.reranker.cpu_reranker import CPUReranker  # noqa: PLC0415

        return CPUReranker()
    except Exception as exc:
        logger.warning("[reranker] model load failed — disabling reranker: %s", exc)
        _RERANKER_DISABLED = True
        return None


def _reset_for_testing() -> None:
    """Reset factory state. Only for use in tests."""
    global _reranker_instance, _RERANKER_DISABLED  # noqa: PLW0603
    _reranker_instance = None
    _RERANKER_DISABLED = False
