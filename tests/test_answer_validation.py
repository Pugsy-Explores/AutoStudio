from __future__ import annotations

from unittest.mock import MagicMock, patch

from agent_v2.schemas.answer_synthesis import AnswerSynthesisResult
from agent_v2.schemas.exploration import ExplorationResultMetadata, ExplorationSummary
from agent_v2.schemas.final_exploration import ExplorationAdapterTrace, FinalExplorationSchema
from agent_v2.schemas.answer_validation import AnswerValidationResult as AVR
from agent_v2.schemas.init import AnswerValidationResult as AVRInit
from agent_v2.schemas.planner_decision import PlannerDecision
from agent_v2.validation.answer_validator import validate_answer
from agent_v2.runtime.planner_task_runtime import (
    build_explore_query_after_validation_failure,
    _validation_feedback_from_state,
)


def _exploration(*, confidence: str, gaps: list[str]) -> FinalExplorationSchema:
    summ = ExplorationSummary(
        overall="summary",
        key_findings=["k"],
        knowledge_gaps=gaps,
        knowledge_gaps_empty_reason=None if gaps else "none",
    )
    md = ExplorationResultMetadata(total_items=1, created_at="2026-01-01T00:00:00Z")
    return FinalExplorationSchema(
        exploration_id="e1",
        instruction="do the thing",
        status="complete",
        evidence=[],
        relationships=[],
        exploration_summary=summ,
        metadata=md,
        confidence=confidence,  # type: ignore[arg-type]
        trace=ExplorationAdapterTrace(llm_used=False, synthesis_success=True),
    )


def test_validate_answer_complete():
    fe = _exploration(confidence="high", gaps=[])
    syn = AnswerSynthesisResult(
        synthesis_success=True,
        direct_answer="Done.",
        coverage="sufficient",
    )
    r = validate_answer(instruction="x", exploration=fe, synthesis=syn)
    assert r.is_complete is True
    assert r.issues == []
    assert r.confidence == "high"
    assert "deterministic validation passed" in r.validation_reason.lower()


def test_validate_answer_fails_on_gaps_and_weak_coverage():
    fe = _exploration(confidence="medium", gaps=["need callers"])
    syn = AnswerSynthesisResult(
        synthesis_success=True,
        direct_answer="Partial.",
        coverage="weak",
    )
    r = validate_answer(instruction="x", exploration=fe, synthesis=syn)
    assert r.is_complete is False
    assert "exploration_knowledge_gaps_present" in r.issues
    assert "synthesis_coverage_weak" in r.issues
    assert "need callers" in r.missing_context
    assert "need callers" in r.validation_reason or "weak synthesis coverage" in r.validation_reason.lower()


def test_validate_answer_fails_on_uncertainty():
    fe = _exploration(confidence="high", gaps=[])
    syn = AnswerSynthesisResult(
        synthesis_success=True,
        direct_answer="Maybe.",
        uncertainty="not sure",
        coverage="sufficient",
    )
    r = validate_answer(instruction="x", exploration=fe, synthesis=syn)
    assert r.is_complete is False
    assert any("uncertainty" in i for i in r.issues)
    assert "uncertainty" in r.validation_reason.lower()


def test_build_explore_query_after_validation_failure():
    vf = AVR(
        is_complete=False,
        issues=["a"],
        missing_context=["sym1", "sym2"],
    )
    q = build_explore_query_after_validation_failure(vf, "user wants foo", max_chars=100)
    assert "sym1" in q
    assert "sym2" in q

    q2 = build_explore_query_after_validation_failure(None, "fallback instr", max_chars=50)
    assert "fallback" in q2.lower() or "instr" in q2.lower()


def test_post_validation_blocked_coerces_synthesize_to_explore():
    """Mirrors ACT loop guard: blocked + synthesize -> explore with query from validation."""
    from types import SimpleNamespace

    state = SimpleNamespace(
        instruction="find callers",
        context={
            "validation_feedback": AVR(
                is_complete=False,
                issues=[],
                missing_context=["symX"],
            ).model_dump()
        },
        metadata={},
    )
    md = state.metadata
    md["post_validation_synthesize_blocked"] = True
    decision = PlannerDecision(type="synthesize", step=None, query=None, tool="synthesize")
    if md.get("post_validation_synthesize_blocked") and decision.type == "synthesize":
        vf_blk = _validation_feedback_from_state(state)
        q = build_explore_query_after_validation_failure(vf_blk, str(state.instruction))
        decision = PlannerDecision(type="explore", step=None, query=q, tool="explore")
    assert decision.type == "explore"
    assert decision.query and "symX" in decision.query


def test_schemas_init_reexports_answer_validation_result():
    assert AVRInit is AVR


