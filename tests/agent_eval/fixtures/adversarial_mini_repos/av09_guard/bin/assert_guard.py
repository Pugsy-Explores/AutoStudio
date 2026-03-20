"""Exit 0 iff validation.guard.validate_input('ok') is True."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from validation.guard import validate_input

if not validate_input("ok"):
    sys.exit(1)
sys.exit(0)
