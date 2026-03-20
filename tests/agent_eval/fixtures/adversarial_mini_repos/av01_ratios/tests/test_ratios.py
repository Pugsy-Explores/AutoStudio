"""Tests for ratio utilities."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
from ratios import normalize_ratios


def test_normalize_ratios():
    assert normalize_ratios(12.0, 4.0) == 3.0
