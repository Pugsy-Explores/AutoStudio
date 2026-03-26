from __future__ import annotations

from agent_v2.schemas.exploration import ExplorationDecision, UnderstandingResult


class EngineDecisionMapper:
    """Deterministic mapper from understanding to engine action contract."""

    def to_exploration_decision(self, understanding: UnderstandingResult) -> ExplorationDecision:
        if understanding.sufficient or understanding.evidence_sufficiency == "sufficient":
            status = "sufficient"
            needs: list[str] = []
            next_action = "stop"
        elif understanding.relevance == "low":
            status = "wrong_target"
            needs = ["different_symbol"]
            next_action = "refine"
        else:
            status = "partial"
            needs = ["more_code"]
            next_action = "expand" if understanding.relevance == "high" else "stop"
        reason = understanding.summary or f"relevance={understanding.relevance}, confidence={understanding.confidence:.2f}"
        return ExplorationDecision(
            status=status,
            needs=needs,
            reason=reason,
            next_action=next_action,
        )
