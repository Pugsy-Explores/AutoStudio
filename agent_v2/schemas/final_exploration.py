"""
Planner-facing exploration output (hybrid adapter).

Schema: Docs/architecture_freeze/EXPLORATION_RESULT_ADAPTER_HYBRID.md
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from agent_v2.schemas.exploration import (
    ExplorationItem,
    ExplorationResultMetadata,
    ExplorationSummary,
    QueryIntent,
)

ExplorationConfidenceBand = Literal["high", "medium", "low"]
ExplorationRelationshipType = Literal["callers", "callees", "related"]


class ExplorationRelationshipEdge(BaseModel):
    """Directed edge from working memory (canonical graph only)."""

    from_key: str = Field(description="memory row 'from'")
    to_key: str = Field(description="memory row 'to'")
    type: ExplorationRelationshipType
    confidence: float = Field(ge=0.0, le=1.0, default=0.85)
    source: str = "expansion"


class ExplorationAdapterTrace(BaseModel):
    """Minimal structured trace (no log dumps)."""

    llm_used: bool
    synthesis_success: bool
    adapter_version: str = "v1"


class FinalExplorationSchema(BaseModel):
    """
    **Planner-facing contract** for exploration output (single source of truth).

    Downstream stages (planner, mode manager, prompts) must consume this type — not
    ``ExplorationResult``. Evidence rows are ``ExplorationItem`` (same shape as historical
    Schema 4 ``items``); use field ``evidence`` and ``exploration_summary``.
    """

    exploration_id: str
    instruction: str
    status: Literal["complete", "incomplete"]
    evidence: list[ExplorationItem]
    relationships: list[ExplorationRelationshipEdge]
    exploration_summary: ExplorationSummary
    metadata: ExplorationResultMetadata
    key_insights: list[str] = Field(default_factory=list)
    objective_coverage: Optional[str] = None
    confidence: ExplorationConfidenceBand
    trace: ExplorationAdapterTrace
    # Read-only mirror of ``state.context["query_intent"]`` at adapter build time (transport).
    query_intent: Optional[QueryIntent] = None

    @field_validator("key_insights")
    @classmethod
    def _cap_insights(cls, v: list[str]) -> list[str]:
        return [str(x).strip()[:800] for x in v[:4] if str(x).strip()]
