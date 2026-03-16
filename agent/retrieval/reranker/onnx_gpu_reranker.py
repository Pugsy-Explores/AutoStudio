"""GPU cross-encoder reranker using ONNX Runtime with INT8 quantized model on CUDA.

Requires: onnxruntime-gpu (or onnxruntime with CUDA), transformers
Uses same INT8 model as CPU path for consistent quality and lower memory.
"""

from __future__ import annotations

import os
from pathlib import Path

from agent.retrieval.reranker.base_reranker import BaseReranker
from config.retrieval_config import (
    RERANKER_BATCH_SIZE,
    RERANKER_CPU_MODEL,
    RERANKER_CPU_TOKENIZER,
)


def _project_root() -> Path:
    if os.environ.get("SERENA_PROJECT_DIR"):
        return Path(os.environ["SERENA_PROJECT_DIR"]).resolve()
    return Path(__file__).resolve().parent.parent.parent.parent


def _resolve_model_path(path: str) -> str:
    p = Path(path)
    if p.is_absolute() and p.exists():
        return str(p)
    if "/" in path and not path.startswith("models/") and not p.exists():
        return path
    root = _project_root()
    resolved = root / path
    return str(resolved.resolve())


class OnnxGPUReranker(BaseReranker):
    """INT8 ONNX reranker for CUDA GPUs.

    Uses same quantized model as CPU path. Lower memory than FP16 sentence-transformers.
    """

    def __init__(self, model_path: str = RERANKER_CPU_MODEL) -> None:
        import onnxruntime as ort  # noqa: PLC0415
        from transformers import AutoTokenizer  # noqa: PLC0415

        resolved_path = _resolve_model_path(model_path)
        self.model_path = resolved_path
        # Prefer CUDA; fall back to CPU if CUDA provider unavailable
        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        try:
            self.session = ort.InferenceSession(
                resolved_path,
                providers=providers,
            )
        except Exception:
            self.session = ort.InferenceSession(
                resolved_path,
                providers=["CPUExecutionProvider"],
            )
        tok = RERANKER_CPU_TOKENIZER
        if tok.startswith("models/"):
            resolved_tok = _resolve_model_path(tok)
            if Path(resolved_tok).exists():
                tok = resolved_tok
            else:
                tok = "Qwen/Qwen3-Reranker-0.6B"
        self.tokenizer = AutoTokenizer.from_pretrained(tok)
        # Qwen3-Reranker outputs (batch, seq, vocab); extract yes/no token scores
        try:
            self._token_yes_id = self.tokenizer.convert_tokens_to_ids("yes")
            self._token_no_id = self.tokenizer.convert_tokens_to_ids("no")
        except Exception:
            self._token_yes_id = self._token_no_id = None

    def _logits_to_scores(self, logits) -> list[float]:
        """Convert model output to scalar scores. Handles 2D (batch,2) and 3D (batch,seq,vocab) Qwen3."""
        import numpy as np  # noqa: PLC0415

        if logits.ndim == 2:
            return [float(x) for x in logits[:, -1].tolist()]
        if logits.ndim == 3:
            # Qwen3-Reranker: (batch, seq, vocab); use last token, yes/no logits, softmax
            last = logits[:, -1, :]
            if self._token_yes_id is not None and self._token_no_id is not None:
                true_logits = last[:, self._token_yes_id].astype(np.float64)
                false_logits = last[:, self._token_no_id].astype(np.float64)
                # Softmax over [no, yes]
                exp_f = np.exp(false_logits - np.maximum(false_logits, true_logits))
                exp_t = np.exp(true_logits - np.maximum(false_logits, true_logits))
                scores_yes = exp_t / (exp_f + exp_t)
                return [float(s) for s in scores_yes]
            # Fallback: use last vocab logit as scalar (may be wrong but avoids crash)
            return [float(last[i, -1]) for i in range(last.shape[0])]
        return [float(x) for x in logits.flatten().tolist()[: logits.shape[0]]]

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

            output_names = [o.name for o in self.session.get_outputs()]
            input_names = {i.name for i in self.session.get_inputs()}
            inputs = {}
            for k, v in encoded.items():
                if k not in input_names:
                    continue
                if v.dtype.name in ("int32", "int64"):
                    inputs[k] = v.astype(np.int64)
                else:
                    inputs[k] = v
            # Qwen3-Reranker ONNX requires position_ids; tokenizer does not return it
            if "position_ids" in input_names:
                seq_len = inputs["input_ids"].shape[1]
                batch_size = inputs["input_ids"].shape[0]
                inputs["position_ids"] = np.arange(seq_len, dtype=np.int64)[None, :].repeat(
                    batch_size, axis=0
                )

            outputs = self.session.run(output_names, inputs)
            logits = outputs[0]
            batch_scores = self._logits_to_scores(logits)
            all_scores.extend(batch_scores)

        return all_scores
