#!/usr/bin/env python3
"""Print memory vs ExplorationResult fidelity checks (pytest -s)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    test = root / "tests" / "test_exploration_memory_output_fidelity.py"
    return subprocess.call(
        [sys.executable, "-m", "pytest", "-s", str(test), "-v"],
        cwd=str(root),
    )


if __name__ == "__main__":
    raise SystemExit(main())
