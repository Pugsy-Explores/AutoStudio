"""Tests for config defaults."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cfg.defaults import cfg_verbose


def test_cfg_verbose():
    assert cfg_verbose() is False
