from __future__ import annotations

import logging

from agent_v2.schemas.exploration import (
    CoverageSignal,
    ExplorationControl,
    ExplorationControlAction,
    RelationshipHint,
    SelectorBatchResult,
    UnderstandingResult,
)

_LOG = logging.getLogger(__name__)


class EngineDecisionMapper:
    """Central control authority: STOP | EXPAND | REFINE only."""

    @staticmethod
    def decide_control(
        understanding: UnderstandingResult,
        selector: SelectorBatchResult,
        relationship_hint: RelationshipHint,
        *,
        expanded_once: bool,
        refine_count: int,
        refine_limit: int,
    ) -> ExplorationControl:
        """
        Mandatory predicate order (exploration_refactor_plan.md §1).

        Intentional: is_sufficient=False + coverage_signal=good → final STOP.
        Bounded exploration; planner may continue from gaps/partial understanding.
        """
        if understanding.effective_sufficient:
            return ExplorationControl(action="stop", reason="analyzer_sufficient")

        if relationship_hint != "none" and not expanded_once:
            return ExplorationControl(
                action="expand",
                reason=f"relationship_hint={relationship_hint}",
            )

        cov: CoverageSignal = selector.coverage_signal
        if cov in ("weak", "fragmented", "empty") and refine_count < refine_limit:
            return ExplorationControl(
                action="refine",
                reason=f"coverage_signal={cov}",
            )

        # Intentional: is_sufficient=False + coverage_signal=good → STOP.
        # Bounded exploration; planner may continue from gaps/partial understanding.
        return ExplorationControl(action="stop", reason="mapper_default_stop")

    @staticmethod
    def to_exploration_decision(understanding: UnderstandingResult) -> "ExplorationDecision":
        """Deprecated: legacy mapping for tests/harnesses only."""
        from agent_v2.schemas.exploration import ExplorationDecision

        if understanding.effective_sufficient:
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
