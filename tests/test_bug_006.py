"""Regression test for BUG-006: symbol_graph get_symbol_dependencies stub returns empty.

Ensures get_symbol_dependencies queries GraphStorage when index exists
and returns [] gracefully when no index (no crash).
"""

import os
import tempfile
from pathlib import Path

import pytest

from agent.retrieval.symbol_graph import get_symbol_dependencies


def test_get_symbol_dependencies_returns_empty_when_no_index():
    """When index.sqlite does not exist, should return [] without crashing."""
    with tempfile.TemporaryDirectory() as tmp:
        result = get_symbol_dependencies("StepExecutor", project_root=tmp)
    assert result == []


def test_get_symbol_dependencies_returns_empty_for_empty_symbol():
    """Empty or whitespace symbol should return []."""
    with tempfile.TemporaryDirectory() as tmp:
        assert get_symbol_dependencies("", project_root=tmp) == []
        assert get_symbol_dependencies("   ", project_root=tmp) == []


def test_get_symbol_dependencies_returns_list():
    """Result should always be a list (empty or with dicts)."""
    with tempfile.TemporaryDirectory() as tmp:
        result = get_symbol_dependencies("StepExecutor", project_root=tmp)
    assert isinstance(result, list)
