"""Exit 0 iff VERSION_HISTORY ## vX.Y.Z matches core/version.py CURRENT_VERSION."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
hist = (ROOT / "VERSION_HISTORY.md").read_text(encoding="utf-8")
ver_py = (ROOT / "core" / "version.py").read_text(encoding="utf-8")
hm = re.search(r"##\s+v([\d.]+)", hist)
vm = re.search(r'CURRENT_VERSION\s*=\s*"([^"]+)"', ver_py)
if not hm or not vm:
    sys.exit(1)
if hm.group(1) != vm.group(1):
    sys.exit(1)
sys.exit(0)
