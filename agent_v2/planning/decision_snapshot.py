"""Build PlannerDecisionSnapshot from runtime state (PlannerTaskRuntime only)."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

from agent_v2.memory.task_working_memory import task_working_memory_from_state
from agent_v2.planning.planner_v2_invocation import plan_document_has_runnable_work
from agent_v2.schemas.final_exploration import FinalExplorationSchema
from agent_v2.schemas.plan import PlanDocument
from agent_v2.schemas.planner_action import PlannerDecisionSnapshot


def plan_document_fingerprint(plan_doc: PlanDocument) -> str:
    """Stable short hash of merged plan state (stagnation / snapshot enrichment)."""

    payload = plan_doc.model_dump(mode="json", exclude_none=True)
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:32]


def build_planner_decision_snapshot(
    state: Any,
    exploration: Optional[FinalExplorationSchema],
    *,
    rolling_conversation_summary: str = "",
    plan_doc: Optional[PlanDocument] = None,
    last_executor_status: Optional[str] = None,
    last_loop_outcome: str = "",
) -> PlannerDecisionSnapshot:
    wm = task_working_memory_from_state(state)
    conf: str | None = None
    gaps_n = 0
    if exploration is not None:
        conf = str(exploration.confidence) if exploration.confidence else None
        gaps = exploration.exploration_summary.knowledge_gaps or []
        gaps_n = len([g for g in gaps if str(g).strip()])

    md = getattr(state, "metadata", None)
    lo = (last_loop_outcome or "").strip()
    ebd_consume: dict[str, Any] | None = None
    if not lo and isinstance(md, dict) and "task_planner_last_loop_outcome" in md:
        lo = str(md.get("task_planner_last_loop_outcome", "") or "").strip()[:8000]
        del md["task_planner_last_loop_outcome"]
        raw_ebd = md.pop("explore_block_details", None)
        if isinstance(raw_ebd, dict):
            ebd_consume = raw_ebd

    aci = 0
    if isinstance(md, dict):
        raw_aci = md.get("act_controller_iteration_count")
        if isinstance(raw_aci, int):
            aci = raw_aci
        elif raw_aci is not None:
            try:
                aci = int(raw_aci)
            except (TypeError, ValueError):
                aci = 0

    has_pending: Optional[bool] = None
    lph = ""
    if plan_doc is not None:
        has_pending = plan_document_has_runnable_work(plan_doc)
        lph = plan_document_fingerprint(plan_doc)

    v_hint = ""
    ctx_obj = getattr(state, "context", None)
    if isinstance(ctx_obj, dict):
        vf = ctx_obj.get("validation_feedback")
        if isinstance(vf, dict):
            mc = vf.get("missing_context")
            if isinstance(mc, list):
                parts = [str(x).strip() for x in mc if str(x).strip()][:24]
                v_hint = " | ".join(parts)[:2000]

    return PlannerDecisionSnapshot(
        instruction=str(getattr(state, "instruction", "") or ""),
        rolling_conversation_summary=rolling_conversation_summary,
        working_memory_fingerprint=wm.fingerprint(),
        last_exploration_confidence=conf,
        last_exploration_gaps_count=gaps_n,
        last_exploration_query_hash=wm.last_exploration_query_hash,
        outer_iteration=wm.outer_explore_iterations,
        has_pending_plan_work=has_pending,
        last_executor_status=last_executor_status,
        last_loop_outcome=lo,
        act_controller_iteration_count=aci,
        explore_block_details=ebd_consume,
        last_plan_hash=lph,
        validation_retrieval_hint=v_hint,
    )
