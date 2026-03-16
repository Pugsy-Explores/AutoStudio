"""CPU cross-encoder reranker using ONNX Runtime with INT8 quantized model.

Requires: onnxruntime, transformers
"""

from __future__ import annotations

from agent.retrieval.reranker.base_reranker import BaseReranker
from config.retrieval_config import RERANKER_BATCH_SIZE, RERANKER_CPU_MODEL


class CPUReranker(BaseReranker):
    """INT8 ONNX reranker for CPU-only environments.

    Tokenizes all pairs in one pass, splits into sub-batches of
    RERANKER_BATCH_SIZE, runs each sub-batch through a single session.run()
    call, and extracts the logit score for the positive relevance class.
    """

    def __init__(self, model_path: str = RERANKER_CPU_MODEL) -> None:
        import onnxruntime as ort  # noqa: PLC0415
        from transformers import AutoTokenizer  # noqa: PLC0415

        self.model_path = model_path
        self.session = ort.InferenceSession(
            model_path,
            providers=["CPUExecutionProvider"],
        )
        # Derive tokenizer from the canonical HuggingFace repo name stored
        # alongside the ONNX file, or fall back to the default model id.
        tokenizer_id = "Qwen/Qwen3-Reranker-0.6B"
        self.tokenizer = AutoTokenizer.from_pretrained(tokenizer_id)

    def _score_pairs(self, pairs: list[tuple[str, str]]) -> list[float]:
        import numpy as np  # noqa: PLC0415

        all_scores: list[float] = []

        for start in range(0, len(pairs), RERANKER_BATCH_SIZE):
            batch = pairs[start : start + RERANKER_BATCH_SIZE]
            queries = [p[0] for p in batch]
            docs = [p[1] for p in batch]

            encoded = self.tokenizer(
                queries,
                docs,
                padding=True,
                truncation=True,
                return_tensors="np",
            )

            inputs = {k: v for k, v in encoded.items() if k in {
                "input_ids", "attention_mask", "token_type_ids"
            }}
            # Drop token_type_ids if the model doesn't expect it
            output_names = [o.name for o in self.session.get_outputs()]
            input_names = {i.name for i in self.session.get_inputs()}
            inputs = {k: v for k, v in inputs.items() if k in input_names}

            outputs = self.session.run(output_names, inputs)
            logits = outputs[0]  # shape: (batch, num_labels) or (batch,)

            if logits.ndim == 2:
                # Binary classification — use the positive class logit
                batch_scores = logits[:, -1].tolist()
            else:
                batch_scores = logits.tolist()

            all_scores.extend([float(s) for s in batch_scores])

        return all_scores
