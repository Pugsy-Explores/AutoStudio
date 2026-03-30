#!/usr/bin/env python3
"""Wrapper so `python prepare_reranker_models.py` works from repo root. Implementation: scripts/prepare_reranker_models.py"""

from __future__ import annotations

import pathlib
import runpy

_root = pathlib.Path(__file__).resolve().parent
_impl = _root / "scripts" / "prepare_reranker_models.py"
if not _impl.is_file():
    raise SystemExit(f"Missing {_impl}")
runpy.run_path(str(_impl), run_name="__main__")
