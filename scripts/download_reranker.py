#!/usr/bin/env python3
"""Download the reranker model appropriate for the current hardware.

Usage:
    python scripts/download_reranker.py
    python scripts/download_reranker.py --model BAAI/bge-reranker-v2-gemma
    python scripts/download_reranker.py --device cpu
    python scripts/download_reranker.py --device gpu
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Ensure project root is on sys.path when called directly
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from agent.retrieval.reranker.hardware import detect_hardware  # noqa: E402
from config.retrieval_config import (  # noqa: E402
    RERANKER_ALTERNATE_MODELS,
    RERANKER_CPU_MODEL,
    RERANKER_GPU_MODEL,
)

DEFAULT_GPU_REPO = RERANKER_GPU_MODEL
DEFAULT_CPU_REPO = "zhiqing/Qwen3-Reranker-0.6B-ONNX"
MODELS_DIR = _ROOT / "models" / "reranker"


def _download_gpu(model_id: str) -> None:
    try:
        from huggingface_hub import snapshot_download  # noqa: PLC0415
    except ImportError:
        logger.error("huggingface_hub is required: pip install huggingface-hub")
        sys.exit(1)

    dest = MODELS_DIR / "gpu"
    dest.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading GPU model %s → %s", model_id, dest)
    snapshot_download(repo_id=model_id, local_dir=str(dest))
    logger.info("GPU model downloaded to %s", dest)


def _download_cpu(model_id: str) -> None:
    try:
        from huggingface_hub import hf_hub_download, list_repo_files  # noqa: PLC0415
    except ImportError:
        logger.error("huggingface_hub is required: pip install huggingface-hub")
        sys.exit(1)

    dest = MODELS_DIR
    dest.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading CPU (ONNX INT8) model from %s → %s", model_id, dest)

    # Download all .onnx files from the repo
    try:
        files = list(list_repo_files(model_id))
    except Exception as exc:
        logger.error("Failed to list repo files for %s: %s", model_id, exc)
        sys.exit(1)

    onnx_files = [f for f in files if f.endswith(".onnx") or f.endswith(".json")]
    if not onnx_files:
        logger.warning("No .onnx files found in %s — downloading full repo.", model_id)
        from huggingface_hub import snapshot_download  # noqa: PLC0415
        snapshot_download(repo_id=model_id, local_dir=str(dest))
    else:
        for fname in onnx_files:
            logger.info("  Downloading %s", fname)
            hf_hub_download(repo_id=model_id, filename=fname, local_dir=str(dest))

    logger.info("CPU model downloaded to %s", dest)
    logger.info("Default RERANKER_CPU_MODEL=models/reranker/model.onnx (matches repo)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Download AutoStudio reranker model")
    parser.add_argument(
        "--device",
        choices=["cpu", "gpu", "auto"],
        default="auto",
        help="Target device (default: auto-detect)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help=(
            f"Override model ID. GPU default: {DEFAULT_GPU_REPO}. "
            f"CPU default: {DEFAULT_CPU_REPO}. "
            f"Alternates: {RERANKER_ALTERNATE_MODELS}"
        ),
    )
    args = parser.parse_args()

    device = args.device if args.device != "auto" else detect_hardware()
    logger.info("Detected device: %s", device)

    if device == "gpu":
        model_id = args.model or DEFAULT_GPU_REPO
        _download_gpu(model_id)
    else:
        model_id = args.model or DEFAULT_CPU_REPO
        _download_cpu(model_id)


if __name__ == "__main__":
    main()
