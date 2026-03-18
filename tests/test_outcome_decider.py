from agent.orchestrator.outcome_decider import OutcomeDecider


def test_success_decision():
    decider = OutcomeDecider()
    decision = decider.decide(
        goal_met=True,
        signals={"has_successful_step": False},
        llm_eval={"is_success": False, "confidence": 0.0},
        errors=[],
        attempt=0,
        max_attempts=3,
    )
    assert decision["status"] == "SUCCESS"
    assert decision["reason"] == "goal_met"


def test_success_llm_override():
    decider = OutcomeDecider()
    decision = decider.decide(
        goal_met=False,
        signals={"has_successful_step": True},
        llm_eval={"is_success": True, "confidence": 0.7},
        errors=[],
        attempt=0,
        max_attempts=3,
    )
    assert decision["status"] == "SUCCESS"
    assert decision["reason"] == "hybrid_success"


def test_success_fallback_to_signals_when_llm_invalid():
    decider = OutcomeDecider()
    decision = decider.decide(
        goal_met=False,
        signals={"has_successful_step": True},
        llm_eval={"is_success": None, "confidence": 0.0, "error": "parse_error"},
        errors=[],
        attempt=0,
        max_attempts=3,
    )
    assert decision["status"] == "SUCCESS"
    assert decision["reason"] == "signals_success"


def test_retry_no_execution_success():
    decider = OutcomeDecider()
    decision = decider.decide(
        goal_met=False,
        signals={"has_successful_step": False},
        llm_eval={"is_success": False, "confidence": 0.0},
        errors=[],
        attempt=0,
        max_attempts=3,
    )
    assert decision["status"] == "RETRY"
    assert decision["reason"] == "no_execution_success"


def test_fail_on_max_attempts():
    decider = OutcomeDecider()
    decision = decider.decide(
        goal_met=False,
        signals={"has_successful_step": True},
        llm_eval={"is_success": False, "confidence": 0.0},
        errors=["boom"],
        attempt=2,
        max_attempts=3,
    )
    assert decision["status"] == "FAIL"
    assert decision["reason"] == "max_attempts_exceeded"

