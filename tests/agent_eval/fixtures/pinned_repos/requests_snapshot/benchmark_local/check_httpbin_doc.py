"""PASS iff HTTPBIN_NOTE first URL matches DEFAULT_HTTPBIN_BASE host path."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
note = (ROOT / "HTTPBIN_NOTE.md").read_text(encoding="utf-8")
meta = (ROOT / "bench_requests_meta.py").read_text(encoding="utf-8")
nm = re.search(r"\*\*`([^`]+)`\*\*", note)
mm = re.search(r'DEFAULT_HTTPBIN_BASE\s*=\s*"([^"]+)"', meta)
if not nm or not mm:
    sys.exit(1)
if urlparse(nm.group(1).strip()).netloc != urlparse(mm.group(1).strip()).netloc:
    sys.exit(1)
sys.exit(0)
