"""Canonical cross-encoder reranker: MS MARCO MiniLM L6 v2, ONNX Runtime, CPU, FP32 only."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np

from agent.retrieval.reranker.base_reranker import BaseReranker
from agent.retrieval.reranker.constants import (
    DEVICE,
    INFER_BATCH_SIZE,
    MODEL_NAME,
    ONNX_RELATIVE_PATH,
    PRECISION,
    TOKENIZER_MAX_LENGTH,
)
from agent.retrieval.reranker.ort_session_options import (
    apply_graph_optimization_level,
    make_reranker_session_options,
)


def _project_root() -> Path:
    if os.environ.get("SERENA_PROJECT_DIR"):
        return Path(os.environ["SERENA_PROJECT_DIR"]).resolve()
    return Path(__file__).resolve().parent.parent.parent.parent


def _resolve_onnx_path() -> str:
    root = _project_root()
    p = root / ONNX_RELATIVE_PATH
    if not p.is_file():
        raise RuntimeError(
            f"Reranking failed: ONNX model missing at {p}. "
            f"Run: python scripts/prepare_reranker_models.py"
        )
    return str(p.resolve())


class MiniLMReranker(BaseReranker):
    """ONNX Runtime cross-encoder on CPU (FP32). Public API: ``rerank_batch`` only (via base)."""

    def __init__(self) -> None:
        import onnxruntime as ort
        from transformers import AutoTokenizer

        self.model_name = MODEL_NAME
        self.device = DEVICE
        self.precision = PRECISION
        assert self.model_name == "cross-encoder/ms-marco-MiniLM-L-6-v2"
        assert self.device == "cpu"

        onnx_path = _resolve_onnx_path()
        self._onnx_path = onnx_path
        _so = make_reranker_session_options()
        apply_graph_optimization_level(_so, ort, onnx_path, aggressive_non_fp16=True)
        self.session = ort.InferenceSession(
            onnx_path,
            sess_options=_so,
            providers=["CPUExecutionProvider"],
        )
        active = self.session.get_providers()
        assert "CPUExecutionProvider" in active, f"expected CPUExecutionProvider, got {active}"

        self.tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)

        self._input_names = {i.name for i in self.session.get_inputs()}
        self._output_names = [o.name for o in self.session.get_outputs()]

    def _logits_to_scores(self, logits: np.ndarray) -> list[float]:
        if logits.ndim == 1:
            return [float(x) for x in logits.tolist()]
        if logits.shape[-1] == 1:
            return [float(x) for x in logits[:, 0].tolist()]
        # Binary classification: take positive-class logit (standard for MS MARCO–style CE)
        return [float(x) for x in logits[:, -1].tolist()]

    def _score_pairs(self, pairs: list[tuple[str, str]]) -> list[float]:
        if not pairs:
            return []
        all_scores: list[float] = []
        for start in range(0, len(pairs), INFER_BATCH_SIZE):
            batch = pairs[start : start + INFER_BATCH_SIZE]
            queries = [p[0] for p in batch]
            passages = [p[1] for p in batch]
            encoded = self.tokenizer(
                queries,
                passages,
                padding=True,
                truncation=True,
                max_length=TOKENIZER_MAX_LENGTH,
                return_tensors="np",
            )
            inputs: dict[str, np.ndarray] = {}
            for k, v in encoded.items():
                if k not in self._input_names:
                    continue
                arr = np.asarray(v)
                if arr.dtype.name in ("int32", "int64"):
                    inputs[k] = arr.astype(np.int64)
                else:
                    inputs[k] = arr
            outputs = self.session.run(self._output_names, inputs)
            logits = np.asarray(outputs[0])
            all_scores.extend(self._logits_to_scores(logits))
        if len(all_scores) != len(pairs):
            raise RuntimeError("Reranking failed: score count mismatch")
        return all_scores
