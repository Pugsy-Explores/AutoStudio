"""Unit tests for LLM-as-Judge (Phase 3)."""

import pytest

from tests.golden.judge import ENABLE_LLM_JUDGE, run_llm_judge


def test_run_llm_judge_returns_expected_shape():
    """run_llm_judge returns dict with passed, score, reason, confidence, disagreement."""
    test = {"input": {"instruction": "Explain X"}}
    result = {"signals": {"answer": "The system does Y."}}
    cfg = {"enabled": True}
    out = run_llm_judge(test, result, cfg)
    assert isinstance(out, dict)
    assert "passed" in out
    assert "score" in out
    assert "reason" in out
    assert "confidence" in out
    assert "disagreement" in out


def test_run_llm_judge_disabled_by_default():
    """When ENABLE_LLM_JUDGE is False, judge returns passed=True, reason=disabled."""
    if ENABLE_LLM_JUDGE:
        pytest.skip("ENABLE_LLM_JUDGE is set; cannot test disabled path")
    test = {"input": {"instruction": "Explain X"}}
    result = {"signals": {"answer": "Some answer"}}
    cfg = {"enabled": True}
    out = run_llm_judge(test, result, cfg)
    assert out["passed"] is True
    assert out["reason"] == "disabled"
