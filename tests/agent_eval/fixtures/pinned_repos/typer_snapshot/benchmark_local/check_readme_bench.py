"""PASS iff README_BENCH.md version matches typer_ver.TYPER_BENCH_VER."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
note = (ROOT / "README_BENCH.md").read_text(encoding="utf-8")
sys.path.insert(0, str(ROOT.parent))
from benchmark_local.typer_ver import TYPER_BENCH_VER  # noqa: E402

m = re.search(r"\*\*([0-9]+\.[0-9]+\.[0-9]+)\*\*", note)
if not m:
    sys.exit(1)
note_ver = m.group(1).strip()
if note_ver != TYPER_BENCH_VER:
    sys.exit(1)
sys.exit(0)
