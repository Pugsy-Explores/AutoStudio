"""PASS iff DECORATORS_NOTE.md stability word matches CLICK_BENCH_API_STABILITY."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
note = (ROOT / "DECORATORS_NOTE.md").read_text(encoding="utf-8")
meta = (ROOT / "bench_click_meta.py").read_text(encoding="utf-8")
nm = re.search(r"\*\*`([^`]+)`", note)
mm = re.search(r'CLICK_BENCH_API_STABILITY\s*=\s*"([^"]+)"', meta)
if not nm or not mm:
    sys.exit(1)
if nm.group(1).strip() != mm.group(1).strip():
    sys.exit(1)
sys.exit(0)
