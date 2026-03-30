#!/usr/bin/env python3
"""Prepare the canonical MiniLM ONNX reranker (delegates to prepare_reranker_models)."""

from __future__ import annotations

import pathlib
import runpy
import sys

_root = pathlib.Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

_impl = _root / "scripts" / "prepare_reranker_models.py"
if not _impl.is_file():
    raise SystemExit(f"Missing {_impl}")
runpy.run_path(str(_impl), run_name="__main__")
