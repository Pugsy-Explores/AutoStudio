"""Unit tests for SEARCH Quality Audit (no LLM)."""

from __future__ import annotations

import json

import pytest

from agent.eval.search_quality_audit import (
    _parse_audit_json,
    _validate_and_normalize,
    aggregate_audit_results,
    effective_search_score,
    is_weak_or_bad,
)


def test_parse_audit_json_plain():
    raw = '{"grounding": 2, "specificity": 3, "verdict": "acceptable"}'
    p = _parse_audit_json(raw)
    assert p is not None
    assert p["grounding"] == 2
    assert p["specificity"] == 3
    assert p["verdict"] == "acceptable"


def test_parse_audit_json_with_markdown():
    raw = '```json\n{"grounding": 1, "verdict": "weak"}\n```'
    p = _parse_audit_json(raw)
    assert p is not None
    assert p["grounding"] == 1
    assert p["verdict"] == "weak"


def test_validate_and_normalize_clamps_scores():
    p = _validate_and_normalize({"grounding": 5, "specificity": -1, "verdict": "excellent"})
    assert p["grounding"] == 3
    assert p["specificity"] == 0
    assert p["verdict"] == "excellent"


def test_validate_and_normalize_red_flags_filter():
    p = _validate_and_normalize({
        "red_flags": ["too_vague", "invalid_flag", "generic_template_used"],
        "verdict": "weak",
    })
    assert set(p["red_flags"]) == {"too_vague", "generic_template_used"}


def test_effective_search_score():
    assert effective_search_score({"grounding": 2, "specificity": 2, "implementation_bias": 2}) == 6
    assert effective_search_score({"grounding": 3, "specificity": 3, "implementation_bias": 3}) == 9


def test_is_weak_or_bad():
    assert is_weak_or_bad({"verdict": "weak"}) is True
    assert is_weak_or_bad({"verdict": "bad"}) is True
    assert is_weak_or_bad({"verdict": "acceptable"}) is False
    assert is_weak_or_bad({"verdict": "excellent"}) is False


def test_aggregate_audit_results_empty():
    agg = aggregate_audit_results([])
    assert agg["total_searches"] == 0
    assert agg["bad_or_weak_rate"] == 0.0
    assert agg["sample_bad"] == []


def test_aggregate_audit_results():
    records = [
        {"verdict": "excellent", "effective_search": 8, "red_flags": [], "is_weak_or_bad": False},
        {"verdict": "weak", "effective_search": 4, "red_flags": ["too_vague"], "is_weak_or_bad": True},
        {"verdict": "acceptable", "effective_search": 6, "red_flags": [], "is_weak_or_bad": False},
    ]
    agg = aggregate_audit_results(records)
    assert agg["total_searches"] == 3
    assert agg["bad_or_weak_rate"] == pytest.approx(1 / 3)
    assert agg["effective_search_avg"] == pytest.approx(6.0)
    assert agg["red_flag_counts"] == {"too_vague": 1}
    assert len(agg["sample_bad"]) == 1
