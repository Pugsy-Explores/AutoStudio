"""
Task-level working memory (per top-level instruction) — model only.

Mutations are performed by PlannerTaskRuntime or helpers it calls.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, Field

TASK_WORKING_MEMORY_CONTEXT_KEY = "task_working_memory"
TASK_WORKING_MEMORY_VERSION = 1


class CompletedStepRecord(BaseModel):
    kind: Literal["explore", "act", "synthesize", "replan", "plan_refresh"]
    summary: str = ""


class AnalyzerSnapshotRecord(BaseModel):
    confidence: str | None = None
    gaps_nonempty: bool = False


class TaskWorkingMemory(BaseModel):
    """Ephemeral per-task state; reset when runtime starts a new top-level instruction."""

    current_goal: str = ""
    sub_tasks: list[str] = Field(default_factory=list)
    completed_steps: list[CompletedStepRecord] = Field(default_factory=list)
    tool_outputs: list[dict[str, str]] = Field(default_factory=list)
    accumulated_context: list[str] = Field(default_factory=list)
    analyzer_snapshots: list[AnalyzerSnapshotRecord] = Field(default_factory=list)
    iteration_count: int = 0
    last_exploration_id: str = ""
    last_exploration_query_hash: str | None = None
    outer_explore_iterations: int = 0
    partial_streak: int = 0
    last_gaps_fingerprint: str | None = None
    version: int = TASK_WORKING_MEMORY_VERSION

    def fingerprint(self) -> str:
        """Short stable string for PlannerDecisionSnapshot (no large payloads)."""
        payload = {
            "g": self.current_goal[:200],
            "n": self.iteration_count,
            "p": self.partial_streak,
            "h": self.last_exploration_query_hash,
        }
        raw = json.dumps(payload, sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    def partial_repeat_exhausted(self, *, max_streak: int = 2) -> bool:
        return self.partial_streak >= max_streak

    def record_exploration_tick(
        self,
        *,
        exploration_id: str,
        query_hash: str | None,
        confidence: str | None,
        gaps_nonempty: bool,
        understanding: str,
    ) -> None:
        self.iteration_count += 1
        self.outer_explore_iterations += 1
        self.last_exploration_id = exploration_id
        self.last_exploration_query_hash = query_hash
        self.analyzer_snapshots.append(
            AnalyzerSnapshotRecord(confidence=confidence, gaps_nonempty=gaps_nonempty)
        )
        if understanding == "partial":
            self.partial_streak += 1
        else:
            self.partial_streak = 0
        self.last_gaps_fingerprint = _gaps_fingerprint_from_flags(gaps_nonempty)

    def record_completed(self, kind: CompletedStepRecord) -> None:
        self.completed_steps.append(kind)

    def model_dump_for_context(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def _gaps_fingerprint_from_flags(gaps_nonempty: bool) -> str:
    return "g1" if gaps_nonempty else "g0"


def _sync_task_working_to_context(state: Any, tm: TaskWorkingMemory) -> None:
    ctx = getattr(state, "context", None)
    if isinstance(ctx, dict):
        ctx[TASK_WORKING_MEMORY_CONTEXT_KEY] = tm


def task_working_memory_from_state(state: Any) -> TaskWorkingMemory:
    from agent_v2.state.agent_state import MEMORY_WORKING, ensure_agent_memory_dict

    memory = ensure_agent_memory_dict(state)
    slot = memory.get(MEMORY_WORKING)
    if isinstance(slot, TaskWorkingMemory):
        _sync_task_working_to_context(state, slot)
        return slot
    if isinstance(slot, dict):
        tm = TaskWorkingMemory.model_validate(slot)
        memory[MEMORY_WORKING] = tm
        _sync_task_working_to_context(state, tm)
        return tm

    ctx = getattr(state, "context", None)
    if not isinstance(ctx, dict):
        raise TypeError("state.context must be a dict for task working memory")
    key = TASK_WORKING_MEMORY_CONTEXT_KEY
    existing = ctx.get(key)
    if isinstance(existing, TaskWorkingMemory):
        memory[MEMORY_WORKING] = existing
        return existing
    if isinstance(existing, dict):
        tm = TaskWorkingMemory.model_validate(existing)
        ctx[key] = tm
        memory[MEMORY_WORKING] = tm
        return tm
    tm = TaskWorkingMemory()
    ctx[key] = tm
    memory[MEMORY_WORKING] = tm
    return tm


def reset_task_working_memory(state: Any) -> TaskWorkingMemory:
    from agent_v2.state.agent_state import MEMORY_WORKING, ensure_agent_memory_dict

    ctx = getattr(state, "context", None)
    if not isinstance(ctx, dict):
        raise TypeError("state.context must be a dict")
    tm = TaskWorkingMemory()
    ctx[TASK_WORKING_MEMORY_CONTEXT_KEY] = tm
    ensure_agent_memory_dict(state)[MEMORY_WORKING] = tm
    return tm
