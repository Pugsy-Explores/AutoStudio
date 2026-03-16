"""Tests for agent/retrieval/reranker/deduplicator.deduplicate_candidates."""

import pytest

from agent.retrieval.reranker.deduplicator import deduplicate_candidates


def test_deduplicate_candidates_exact_duplicates():
    """Exact duplicate snippets are removed; first occurrence kept."""
    candidates = [
        {"file": "a.py", "symbol": "foo", "snippet": "def foo(): pass", "type": "symbol"},
        {"file": "b.py", "symbol": "bar", "snippet": "def foo(): pass", "type": "symbol"},
        {"file": "c.py", "symbol": "baz", "snippet": "def baz(): pass", "type": "symbol"},
    ]
    result = deduplicate_candidates(candidates)
    assert len(result) == 2
    assert result[0]["snippet"] == "def foo(): pass"
    assert result[0]["file"] == "a.py"
    assert result[1]["snippet"] == "def baz(): pass"


def test_deduplicate_candidates_no_duplicates():
    """No duplicates when all snippets are unique."""
    candidates = [
        {"file": "a.py", "symbol": "foo", "snippet": "def foo(): pass", "type": "symbol"},
        {"file": "b.py", "symbol": "bar", "snippet": "def bar(): pass", "type": "symbol"},
    ]
    result = deduplicate_candidates(candidates)
    assert len(result) == 2


def test_deduplicate_candidates_empty():
    """Empty list returns empty list."""
    assert deduplicate_candidates([]) == []


def test_deduplicate_candidates_whitespace():
    """Different whitespace produces different snippets (SHA-256 of snippet)."""
    candidates = [
        {"file": "a.py", "symbol": "foo", "snippet": "def foo(): pass", "type": "symbol"},
        {"file": "b.py", "symbol": "foo", "snippet": "def foo(): pass ", "type": "symbol"},
    ]
    result = deduplicate_candidates(candidates)
    assert len(result) == 2


def test_deduplicate_candidates_preserves_order():
    """First occurrence of each unique snippet is kept."""
    candidates = [
        {"file": "x.py", "symbol": "first", "snippet": "same", "type": "symbol"},
        {"file": "y.py", "symbol": "second", "snippet": "same", "type": "symbol"},
    ]
    result = deduplicate_candidates(candidates)
    assert len(result) == 1
    assert result[0]["file"] == "x.py"
    assert result[0]["symbol"] == "first"
