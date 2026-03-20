"""PASS iff VERSION_NOTE version matches version_meta.RELEASE_VERSION."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
note = (ROOT / "VERSION_NOTE.md").read_text(encoding="utf-8")
# Import from parent (workspace root) so benchmark_local.version_meta resolves
sys.path.insert(0, str(ROOT.parent))
from benchmark_local.version_meta import RELEASE_VERSION  # noqa: E402

m = re.search(r"\*\*([0-9]+\.[0-9]+\.[0-9]+)\*\*", note)
if not m:
    sys.exit(1)
note_ver = m.group(1).strip()
if note_ver != RELEASE_VERSION:
    sys.exit(1)
sys.exit(0)
