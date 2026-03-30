"""Exit 0 iff SPEC.md bold URL netloc matches impl/spec.py DEFAULT_ENDPOINT netloc."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
spec_md = (ROOT / "SPEC.md").read_text(encoding="utf-8")
spec_py = (ROOT / "impl" / "spec.py").read_text(encoding="utf-8")
mm = re.search(r"\*\*([^*]+)\*\*", spec_md)
sm = re.search(r'DEFAULT_ENDPOINT\s*=\s*"([^"]+)"', spec_py)
if not mm or not sm:
    sys.exit(1)
md_url = mm.group(1).strip()
py_url = sm.group(1).strip()
if urlparse(md_url).netloc != urlparse(py_url).netloc:
    sys.exit(1)
sys.exit(0)
