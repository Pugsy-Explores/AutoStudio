#!/usr/bin/env python3
"""Diagnose MiniLM ONNX reranker load and inference."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main():
    print("=== Reranker diagnostic (MiniLM ONNX CPU) ===\n")

    from agent.retrieval.reranker.constants import MODEL_NAME, ONNX_RELATIVE_PATH

    print("Config:")
    print(f"  MODEL_NAME: {MODEL_NAME}")
    print(f"  ONNX_RELATIVE_PATH: {ONNX_RELATIVE_PATH}")

    print("\nDependencies:")
    try:
        import onnxruntime as ort

        print(f"  onnxruntime: {ort.__version__}")
    except ImportError as e:
        print(f"  onnxruntime: NOT INSTALLED — {e}")
        return 1

    try:
        print("  transformers: OK")
    except ImportError as e:
        print(f"  transformers: NOT INSTALLED — {e}")
        return 1

    root = Path(os.environ.get("SERENA_PROJECT_DIR", os.getcwd()))
    model_path = root / ONNX_RELATIVE_PATH
    print(f"\nONNX file:")
    print(f"  Resolved path: {model_path.resolve()}")
    print(f"  Exists: {model_path.is_file()}")

    print("\n--- Creating reranker ---")
    try:
        from agent.retrieval.reranker.reranker_factory import create_reranker

        r = create_reranker()
        if r is None:
            print("create_reranker() returned None (RERANKER_ENABLED=0)")
            return 1
        print(f"  Type: {type(r).__name__}")
        onnx_p = getattr(r, "_onnx_path", None)
        if onnx_p:
            print(f"  ONNX: {onnx_p}")
    except Exception as e:
        print(f"Create failed: {type(e).__name__}: {e}")
        import traceback

        traceback.print_exc()
        return 1

    print("\n--- Running inference (rerank_batch) ---")
    try:
        out = r.rerank_batch([("test query", ["snippet one", "snippet two", "s3", "s4", "s5", "s6"])])
        print(f"Inference OK: {len(out[0])} results")
    except Exception as e:
        print(f"Inference failed: {type(e).__name__}: {e}")
        import traceback

        traceback.print_exc()
        return 1

    print("\n=== All OK ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
