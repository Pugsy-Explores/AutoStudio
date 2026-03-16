#!/usr/bin/env python3
"""Diagnose reranker load and inference. Run when seeing inference_error:ValueError."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main():
    print("=== Reranker diagnostic ===\n")

    # 1. Config
    from config.retrieval_config import (
        RERANKER_CPU_MODEL,
        RERANKER_CPU_TOKENIZER,
        RERANKER_DEVICE,
        RERANKER_USE_INT8,
    )
    from agent.retrieval.reranker.hardware import detect_hardware

    print("Config:")
    print(f"  RERANKER_USE_INT8: {RERANKER_USE_INT8} (INT8 for both CPU and GPU)")
    print(f"  RERANKER_DEVICE: {RERANKER_DEVICE}")
    print(f"  Detected hardware: {detect_hardware()}")

    # 2. Check deps
    print("\nDependencies:")
    try:
        import onnxruntime as ort
        print(f"  onnxruntime: {ort.__version__}")
    except ImportError as e:
        print(f"  onnxruntime: NOT INSTALLED — {e}")
        return 1

    try:
        from transformers import AutoTokenizer
        print("  transformers: OK")
    except ImportError as e:
        print(f"  transformers: NOT INSTALLED — {e}")
        return 1

    # 3. Model path
    root = Path(os.environ.get("SERENA_PROJECT_DIR", os.getcwd()))
    model_path = root / RERANKER_CPU_MODEL
    print(f"\nModel (INT8 ONNX):")
    print(f"  RERANKER_CPU_MODEL: {RERANKER_CPU_MODEL}")
    print(f"  Resolved path: {model_path.resolve()}")
    print(f"  Exists: {model_path.exists()}")

    # 4. Create and run
    print("\n--- Creating reranker ---")
    try:
        from agent.retrieval.reranker.reranker_factory import create_reranker

        r = create_reranker()
        if r is None:
            print("create_reranker() returned None (disabled)")
            return 1
        print(f"  Type: {type(r).__name__}")
        path_or_name = getattr(r, "model_path", None) or getattr(r, "model_name", None)
        if path_or_name:
            print(f"  Model: {path_or_name}")
    except Exception as e:
        print(f"Create failed: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return 1

    # 5. Inference
    print("\n--- Running inference ---")
    try:
        out = r.rerank("test query", ["snippet one", "snippet two"])
        print(f"Inference OK: {len(out)} results")
    except Exception as e:
        print(f"Inference failed: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return 1

    print("\n=== All OK ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
