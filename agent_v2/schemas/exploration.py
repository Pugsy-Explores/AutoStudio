"""
Exploration schemas — Schema 4 (ExplorationResult, ExplorationItem).

Foundation of planning quality: strict, source-grounded, LLM-consumable.
No raw data dumps — only distilled, structured content.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


class ExplorationSource(BaseModel):
    ref: str
    location: Optional[str] = None


class ExplorationContent(BaseModel):
    summary: str
    key_points: list[str]
    entities: list[str]


class ExplorationRelevance(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
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
    # Phase 12.6.E (additive, zero-heuristic): bounded excerpt + deterministic origin
    snippet: str = ""
    read_source: Optional[Literal["symbol", "line", "head"]] = None


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
    completion_status: Literal["complete", "incomplete"] = "incomplete"
    termination_reason: str = "unknown"
    explored_files: int = 0
    explored_symbols: int = 0
    # Phase 12.6.E (additive): structural counts only (no ranking/quality)
    source_summary: dict[str, int] = Field(default_factory=dict)


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

    @model_validator(mode="after")
    def validate_item_bounds(self) -> "ExplorationResult":
        if len(self.items) > 6:
            raise ValueError("ExplorationResult.items must contain at most 6 entries")
        if self.metadata.total_items != len(self.items):
            raise ValueError("metadata.total_items must equal len(items)")
        return self


class QueryIntent(BaseModel):
    symbols: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    intents: list[str] = Field(default_factory=list)


class ExplorationCandidate(BaseModel):
    symbol: Optional[str] = None
    file_path: str
    snippet: Optional[str] = None
    source: Literal["graph", "grep", "vector"]


class ExplorationDecision(BaseModel):
    status: Literal["wrong_target", "partial", "sufficient"]
    needs: list[
        Literal["more_code", "callers", "callees", "definition", "different_symbol"]
    ] = Field(default_factory=list)
    reason: str
    next_action: Optional[Literal["expand", "refine", "stop"]] = None


class ExplorationTarget(BaseModel):
    file_path: str
    symbol: Optional[str] = None
    line: Optional[int] = None
    source: Literal["discovery", "expansion"]


class GraphExpansionResult(BaseModel):
    callers: list[ExplorationTarget] = Field(default_factory=list)
    callees: list[ExplorationTarget] = Field(default_factory=list)
    related: list[ExplorationTarget] = Field(default_factory=list)


class ExplorationState(BaseModel):
    instruction: str
    pending_targets: list[ExplorationTarget] = Field(default_factory=list)
    current_target: Optional[ExplorationTarget] = None
    seen_files: set[str] = Field(default_factory=set)
    seen_symbols: set[str] = Field(default_factory=set)
    expanded_symbols: set[str] = Field(default_factory=set)
    findings: list[dict] = Field(default_factory=list)
    steps_taken: int = 0
    backtracks: int = 0
    primary_symbol: Optional[str] = None
    relationships_found: bool = False
    last_decision: Optional[str] = None
