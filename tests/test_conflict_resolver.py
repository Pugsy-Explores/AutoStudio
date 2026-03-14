"""Tests for editing/conflict_resolver."""

import pytest

from editing.conflict_resolver import resolve_conflicts


def test_resolve_conflicts_empty():
    """resolve_conflicts returns valid for empty changes."""
    result = resolve_conflicts({"changes": []})
    assert result["valid"] is True


def test_resolve_conflicts_no_conflicts():
    """resolve_conflicts returns valid when no conflicts."""
    patch_plan = {
        "changes": [
            {"file": "a.py", "symbol": "foo", "action": "modify", "patch": "...", "reason": "r1"},
            {"file": "b.py", "symbol": "bar", "action": "modify", "patch": "...", "reason": "r2"},
        ]
    }
    result = resolve_conflicts(patch_plan)
    assert result["valid"] is True


def test_resolve_conflicts_same_symbol():
    """resolve_conflicts detects multiple edits to same symbol."""
    patch_plan = {
        "changes": [
            {"file": "a.py", "symbol": "foo", "action": "modify", "patch": "p1", "reason": "r1"},
            {"file": "a.py", "symbol": "foo", "action": "modify", "patch": "p2", "reason": "r2"},
        ]
    }
    result = resolve_conflicts(patch_plan)
    assert result["valid"] is False
    assert "conflicts" in result
    assert len(result["conflicts"]) >= 1
    assert any(c.get("type") == "same_symbol" for c in result["conflicts"])
    assert "sequential_groups" in result
    assert len(result["sequential_groups"]) >= 2


def test_resolve_conflicts_same_file():
    """resolve_conflicts detects multiple edits to same file."""
    patch_plan = {
        "changes": [
            {"file": "a.py", "symbol": "foo", "action": "modify", "patch": "p1", "reason": "r1"},
            {"file": "a.py", "symbol": "bar", "action": "modify", "patch": "p2", "reason": "r2"},
        ]
    }
    result = resolve_conflicts(patch_plan)
    assert result["valid"] is False
    assert "conflicts" in result
    assert "sequential_groups" in result


def test_resolve_conflicts_semantic_overlap():
    """resolve_conflicts detects semantic overlap (Foo vs Foo.bar)."""
    patch_plan = {
        "changes": [
            {"file": "a.py", "symbol": "Foo", "action": "modify", "patch": "p1", "reason": "r1"},
            {"file": "a.py", "symbol": "Foo.bar", "action": "modify", "patch": "p2", "reason": "r2"},
        ]
    }
    result = resolve_conflicts(patch_plan)
    assert result["valid"] is False
    assert any(c.get("type") == "semantic_overlap" for c in result.get("conflicts", []))
