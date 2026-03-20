"""Exit 0 iff RELEASE_NOTES ## vX.Y.Z matches pkg/version.py BUILD_NUMBER."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
notes = (ROOT / "RELEASE_NOTES.md").read_text(encoding="utf-8")
ver_py = (ROOT / "pkg" / "version.py").read_text(encoding="utf-8")
nm = re.search(r"##\s+v([\d.]+)", notes)
vm = re.search(r'BUILD_NUMBER\s*=\s*"([^"]+)"', ver_py)
if not nm or not vm:
    sys.exit(1)
if nm.group(1) != vm.group(1):
    sys.exit(1)
sys.exit(0)
