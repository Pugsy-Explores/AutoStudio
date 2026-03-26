"""
Exploration schemas — Schema 4 (ExplorationResult, ExplorationItem).

Foundation of planning quality: strict, source-grounded, LLM-consumable.
No raw data dumps — only distilled, structured content.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

ExpandDirectionHint = Literal["callers", "callees", "both"]


FailureReason = Literal[
    "no_results",
    "low_relevance",
    "too_broad",
    "too_narrow",
    "wrong_abstraction",
    "ambiguous_intent",
    "missing_symbol_signal",
]


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
    # Optional regex-style patterns (same SEARCH tool path as text; labeled grep in discovery mapping)
    regex_patterns: list[str] = Field(default_factory=list)
    intents: list[str] = Field(default_factory=list)


class ReadPacket(BaseModel):
    file_path: str
    symbol: Optional[str] = None
    read_source: Optional[Literal["symbol", "line", "head"]] = None
    content: str = ""
    line_start: int = 1
    line_end: int = 1
    char_count: int = 0
    line_count: int = 0
    call_chain_id: Optional[str] = None


class LineRangeSignal(BaseModel):
    start: int = Field(ge=1)
    end: int = Field(ge=1)
    reason: str

    @model_validator(mode="after")
    def validate_order(self) -> "LineRangeSignal":
        if self.end < self.start:
            raise ValueError("line range end must be >= start")
        return self


class InspectionSignals(BaseModel):
    line_ranges: list[LineRangeSignal] = Field(default_factory=list)
    symbols: list[str] = Field(default_factory=list)
    relationships: list[str] = Field(default_factory=list)


class ContextBlock(BaseModel):
    file_path: str
    start: int = Field(ge=1)
    end: int = Field(ge=1)
    content: str = ""
    origin_reason: str = ""
    symbol: Optional[str] = None
    relationship_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_order(self) -> "ContextBlock":
        if self.end < self.start:
            raise ValueError("context block end must be >= start")
        return self


class UnderstandingResult(BaseModel):
    relevance: Literal["high", "medium", "low"] = "medium"
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    sufficient: bool = False
    evidence_sufficiency: Literal["insufficient", "partial", "sufficient"] = "partial"
    knowledge_gaps: list[str] = Field(default_factory=list)
    summary: str = ""


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
    # When status is wrong_target: optional explicit scope — only "file" adds to excluded_paths (analyzer JSON).
    wrong_target_scope: Optional[Literal["file"]] = None


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
    # (canonical_path, symbol) already inspected — used to filter re-discovery / refine queues
    explored_location_keys: set[tuple[str, str]] = Field(default_factory=set)
    # Canonical paths excluded from discovery/enqueue when analyzer sets wrong_target_scope=file
    excluded_paths: set[str] = Field(default_factory=set)
    seen_symbols: set[str] = Field(default_factory=set)
    expanded_symbols: set[str] = Field(default_factory=set)
    findings: list[dict] = Field(default_factory=list)
    steps_taken: int = 0
    backtracks: int = 0
    primary_symbol: Optional[str] = None
    relationships_found: bool = False
    last_decision: Optional[str] = None
    refine_used_last_step: bool = False
    no_improvement_streak: int = 0
    last_improvement_signature: Optional[tuple[bool, str, int]] = None
    attempted_gaps: set[str] = Field(default_factory=set)
    seen_relation_edges: set[str] = Field(default_factory=set)
    gap_expand_attempts: int = 0
    gap_expand_successes: int = 0
    last_expand_was_gap_driven: bool = False
    # Directed expansion / multi-hop depth (additive; backward compatible defaults)
    expansion_depth: int = 0
    expand_direction_hint: Optional[ExpandDirectionHint] = None
    # (normalized gap bundle key, canonical file, symbol) — avoid repeat gap→target expansion
    attempted_gap_targets: set[tuple[str, str, str]] = Field(default_factory=set)
    # Merged into discovery text queries for refine-only gap paths (engine-local; parser unchanged)
    discovery_keyword_inject: list[str] = Field(default_factory=list)
    # Key for attempted_gap_targets and prefilter (set during gap-driven decision; cleared after expand)
    gap_bundle_key_for_expansion: str = ""
