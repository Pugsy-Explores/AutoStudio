"""Exit 0 iff API.md bold URL netloc matches spec/api_spec.py API_BASE netloc."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
api_md = (ROOT / "API.md").read_text(encoding="utf-8")
spec_py = (ROOT / "spec" / "api_spec.py").read_text(encoding="utf-8")
mm = re.search(r"\*\*([^*]+)\*\*", api_md)
sm = re.search(r'API_BASE\s*=\s*"([^"]+)"', spec_py)
if not mm or not sm:
    sys.exit(1)
api_url = mm.group(1).strip()
spec_url = sm.group(1).strip()
api_netloc = urlparse(api_url).netloc
spec_netloc = urlparse(spec_url).netloc
if api_netloc != spec_netloc:
    sys.exit(1)
sys.exit(0)
