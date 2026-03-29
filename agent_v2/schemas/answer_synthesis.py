"""
V1 contract for AnswerSynthesizer (post-exploration, pre-planner).

Coverage derivation rules are documented on :func:`derive_answer_synthesis_coverage`.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from agent_v2.schemas.exploration import ExplorationItem, QueryIntent
from agent_v2.schemas.final_exploration import ExplorationRelationshipEdge, FinalExplorationSchema

AnswerSynthesisCoverage = Literal["sufficient", "partial", "weak"]


class AnswerSynthesisInput(BaseModel):
    """Minimum viable payload for synthesis; exploration carries evidence/relationships/gaps."""

    instruction: str
    exploration: FinalExplorationSchema
    coverage: AnswerSynthesisCoverage
    evidence: list[ExplorationItem] = Field(default_factory=list)
    relationships: list[ExplorationRelationshipEdge] = Field(default_factory=list)
    knowledge_gaps: list[str] = Field(default_factory=list)
    confidence: str = ""
    query_intent: Optional[QueryIntent] = None

    @classmethod
    def from_exploration(cls, exploration: FinalExplorationSchema) -> "AnswerSynthesisInput":
        cov = derive_answer_synthesis_coverage(exploration)
        gaps = list(exploration.exploration_summary.knowledge_gaps or [])
        return cls(
            instruction=exploration.instruction,
            exploration=exploration,
            coverage=cov,
            evidence=list(exploration.evidence or []),
            relationships=list(exploration.relationships or []),
            knowledge_gaps=gaps,
            confidence=str(exploration.confidence or ""),
            query_intent=exploration.query_intent,
        )


class CitationRef(BaseModel):
    item_id: str = ""
    file: str = ""
    symbol: str = ""


class AnswerSynthesisResult(BaseModel):
    """User-facing synthesis output (sectioned text from LLM + metadata)."""

    direct_answer: str = ""
    structured_explanation: str = ""
    citations: list[CitationRef] = Field(default_factory=list)
    uncertainty: Optional[str] = None
    stated_confidence: Optional[str] = None
    coverage: AnswerSynthesisCoverage = "weak"
    synthesis_success: bool = True
    error: Optional[str] = None


def derive_answer_synthesis_coverage(exploration: FinalExplorationSchema) -> AnswerSynthesisCoverage:
    """
    Deterministic coverage for the synthesizer prompt (``sufficient`` | ``partial`` | ``weak``).

    **weak** — exploration is a poor basis for a confident user answer:
      - no evidence rows, or
      - planner confidence band is ``low``, or
      - termination indicates search/loop failure:
        ``stalled``, ``no_relevant_candidate``, ``pending_exhausted``, or
      - run marked incomplete with at most one evidence row, or
      - ``unknown`` termination with at most one evidence row.

    **partial** — some signal but incomplete:
      - non-empty knowledge gaps, or
      - confidence ``medium``, or
      - metadata ``completion_status`` is ``incomplete``, or
      - ``unknown`` termination (with enough evidence to avoid ``weak``), or
      - ``policy_violation_full_read`` termination.

    **sufficient** — otherwise (complete exploration, high confidence, no gaps, strong termination).
    """
    md = exploration.metadata
    tr = (md.termination_reason or "unknown").strip().lower()
    gaps = [g for g in (exploration.exploration_summary.knowledge_gaps or []) if str(g).strip()]
    n_ev = len(exploration.evidence or [])
    conf = exploration.confidence
    complete = (md.completion_status or "incomplete") == "complete"

    weak_tr = frozenset({"stalled", "no_relevant_candidate", "pending_exhausted"})

    if n_ev == 0:
        return "weak"
    if conf == "low":
        return "weak"
    if tr in weak_tr:
        return "weak"
    if not complete and n_ev <= 1:
        return "weak"
    if tr == "unknown" and n_ev <= 1:
        return "weak"

    if gaps:
        return "partial"
    if conf == "medium":
        return "partial"
    if not complete:
        return "partial"
    if tr == "unknown":
        return "partial"
    if tr == "policy_violation_full_read":
        return "partial"

    return "sufficient"
