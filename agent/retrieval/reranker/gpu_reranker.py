"""GPU cross-encoder reranker using sentence-transformers CrossEncoder with FP16.

Requires: sentence-transformers, torch (with CUDA)
"""

from __future__ import annotations

from agent.retrieval.reranker.base_reranker import BaseReranker
from config.retrieval_config import RERANKER_BATCH_SIZE, RERANKER_GPU_MODEL


class GPUReranker(BaseReranker):
    """FP16 CrossEncoder reranker for CUDA-capable GPUs.

    Enables half-precision when the device supports compute capability >= 7.0
    (Volta and later) to halve memory footprint and improve throughput.
    """

    def __init__(self, model_name: str = RERANKER_GPU_MODEL) -> None:
        from sentence_transformers import CrossEncoder  # noqa: PLC0415

        self.model_name = model_name
        self.model = CrossEncoder(model_name, device="cuda")

        # Enable FP16 on Volta+ GPUs (compute capability >= 7.0)
        try:
            import torch  # noqa: PLC0415

            cap = torch.cuda.get_device_capability()
            if cap[0] >= 7:
                self.model.model.half()
        except Exception:
            pass  # stay in FP32 if capability check fails

    def _score_pairs(self, pairs: list[tuple[str, str]]) -> list[float]:
        scores = self.model.predict(pairs, batch_size=RERANKER_BATCH_SIZE)
        return [float(s) for s in scores]
