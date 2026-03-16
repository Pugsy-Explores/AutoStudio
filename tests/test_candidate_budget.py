"""Tests for candidate budget (MAX_RERANK_CANDIDATES) in retrieval pipeline."""

import pytest

from config.retrieval_config import MAX_RERANK_CANDIDATES


def test_max_rerank_candidates_config():
    """MAX_RERANK_CANDIDATES is defined and defaults to 50."""
    assert MAX_RERANK_CANDIDATES >= 1
    assert MAX_RERANK_CANDIDATES == 50  # default from plan


def test_candidate_budget_slice_logic():
    """Verify slice candidates[:MAX_RERANK_CANDIDATES] behavior."""
    candidates = [{"snippet": f"x{i}"} for i in range(100)]
    budgeted = candidates[:MAX_RERANK_CANDIDATES]
    assert len(budgeted) == MAX_RERANK_CANDIDATES
    candidate_budget_applied = 100 - len(budgeted)
    assert candidate_budget_applied == 50


def test_candidate_budget_no_trim_when_under_cap():
    """When count <= MAX_RERANK_CANDIDATES, no trimming."""
    candidates = [{"snippet": "a"}, {"snippet": "b"}]
    budgeted = candidates[:MAX_RERANK_CANDIDATES]
    assert len(budgeted) == 2
    assert len(budgeted) - len(candidates) == 0
