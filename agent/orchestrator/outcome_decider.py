from typing import Literal, TypedDict


DecisionStatus = Literal["SUCCESS", "RETRY", "FAIL"]


class Decision(TypedDict):
    status: DecisionStatus
    reason: str


class OutcomeDecider:
    """
    Single authority for deciding whether to:
    - stop (SUCCESS)
    - retry (RETRY)
    - terminate (FAIL)

    GoalEvaluator = primary signal
    Critic = advisory only
    """

    def decide(
        self,
        *,
        goal_met: bool,
        signals: dict,
        llm_eval: dict,
        errors: list[str],
        attempt: int,
        max_attempts: int,
    ) -> Decision:
        llm_valid = llm_eval.get("is_success") is not None

        # 1. Hard success
        if goal_met:
            return {"status": "SUCCESS", "reason": "goal_met"}

        # 2. Hybrid success (ignore invalid LLM signals)
        if llm_valid and llm_eval["is_success"] and llm_eval["confidence"] >= 0.7:
            if signals["has_successful_step"]:
                return {"status": "SUCCESS", "reason": "hybrid_success"}

        # 3. Fallback to deterministic signals when LLM fails
        if not llm_valid:
            if signals["has_successful_step"]:
                return {"status": "SUCCESS", "reason": "signals_success"}

        # 4. Hard failure
        if attempt >= max_attempts - 1:
            return {"status": "FAIL", "reason": "max_attempts_exceeded"}

        # 5. No progress detection (signal-based, NOT heuristic)
        if not signals["has_successful_step"]:
            return {"status": "RETRY", "reason": "no_execution_success"}

        # 6. Default retry
        return {"status": "RETRY", "reason": "goal_not_met"}
