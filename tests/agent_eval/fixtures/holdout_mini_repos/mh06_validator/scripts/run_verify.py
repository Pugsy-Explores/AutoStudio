"""Exit 0 iff valid.check.is_valid('hello') is True. Non-pytest validation for holdout."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from valid.check import is_valid

if not is_valid("hello"):
    sys.exit(1)
sys.exit(0)
