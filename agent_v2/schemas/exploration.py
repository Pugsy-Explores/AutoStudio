"""
Exploration schemas — Schema 4 (ExplorationResult, ExplorationItem).

Foundation of planning quality: strict, source-grounded, LLM-consumable.
No raw data dumps — only distilled, structured content.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator

SourceChannel = Literal["graph", "grep", "vector"]

ExpandDirectionHint = Literal["callers", "callees", "both"]

RelationshipHint = Literal["none", "callers", "callees", "both"]

IntentTaskType = Literal["explanation", "debugging", "navigation", "modification"]
IntentScopeLevel = Literal["narrow", "component", "system"]
IntentFocusKind = Literal["internal_logic", "relationships", "usage"]

# Canonical store: agent ``state.context[QUERY_INTENT_CONTEXT_KEY]`` (QueryIntent).
QUERY_INTENT_CONTEXT_KEY = "query_intent"

SelectionConfidence = Literal["high", "medium", "low"]

CoverageSignal = Literal["good", "weak", "fragmented", "empty", "unknown"]

ExplorationControlAction = Literal["stop", "expand", "refine"]


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


# Planner advisory cap only (intent-aligned budget); not enforced in engine control flow.
EXPLORATION_BUDGET_GLOBAL_CAP: int = 3


class ExplorationResultMetadata(BaseModel):
    total_items: int
    created_at: str
    completion_status: Literal["complete", "incomplete"] = "incomplete"
    termination_reason: str = "unknown"
    explored_files: int = 0
    explored_symbols: int = 0
    # Inner-loop iterations for this explore() run (for planner cost visibility).
    engine_loop_steps: int = 0
    # Phase 12.6.E (additive): structural counts only (no ranking/quality)
    source_summary: dict[str, int] = Field(default_factory=dict)


class ExplorationResult(BaseModel):
    """
    Legacy Schema 4 bundle (items + summary + metadata).

    **Planner contract:** use ``FinalExplorationSchema`` from
    ``agent_v2/schemas/final_exploration.py`` — produced by ``ExplorationEngineV2.explore()``.
    ``ExplorationResult`` is only constructed inside ``agent_v2/exploration`` (e.g. legacy
    runner glue) and must not be imported by planner / downstream stages.
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
    relationship_hint: RelationshipHint = "none"
    # User task (from instruction only in parser prompt; sticky on refine via merge)
    intent_type: Optional[IntentTaskType] = None
    target: Optional[str] = None
    scope: Optional[IntentScopeLevel] = None
    focus: Optional[IntentFocusKind] = None

    def has_meaningful_queries(self) -> bool:
        """True when at least one non-empty symbol, keyword, or regex pattern is present."""
        if any(str(x).strip() for x in (self.symbols or [])):
            return True
        if any(str(x).strip() for x in (self.keywords or [])):
            return True
        if any(str(x).strip() for x in (self.regex_patterns or [])):
            return True
        return False


def read_query_intent_from_agent_state(state: Any) -> Optional[QueryIntent]:
    """Read canonical query intent from ``state.context`` (single source of truth)."""
    ctx = getattr(state, "context", None)
    if not isinstance(ctx, dict):
        return None
    raw = ctx.get(QUERY_INTENT_CONTEXT_KEY)
    if raw is None:
        return None
    if isinstance(raw, QueryIntent):
        return raw
    if isinstance(raw, dict):
        try:
            return QueryIntent.model_validate(raw)
        except Exception:
            return None
    return None


def write_query_intent_to_agent_state(state: Any, qi: QueryIntent) -> None:
    """Persist intent to ``state.context``; no-op if context is not a dict."""
    ctx = getattr(state, "context", None)
    if isinstance(ctx, dict):
        ctx[QUERY_INTENT_CONTEXT_KEY] = qi


def _intent_budget_raw_units(qi: Optional[QueryIntent]) -> int:
    """
    Deterministic advisory units before global cap.

    Order: intent_type → focus (relationships = explanation tier) → relationship_hint → default explanation.
    """
    if qi is None:
        return 2
    if qi.intent_type:
        t = qi.intent_type
        if t == "navigation":
            return 1
        if t == "explanation":
            return 2
        if t == "debugging":
            return 3
        if t == "modification":
            return 2
        return 2
    if qi.focus:
        return 2
    rh = qi.relationship_hint
    if rh is not None and str(rh) != "none":
        return 2
    return 2


def effective_exploration_budget(qi: Optional[QueryIntent]) -> int:
    """Advisory planner budget: min(intent-derived units, EXPLORATION_BUDGET_GLOBAL_CAP)."""
    return min(_intent_budget_raw_units(qi), EXPLORATION_BUDGET_GLOBAL_CAP)


