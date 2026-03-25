"""
Exploration schemas — Schema 4 (ExplorationResult, ExplorationItem).

Foundation of planning quality: strict, source-grounded, LLM-consumable.
No raw data dumps — only distilled, structured content.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, model_validator


class ExplorationSource(BaseModel):
    ref: str
    location: Optional[str] = None


class ExplorationContent(BaseModel):
    summary: str
    key_points: list[str]
    entities: list[str]


class ExplorationRelevance(BaseModel):
    score: float
    reason: str


class ExplorationItemMetadata(BaseModel):
    timestamp: str
    tool_name: str


class ExplorationItem(BaseModel):
    item_id: str
    type: Literal["file", "search", "command", "other"]
    source: ExplorationSource
    content: ExplorationContent
    relevance: ExplorationRelevance
    metadata: ExplorationItemMetadata


class ExplorationSummary(BaseModel):
    """
    knowledge_gaps_empty_reason is REQUIRED (non-empty) when knowledge_gaps is empty.
    knowledge_gaps_empty_reason MUST be null when knowledge_gaps is non-empty.
    """
    overall: str
    key_findings: list[str]
    knowledge_gaps: list[str]
    knowledge_gaps_empty_reason: Optional[str] = None

    @model_validator(mode="after")
    def validate_knowledge_gaps_consistency(self) -> "ExplorationSummary":
        if not self.knowledge_gaps and not self.knowledge_gaps_empty_reason:
            raise ValueError(
                "knowledge_gaps_empty_reason must be a non-empty string when knowledge_gaps is empty"
            )
        if self.knowledge_gaps and self.knowledge_gaps_empty_reason is not None:
            raise ValueError(
                "knowledge_gaps_empty_reason must be null when knowledge_gaps is non-empty"
            )
        return self


class ExplorationResultMetadata(BaseModel):
    total_items: int
    created_at: str


class ExplorationResult(BaseModel):
    """
    Schema 4 — authoritative normative type from SCHEMAS.md.
    Exploration is bounded (≤ 6 items). Items must have summary + key_points.
    Sources must be real (no hallucinated refs).
    """
    exploration_id: str
    instruction: str
    items: list[ExplorationItem]
    summary: ExplorationSummary
    metadata: ExplorationResultMetadata
