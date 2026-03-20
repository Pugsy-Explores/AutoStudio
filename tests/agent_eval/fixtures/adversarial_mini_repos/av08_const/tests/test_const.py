"""Tests for BASE_URI."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mod_b.client import get_base


def test_base_uri():
    assert get_base() == "https"
