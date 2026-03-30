"""ONNX Runtime SessionOptions for the MiniLM reranker (CPU FP32)."""

from __future__ import annotations


def apply_graph_optimization_level(so, ort, resolved_model_path: str, *, aggressive_non_fp16: bool = False) -> None:
    """FP32 MiniLM: full graph optimizations unless explicitly disabled."""
    del resolved_model_path
    if aggressive_non_fp16:
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL


def make_reranker_session_options():
    """SessionOptions: disable CPU mem arena for lower steady-state RSS."""
    import onnxruntime as ort

    so = ort.SessionOptions()
    so.enable_cpu_mem_arena = False
    return so
