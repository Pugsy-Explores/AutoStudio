from unittest.mock import patch

from agent.meta.llm_evaluator import LLMEvaluator


def test_llm_evaluator_success_parsing():
    evaluator = LLMEvaluator()
    with patch("agent.meta.llm_evaluator.call_reasoning_model", return_value='{"is_success": true, "confidence": 0.9, "reason": "done"}'):
        out = evaluator.evaluate("Do X", {"steps": []}, [])
    assert out["is_success"] is True
    assert out["confidence"] == 0.9
    assert out["reason"] == "done"
    assert out["error"] == ""


def test_llm_evaluator_failure_parsing():
    evaluator = LLMEvaluator()
    with patch("agent.meta.llm_evaluator.call_reasoning_model", return_value="not json"):
        out = evaluator.evaluate("Do X", {"steps": []}, [])
    assert out == {"is_success": None, "confidence": 0.0, "reason": "", "error": "parse_error"}


def test_llm_evaluator_confidence_value_exposed():
    evaluator = LLMEvaluator()
    with patch("agent.meta.llm_evaluator.call_reasoning_model", return_value='{"is_success": true, "confidence": 0.8, "reason": "ok"}'):
        out = evaluator.evaluate("Do X", {"steps": []}, [])
    assert out["confidence"] == 0.8

