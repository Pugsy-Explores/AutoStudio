"""Exit 0 iff README major.minor matches APP_VERSION major.minor."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
readme = (ROOT / "README.md").read_text(encoding="utf-8")
code = (ROOT / "src" / "widget" / "constants.py").read_text(encoding="utf-8")
rm = re.search(r"Current release:\s*\*\*([^*]+)\*\*", readme)
cm = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', code)
if not rm or not cm:
    sys.exit(1)
rv, cv = rm.group(1).strip(), cm.group(1).strip()
r_parts = rv.split(".")
c_parts = cv.split(".")
if len(r_parts) < 2 or len(c_parts) < 2:
    sys.exit(1)
if r_parts[0] != c_parts[0] or r_parts[1] != c_parts[1]:
    sys.exit(1)
sys.exit(0)
