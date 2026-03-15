"""Unit tests for anchor_detector."""

import pytest

from agent.retrieval.anchor_detector import detect_anchors


def test_detect_anchors_with_symbol():
    """Results with symbol are kept as anchors."""
    results = [
        {"file": "a.py", "symbol": "Foo", "snippet": "class Foo"},
        {"file": "b.py", "symbol": "", "snippet": "other"},
    ]
    anchors = detect_anchors(results, "Foo")
    assert len(anchors) == 1
    assert anchors[0]["file"] == "a.py" and anchors[0]["symbol"] == "Foo"


def test_detect_anchors_snippet_has_class_def():
    """Snippet containing 'class X' matching query is anchor."""
    results = [
        {"file": "a.py", "symbol": "", "snippet": "class StepExecutor:\n  pass"},
    ]
    anchors = detect_anchors(results, "StepExecutor")
    assert len(anchors) == 1


def test_detect_anchors_fallback_when_no_anchors():
    """When no anchors detected, fallback to top N results."""
    results = [
        {"file": "x.py", "symbol": "", "snippet": "x = 1"},
        {"file": "y.py", "symbol": "", "snippet": "y = 2"},
        {"file": "z.py", "symbol": "", "snippet": "z = 3"},
    ]
    anchors = detect_anchors(results, "UnrelatedQuery")
    assert len(anchors) == 3  # fallback_top_n default 3
    assert anchors[0]["file"] == "x.py"


def test_detect_anchors_empty_results():
    """Empty results return empty list."""
    assert detect_anchors([], "q") == []
    assert detect_anchors(None, "q") == []
