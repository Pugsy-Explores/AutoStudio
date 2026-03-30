"""Tests for logging levels."""

import importlib.util
from pathlib import Path

# Load workspace logging.levels explicitly to avoid stdlib 'logging' shadowing.
# Pytest imports stdlib logging before tests run; use importlib to get local package.
_root = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "logging_levels", _root / "logging" / "levels.py"
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
get_severity = getattr(_mod, "get_severity", None)
if get_severity is None:
    raise ImportError("get_severity not found in logging/levels.py")


def test_get_severity():
    s = get_severity()
    assert isinstance(s, str) and len(s) > 0
