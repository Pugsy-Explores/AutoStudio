"""Exit 0 iff CHANGELOG ## vX.Y.Z matches lib/version.py RELEASE_VERSION."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
version_py = (ROOT / "lib" / "version.py").read_text(encoding="utf-8")
cm = re.search(r"##\s+v([\d.]+)", changelog)
vm = re.search(r'RELEASE_VERSION\s*=\s*"([^"]+)"', version_py)
if not cm or not vm:
    sys.exit(1)
if cm.group(1) != vm.group(1):
    sys.exit(1)
sys.exit(0)
