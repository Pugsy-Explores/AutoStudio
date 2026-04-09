"""
Plan schemas — Schema 1 (PlanDocument) and Schema 2 (PlanStep).

PlanDocument is the planner-owned control plane. PlanStep is immutable at execution time;
runtime lives on ExecutionTask (see execution_task.py) after compile_plan_document.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

# Phase 1: search_web omitted (disabled in prompt + schema). No analyze_code — dishonest vs executor.
PlannerPlannerTool = Literal[
    "explore",
    "open_file",
    "search_code",
    "run_shell",
    "edit",
    "run_tests",
    "none",
]


class PlanStep(BaseModel):
    step_id: str
    index: int
    type: Literal["explore", "analyze", "modify", "validate", "finish"]
    goal: str
    action: Literal["search", "open_file", "edit", "run_tests", "shell", "finish"]
    inputs: dict = {}
    outputs: dict = {}
    dependencies: list[str] = []


class PlanSource(BaseModel):
    type: Literal["file", "search", "other"]
    ref: str
    summary: str


class PlanRisk(BaseModel):
    risk: str
    impact: Literal["low", "medium", "high"]
    mitigation: str


class PlanMetadata(BaseModel):
    created_at: str
    version: int = 1


class PlannerControllerOutput(BaseModel):
    """
    Structured decision from the planner LLM (orchestration maps to PlannerDecision).
    action is the single source of truth for explore vs continue vs replan vs stop.
    """

    action: Literal["continue", "replan", "explore", "stop"] = "continue"
    next_step_instruction: str = ""
    exploration_query: str = ""


class PlannerEngineStepSpec(BaseModel):
    """
    Single next executor step when decision is \"act\".

    Maps to PlanStep.action / inputs synthesis in PlannerV2 (not free-form only).
    """

    action: Literal["search", "open_file", "edit", "run_tests", "shell"] = "search"
    input: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("input", mode="before")
    @classmethod
    def _strip_input(cls, v: Any) -> str:
        if v is None:
            return ""
        return str(v).strip()[:8000]

    @field_validator("metadata", mode="before")
    @classmethod
    def _normalize_metadata(cls, v: Any) -> dict[str, Any]:
        if v is None or v == "":
            return {}
        if not isinstance(v, dict):
            return {}
        out: dict[str, Any] = {}
        for i, (k, val) in enumerate(v.items()):
            if i >= 16:
                break
            ks = str(k).strip()[:64]
            if not ks:
                continue
            if isinstance(val, str):
                out[ks] = val.strip()[:4000]
            elif isinstance(val, (int, float, bool)):
                out[ks] = val
            else:
                out[ks] = str(val).strip()[:4000]
        return out


class PlannerEngineOutput(BaseModel):
    """
    Decision-first planner output (replaces multi-step JSON from the LLM).

    Executor-facing PlanStep rows are synthesized from this for the compile phase.
    """

    decision: Literal["act", "explore", "replan", "stop", "synthesize", "plan"]
    tool: PlannerPlannerTool = "none"
    reason: str = ""
    query: str = ""
    step: Optional[PlannerEngineStepSpec] = None

    @field_validator("tool", mode="before")
    @classmethod
    def _coerce_tool(cls, v: Any) -> Any:
        if v is None or (isinstance(v, str) and not str(v).strip()):
            return "none"
        return v

    @field_validator("step", mode="before")
    @classmethod
    def _coerce_step_legacy_string(cls, v: Any) -> Any:
        """Allow legacy plain string for ``step`` (treated as search input)."""
        if v is None or v == "":
            return None
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return None
            return {"action": "search", "input": s[:8000]}
        return v


class PlanDocument(BaseModel):
    """
    Single source of truth for execution. Defines what to do, in what order,
    and the intent behind each step. Executor must follow it; cannot invent new steps.
    """
    plan_id: str
    instruction: str
    understanding: str
    sources: list[PlanSource]
    steps: list[PlanStep]
    risks: list[PlanRisk]
    completion_criteria: list[str]
    metadata: PlanMetadata
    engine: Optional[PlannerEngineOutput] = Field(
        default=None,
        description="Decision-first output from planner LLM; drives PlannerDecision when set.",
    )
    controller: Optional[PlannerControllerOutput] = Field(
        default=None,
        description="Legacy orchestration; optional when engine is set.",
    )