def task_intent_summary_for_analyzer(qi: QueryIntent, instruction: str) -> str:
    """Deterministic summary for analyzer prompt (formatting only)."""
    parts: list[str] = []
    if qi.intent_type:
        parts.append(f"type={qi.intent_type}")
    if qi.target and str(qi.target).strip():
        parts.append(f"target={qi.target.strip()}")
    if qi.scope:
        parts.append(f"scope={qi.scope}")
    if qi.focus:
        parts.append(f"focus={qi.focus}")
    head = "; ".join(parts) if parts else "(task fields not set by query-intent parser)"
    instr = (instruction or "").strip()
    return f"{head}\ninstruction: {instr[:2000]}".strip()


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
    """Semantic interpretation only (no control flow). Legacy fields kept for adapters."""

    relevance: Literal["high", "medium", "low"] = "medium"
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    sufficient: bool = False
    evidence_sufficiency: Literal["insufficient", "partial", "sufficient"] = "partial"
    knowledge_gaps: list[str] = Field(default_factory=list)
    summary: str = ""
    # Refactor fields (prompt JSON)
    semantic_understanding: str = ""
    relevant_files: list[str] = Field(default_factory=list)
    relationship_strings: list[str] = Field(default_factory=list)
    confidence_label: Literal["high", "medium", "low"] = "medium"
    is_sufficient: bool = False
    gaps_relevant_to_intent: list[str] = Field(default_factory=list)

    @property
    def effective_sufficient(self) -> bool:
        return self.is_sufficient or self.sufficient or self.evidence_sufficiency == "sufficient"


class ExplorationCandidate(BaseModel):
    symbol: Optional[str] = None
    file_path: str
    snippet: Optional[str] = None
    source: Literal["graph", "grep", "vector"]
    symbols: list[str] = Field(default_factory=list)
    snippet_summary: Optional[str] = None
    source_channels: list[SourceChannel] = Field(default_factory=list)
    discovery_max_score: Optional[float] = None
    discovery_rerank_score: Optional[float] = None
    # Which configured exploration test repo this file belongs to (multi-root eval); None if unknown.
    repo: Optional[str] = None

    @model_validator(mode="after")
    def _sync_legacy_fields(self) -> "ExplorationCandidate":
        if self.symbols and not self.symbol:
            self.symbol = self.symbols[0]
        if not self.source_channels and self.source:
            self.source_channels = [self.source]
        return self


class ExplorationControl(BaseModel):
    """Single control output from EngineDecisionMapper."""

    action: ExplorationControlAction
    reason: str = ""


class ExplorationDecision(BaseModel):
    """Legacy structured decision; prefer ExplorationControl for new orchestration."""

    status: Literal["wrong_target", "partial", "sufficient"]
    needs: list[
        Literal["more_code", "callers", "callees", "definition", "different_symbol"]
    ] = Field(default_factory=list)
    reason: str
    next_action: Optional[Literal["expand", "refine", "stop"]] = None
    # When status is wrong_target: optional explicit scope — only "file" adds to excluded_paths (analyzer JSON).
    wrong_target_scope: Optional[Literal["file"]] = None


class SelectorBatchResult(BaseModel):
    """Signal provider output (not control)."""

    selected_candidates: list["ExplorationCandidate"] = Field(default_factory=list)
    selection_confidence: SelectionConfidence = "medium"
    coverage_signal: CoverageSignal = "good"
    # Symbol picks keyed by batch payload index ("0".."n-1") into candidates[:SELECTOR_TOP_K].
    # Sole source of truth for selector-chosen symbols; never copy onto ExplorationCandidate.
    selected_symbols: dict[str, list[str]] = Field(default_factory=dict)
    # Parallel to selected_candidates: top-row index used for selected_symbols lookup.
    selected_top_indices: list[int] = Field(default_factory=list)


class ScopedCandidatesResult(BaseModel):
    """Scoper contract wrapper."""

    scoped_candidates: list["ExplorationCandidate"] = Field(default_factory=list)


class ExplorationTarget(BaseModel):
    file_path: str
    symbol: Optional[str] = None
    line: Optional[int] = None
    source: Literal["discovery", "expansion"]
    # Selector output for the discovery batch that enqueued this target (not used for graph expansion).
    selector_batch: Optional[SelectorBatchResult] = None
    # Index into the selector batch top slice (candidates[:SELECTOR_TOP_K]) for selected_symbols[str(i)].
    selector_top_index: Optional[int] = None


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
    # Merged into discovery text queries only during REFINE-phase discovery (mapper-approved).
    discovery_keyword_inject: list[str] = Field(default_factory=list)
    # Key for attempted_gap_targets and prefilter (legacy expansion bookkeeping)
    gap_bundle_key_for_expansion: str = ""
    # Refactor: mapper-driven counters
    expanded_once: bool = False
    refine_count: int = 0
    # Intent bootstrap passes before the main loop (re-parse + discovery when queue empty).
    # Not EngineDecisionMapper REFINE; does not consume refine_count.
    intent_bootstrap_pass_count: int = 0
    last_selector_batch: Optional[SelectorBatchResult] = None
