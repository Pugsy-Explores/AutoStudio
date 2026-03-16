"""Hardware detection for reranker runtime selection."""

from config.retrieval_config import RERANKER_DEVICE


def detect_hardware() -> str:
    """Return 'gpu' or 'cpu' based on config and available hardware.

    Respects the explicit RERANKER_DEVICE override first, then probes torch.
    Falls back to 'cpu' when torch is absent or no CUDA device is found.
    """
    if RERANKER_DEVICE in ("cpu", "gpu"):
        return RERANKER_DEVICE
    try:
        import torch  # noqa: PLC0415

        if torch.cuda.is_available():
            return "gpu"
    except ImportError:
        pass
    return "cpu"