def test_validate_answer_llm_merge_when_enabled():
    fe = _exploration(confidence="high", gaps=[])
    syn = AnswerSynthesisResult(
        synthesis_success=True,
        direct_answer="Done.",
        coverage="sufficient",
    )
    mock_loop = MagicMock()
    mock_loop.enable_answer_validation = True
    mock_loop.enable_answer_validation_llm = True
    mock_cfg = MagicMock()
    mock_cfg.planner_loop = mock_loop
    llm_json = (
        '{"is_complete": false, "issues": ["llm_gap"], '
        '"missing_context": ["verify Z"], "confidence": "low", '
        '"validation_reason": "Missing test coverage and one caller path"}'
    )
    with patch("agent_v2.validation.answer_validator.get_config", return_value=mock_cfg):
        with patch(
            "agent_v2.validation.answer_validator.call_reasoning_model",
            return_value=llm_json,
        ):
            r = validate_answer(instruction="x", exploration=fe, synthesis=syn)
    assert r.is_complete is False
    assert "llm_gap" in r.issues
    assert "verify Z" in r.missing_context
    assert "Missing test coverage and one caller path" in r.validation_reason


def test_validate_answer_llm_not_called_when_disabled():
    fe = _exploration(confidence="high", gaps=[])
    syn = AnswerSynthesisResult(
        synthesis_success=True,
        direct_answer="Done.",
        coverage="sufficient",
    )
    mock_loop = MagicMock()
    mock_loop.enable_answer_validation = True
    mock_loop.enable_answer_validation_llm = False
    mock_cfg = MagicMock()
    mock_cfg.planner_loop = mock_loop
    with patch("agent_v2.validation.answer_validator.get_config", return_value=mock_cfg):
        with patch("agent_v2.validation.answer_validator.call_reasoning_model") as m_call:
            r = validate_answer(instruction="x", exploration=fe, synthesis=syn)
    m_call.assert_not_called()
    assert r.is_complete is True
    assert r.validation_reason


def _answer_validation_prompt_variables() -> dict[str, str]:
    return {
        "instruction": "do the task",
        "rules_validation_json": "{}",
        "exploration_summary": "summary",
        "exploration_confidence": "high",
        "synthesis_direct_answer": "answer",
        "synthesis_structured_explanation": "expl",
        "synthesis_coverage": "sufficient",
        "synthesis_uncertainty": "none",
    }


def test_goin_2_57b_answer_validation_prompt_resolves_and_substitutes():
    from agent.prompt_system.loader import load_prompt, normalize_model_name_for_path

    assert normalize_model_name_for_path("GOIN 2.57B") == "goin_2.57b"
    t = load_prompt(
        "answer_validation",
        version="v1",
        variables=_answer_validation_prompt_variables(),
        model_name="GOIN 2.57B",
    )
    norm_path = t.source_path.replace("\\", "/").lower()
    assert "answer_validation/models/goin_2.57b/v1.yaml" in norm_path
    assert "VALIDATION PRINCIPLES" in t.system_prompt
    assert "STRICTNESS RULE" in t.system_prompt
    assert "Prefer false negatives over false positives" in t.system_prompt
    assert "validation_reason" in t.system_prompt
    assert "RULES PASS (baseline validation)" in t.user_prompt_template
    assert "do the task" in t.user_prompt_template
    assert "{{" not in t.system_prompt


def test_validate_answer_llm_path_uses_goin_prompt_when_model_name_goin():
    fe = _exploration(confidence="high", gaps=[])
    syn = AnswerSynthesisResult(
        synthesis_success=True,
        direct_answer="Done.",
        coverage="sufficient",
    )
    mock_loop = MagicMock()
    mock_loop.enable_answer_validation = True
    mock_loop.enable_answer_validation_llm = True
    mock_cfg = MagicMock()
    mock_cfg.planner_loop = mock_loop
    captured: dict[str, str | None] = {}

    def _capture(prompt: str, system_prompt: str | None = None, **_: object) -> str:
        captured["user"] = prompt
        captured["system"] = system_prompt
        return (
            '{"is_complete": true, "issues": [], "missing_context": [], '
            '"confidence": "high", "validation_reason": "Looks complete."}'
        )

    with patch("agent_v2.validation.answer_validator.get_config", return_value=mock_cfg):
        with patch(
            "agent_v2.validation.answer_validator.get_prompt_model_name_for_task",
            return_value="GOIN 2.57B",
        ):
            with patch(
                "agent_v2.validation.answer_validator.call_reasoning_model",
                side_effect=_capture,
            ):
                r = validate_answer(instruction="instr", exploration=fe, synthesis=syn)
    assert r.is_complete is True
    assert captured.get("system") and "VALIDATION PRINCIPLES" in captured["system"]
    assert captured.get("user") and "VALIDATION TASK" in captured["user"]
    assert "instr" in (captured.get("user") or "")
    assert r.validation_reason == "Looks complete."
