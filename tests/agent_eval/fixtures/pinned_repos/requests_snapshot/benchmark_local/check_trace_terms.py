"""PASS if artifact lists required facts about Sessions (explain-task rubric)."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
text = (ROOT / "artifacts" / "explain_out.txt").read_text(encoding="utf-8")
required = ("Session.request", "hooks")
for term in required:
    if term not in text:
        sys.exit(1)
# At least one line looks like a trace (arrow or '->')
if not re.search(r"(->|→|calls\s+)", text, re.I):
    sys.exit(1)
sys.exit(0)
