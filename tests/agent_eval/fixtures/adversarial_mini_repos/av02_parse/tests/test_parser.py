"""Tests for byte parser."""

import importlib.util
from pathlib import Path

# Load workspace io.bytes_parser explicitly. Python's frozen stdlib 'io' always wins
# over a local io package; use importlib to load the local module.
_root = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "io_bytes_parser", _root / "io" / "bytes_parser.py"
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
parse_bytes = _mod.parse_bytes


def test_parse_bytes():
    result = parse_bytes(b"foo bar baz")
    assert isinstance(result, list)
    assert len(result) == 3
