"""Tests for runtime options."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime.options import max_retries


def test_max_retries():
    assert max_retries() == 3
