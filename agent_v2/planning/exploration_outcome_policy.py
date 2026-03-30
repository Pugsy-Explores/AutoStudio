"""
Normalize exploration outcomes for stop / gate policy (no ExplorationEngine changes).

PlannerTaskRuntime enforces; TaskPlannerService does not call these.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from agent_v2.config import AgentV2Config, ChatPlanningConfig

if TYPE_CHECKING:
    from agent_v2.memory.task_working_memory import TaskWorkingMemory
    from agent_v2.schemas.final_exploration import FinalExplorationSchema

UnderstandingLevel = Literal["sufficient", "partial", "insufficient"]


def normalize_understanding(fe: "FinalExplorationSchema") -> UnderstandingLevel:
    """
    Derive coarse understanding from FinalExplorationSchema (full-planner-arch-freeze-impl §4.4).

    - sufficient: high confidence and no non-empty knowledge gaps
    - insufficient: low confidence or many gaps
    - partial: otherwise
    """
    conf = (fe.confidence or "").strip().lower()
    gaps = [str(g).strip() for g in (fe.exploration_summary.knowledge_gaps or []) if str(g).strip()]
    n = len(gaps)
    if conf == "low":
        return "insufficient"
    if conf == "high" and n == 0:
        return "sufficient"
    if conf == "medium" and n > 2:
        return "insufficient"
    if n == 0 and conf == "medium":
        return "partial"
    return "partial"


def _legacy_sub_exploration_gates_ok(fe: "FinalExplorationSchema") -> bool:
    """Original gate: allow another sub-explore when gaps exist or confidence is low."""
    gaps = fe.exploration_summary.knowledge_gaps or []
    if any(str(g).strip() for g in gaps):
        return True
    return fe.confidence == "low"


def should_stop_after_exploration(
    fe: "FinalExplorationSchema",
    wm: "TaskWorkingMemory",
    *,
    chat: ChatPlanningConfig,
) -> tuple[bool, str]:
    """
    If True, caller should treat exploration as complete for gating (no further sub-explore).

    Sub-exploration *count* remains enforced in PlannerTaskRuntime via
    ``max_sub_explorations_per_task``. Budget-style stops are not duplicated here.

    When chat.enable_exploration_stop_policy is False, returns (False, "").
    """
    if not chat.enable_exploration_stop_policy:
        return False, ""

    nu = normalize_understanding(fe)
    if nu == "sufficient":
        return True, "sufficient_understanding"
    if wm.partial_repeat_exhausted(max_streak=2):
        return True, "repeated_partial"
    return False, ""


def sub_exploration_allowed(
    fe: "FinalExplorationSchema",
    wm: "TaskWorkingMemory",
    *,
    cfg: AgentV2Config,
) -> bool:
    """
    True if another sub-exploration may proceed (matches legacy _sub_exploration_gates_ok
    when stop policy off; tightens when on).
    """
    if not _legacy_sub_exploration_gates_ok(fe):
        return False
    stop, _ = should_stop_after_exploration(fe, wm, chat=cfg.chat_planning)
    return not stop
