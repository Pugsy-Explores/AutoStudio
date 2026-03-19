"""
Plan resolver: router decides, planner plans.

Per docs (phase.md, ROUTING_ARCHITECTURE_REPORT.md):
- Instruction router classifies before planner when ENABLE_INSTRUCTION_ROUTER=1
- CODE_SEARCH / CODE_EXPLAIN / INFRA → single-step plan, skip planner (30–60% fewer planner calls)
- CODE_EDIT / GENERAL → planner produces multi-step plan

Categories: CODE_SEARCH, CODE_EDIT, CODE_EXPLAIN, INFRA, GENERAL
Planner actions: SEARCH, EDIT, EXPLAIN, INFRA

Phase 4: Every plan has a unique plan_id so step identity is (plan_id, step_id).
"""

import logging
from uuid import uuid4

from agent.observability.trace_logger import trace_stage
from config.agent_config import (
    TWO_PHASE_DOCS_CODE_MAX_PARENT_RETRIES_PHASE_0,
    TWO_PHASE_DOCS_CODE_MAX_PARENT_RETRIES_PHASE_1,
)
from config.router_config import ENABLE_INSTRUCTION_ROUTER, ROUTER_CONFIDENCE_THRESHOLD
from planner.planner import plan

logger = logging.getLogger(__name__)

# Router categories that may short-circuit to a single step without calling plan().
_SHORT_CIRCUIT_ROUTER_CATEGORIES = frozenset({"CODE_SEARCH", "CODE_EXPLAIN", "INFRA"})


def _confidence_allows_router_short_circuit(confidence) -> bool:
    """
    True only when confidence is a usable numeric value at or above ROUTER_CONFIDENCE_THRESHOLD.
    None, non-numeric, or below threshold → False (conservative: defer to planner).
    """
    if confidence is None:
        return False
    try:
        value = float(confidence)
    except (TypeError, ValueError):
        return False
    return value >= float(ROUTER_CONFIDENCE_THRESHOLD)

_DOCS_INTENT_TOKENS = (
    "readme",
    "docs",
    "documentation",
    "documented",
    "architecture docs",
    "setup docs",
    "install",
    "installation",
    "usage",
    "guide",
)

_DOCS_DISCOVERY_VERBS = (
    # High-precision markers that the user is asking to locate docs artifacts.
    "where",
    "locate",
    "find",
    "list",
    "show",
)

_NON_DOCS_TOKENS = (
    # Generic code-intent markers; keep bounded and domain-neutral.
    "implemented",
    "implementation",
    "class ",
    "function ",
    "method ",
    "refactor",
    "edit ",
    "change ",
    "patch",
    "bug",
    "stack trace",
    "exception",
    # Phase 7B.1: mixed-intent markers (keep small; high precision).
    "explain",
    "flow",
    # Negation of docs: "documented" substring in "undocumented" would false-positive.
    "undocumented",
)


def _is_docs_artifact_intent(instruction: str) -> bool:
    """
    Phase 6D.2: bounded, deterministic docs-artifact intent detector.
    Narrow purpose: detect instructions asking to locate/read documentation artifacts
    (README/docs/documentation) so we can enter docs lane early.

    Guardrails:
    - deterministic string checks only
    - bounded generic token list (no repo-specific phrases)
    - do not try to be a broad semantic classifier
    """
    if not instruction:
        return False
    i = instruction.strip().lower()
    if not i:
        return False
    # Conservative: only override when user is explicitly asking to locate docs artifacts.
    has_discovery_verb = any(v in i for v in _DOCS_DISCOVERY_VERBS)
    if not has_discovery_verb:
        return False
    has_docs = any(t in i for t in _DOCS_INTENT_TOKENS)
    if not has_docs:
        return False
    has_non_docs = any(t in i for t in _NON_DOCS_TOKENS)
    return not has_non_docs


def _docs_seed_plan(instruction: str) -> dict:
    """Docs-lane seed plan: SEARCH_CANDIDATES(docs) -> BUILD_CONTEXT(docs) -> EXPLAIN(docs)."""
    i = (instruction or "").strip()
    q = "readme docs"
    il = i.lower()
    if "architecture" in il:
        q = "architecture docs"
    elif "install" in il or "setup" in il:
        q = "install docs"
    return _ensure_plan_id(
        {
            "steps": [
                {
                    "id": 1,
                    "action": "SEARCH_CANDIDATES",
                    "artifact_mode": "docs",
                    "description": "Locate README/docs artifacts",
                    "query": q,
                    "reason": "Docs-artifact intent detected; enter docs lane early",
                },
                {
                    "id": 2,
                    "action": "BUILD_CONTEXT",
                    "artifact_mode": "docs",
                    "description": "Build docs context from candidates",
                    "reason": "Read and rank docs context",
                },
                {
                    "id": 3,
                    "action": "EXPLAIN",
                    "artifact_mode": "docs",
                    "description": i or "Explain documentation content",
                    "reason": "Answer using docs context",
                },
            ]
        }
    )


def new_plan_id() -> str:
    """Return a unique plan_id with 'plan_' prefix for readable logs (e.g. plan_3f8b8a7d)."""
    return f"plan_{uuid4().hex[:8]}"


def _ensure_plan_id(plan: dict) -> dict:
    """Ensure plan has plan_id (Phase 4 — plan-scoped step identity)."""
    out = dict(plan)
    if "plan_id" not in out or not out["plan_id"]:
        out["plan_id"] = new_plan_id()
    return out


def get_plan(
    instruction: str,
    trace_id: str | None = None,
    log_event_fn=None,
    retry_context: dict | None = None,
) -> dict:
    """
    Resolve plan: use instruction router when enabled, else planner.

    When ENABLE_INSTRUCTION_ROUTER=1:
    - CODE_SEARCH → single SEARCH step (skip planner)
    - CODE_EXPLAIN → single EXPLAIN step (skip planner)
    - INFRA → single INFRA step (skip planner)
    - CODE_EDIT / GENERAL → planner

    When disabled: always use planner.

    Phase 5: retry_context (previous_attempts, critic_feedback) is passed to planner when provided.

    Router confidence: CODE_SEARCH / CODE_EXPLAIN / INFRA short-circuits apply only when
    decision.confidence is numeric and >= ROUTER_CONFIDENCE_THRESHOLD; otherwise those
    categories are treated as GENERAL for planning (plan() is called).
    """
    if not ENABLE_INSTRUCTION_ROUTER:
        if trace_id:
            with trace_stage(trace_id, "planner") as summary:
                plan_result = plan(instruction, retry_context=retry_context)
                summary["instruction"] = (instruction or "")[:200]
                summary["number_of_steps"] = len(plan_result.get("steps", []))
                summary["actions"] = [s.get("action") for s in plan_result.get("steps", [])]
            return _ensure_plan_id(plan_result)
        return _ensure_plan_id(plan(instruction, retry_context=retry_context))

    from agent.routing.instruction_router import route_instruction

    # Phase 6D.2: docs-artifact intent bypasses router short-circuit plans.
    # This avoids misrouting docs questions into INFRA/EXPLAIN without docs lane.
    if _is_docs_artifact_intent(instruction):
        if log_event_fn and trace_id:
            try:
                log_event_fn(trace_id, "docs_intent_override", {"detected": True})
            except Exception:
                pass
        plan_result = _docs_seed_plan(instruction)
        if trace_id:
            with trace_stage(trace_id, "planner") as summary:
                summary["instruction"] = (instruction or "")[:200]
                summary["number_of_steps"] = len(plan_result.get("steps", []))
                summary["actions"] = [s.get("action") for s in plan_result.get("steps", [])]
        return plan_result

    decision = route_instruction(instruction)
    router_category = decision.category
    raw_confidence = getattr(decision, "confidence", None)
    trust_short_circuit = _confidence_allows_router_short_circuit(raw_confidence)
    confidence_fallback_applied = (
        router_category in _SHORT_CIRCUIT_ROUTER_CATEGORIES and not trust_short_circuit
    )
    category = "GENERAL" if confidence_fallback_applied else router_category

    if log_event_fn and trace_id:
        try:
            log_event_fn(
                trace_id,
                "instruction_router",
                {
                    "category": router_category,
                    "confidence": raw_confidence,
                    "confidence_fallback_applied": confidence_fallback_applied,
                    "router_confidence_threshold": float(ROUTER_CONFIDENCE_THRESHOLD),
                    "plan_branch_category": category,
                },
            )
        except Exception as e:
            logger.debug("[plan_resolver] log_event skipped: %s", e)

    if category == "CODE_SEARCH":
        plan_result = _ensure_plan_id({
            "steps": [
                {"id": 1, "action": "SEARCH", "description": instruction, "reason": "Routed by instruction router"}
            ],
        })
        if trace_id:
            with trace_stage(trace_id, "planner") as summary:
                summary["instruction"] = (instruction or "")[:200]
                summary["number_of_steps"] = 1
                summary["actions"] = ["SEARCH"]
        return plan_result
    if category == "CODE_EXPLAIN":
        plan_result = _ensure_plan_id({
            "steps": [
                {"id": 1, "action": "EXPLAIN", "description": instruction, "reason": "Routed by instruction router"}
            ],
        })
        if trace_id:
            with trace_stage(trace_id, "planner") as summary:
                summary["instruction"] = (instruction or "")[:200]
                summary["number_of_steps"] = 1
                summary["actions"] = ["EXPLAIN"]
        return plan_result
    if category == "INFRA":
        plan_result = _ensure_plan_id({
            "steps": [
                {"id": 1, "action": "INFRA", "description": instruction, "reason": "Routed by instruction router"}
            ],
        })
        if trace_id:
            with trace_stage(trace_id, "planner") as summary:
                summary["instruction"] = (instruction or "")[:200]
                summary["number_of_steps"] = 1
                summary["actions"] = ["INFRA"]
        return plan_result

    # CODE_EDIT or GENERAL: use planner
    if trace_id:
        with trace_stage(trace_id, "planner") as summary:
            plan_result = plan(instruction, retry_context=retry_context)
            summary["instruction"] = (instruction or "")[:200]
            summary["number_of_steps"] = len(plan_result.get("steps", []))
            summary["actions"] = [s.get("action") for s in plan_result.get("steps", [])]
        return _ensure_plan_id(plan_result)
    return _ensure_plan_id(plan(instruction, retry_context=retry_context))


def _is_two_phase_docs_code_intent(instruction: str) -> bool:
    """
    True when instruction mixes docs-discovery intent with code-intent (e.g. find docs + explain).
    Must return False if _is_docs_artifact_intent(instruction) is True.
    Reuses _DOCS_DISCOVERY_VERBS, _DOCS_INTENT_TOKENS; code-intent markers from _NON_DOCS_TOKENS subset.
    """
    if not instruction or not instruction.strip():
        return False
    if _is_docs_artifact_intent(instruction):
        return False
    lower = instruction.strip().lower()
    has_discovery = any(v in lower for v in _DOCS_DISCOVERY_VERBS)
    if not has_discovery:
        return False
    has_docs = any(t in lower for t in _DOCS_INTENT_TOKENS)
    if not has_docs:
        return False
    # Narrow: exclude "implemented"/"implementation" (ambiguous—can describe docs subject).
    code_markers = ("explain", "flow", "function ", "method ", "class ")
    has_code_intent = any(m in lower for m in code_markers)
    return has_code_intent


def _derive_phase_subgoals(instruction: str) -> tuple[str, str]:
    """
    Return (phase_0_subgoal, phase_1_subgoal) for two-phase docs-then-code execution.
    Phase 0: docs discovery. Phase 1: code explanation/description from connector split.
    """
    source = (instruction or "").strip()
    phase0 = "Find documentation artifacts relevant to: " + source[:150]

    connectors = (
        " and explain ",
        " and describe ",
        " and show how ",
        " and summarize ",
        " and walk through ",
        ", then explain ",
        " then explain ",
        " and tell me about ",
        " and tell me how ",
        " and walk me through ",
        " and illustrate ",
        " before explaining ",
        ", explain ",
    )
    lower = source.lower()
    for connector in connectors:
        pos = lower.find(connector)
        if pos != -1:
            start = pos + len(connector)
            raw = source[start:].strip()
            if len(raw) >= 10:
                phase1 = raw[0].upper() + raw[1:]
            else:
                phase1 = source
            return (phase0, phase1)

    phase1 = source
    return (phase0, phase1)


def _coerce_max_parent_retries(value) -> int:
    """
    Sanitize config-driven retry budget for two-phase plans.
    Non-int, bool, or negative values -> 0 (Stage 7).
    """
    if isinstance(value, bool):
        return 0
    if not isinstance(value, int):
        return 0
    if value < 0:
        return 0
    return value


def _build_replan_phase(phase_plan: dict, failure_context: dict | None = None) -> dict:
    """
    Stage 10: rebuild a single phase plan after a REPLAN parent-policy decision.

    Must not call get_parent_plan() or run_hierarchical(). Returns a new phase_plan dict
    for the same phase_id / phase_index / lane / validation / retry_policy with fresh steps
    and a new plan_id where applicable.

    failure_context may include: parent_instruction, failure_class, goal_reason (for logging
    or deterministic query tweaks).
    """
    from agent.orchestrator.goal_evaluator import is_explain_like_instruction
    from planner.planner_utils import validate_plan

    if not isinstance(phase_plan, dict):
        raise ValueError("phase_plan_invalid")
    fc = None
    parent_instruction = ""
    if isinstance(failure_context, dict):
        parent_instruction = str(failure_context.get("parent_instruction") or "")
        fc = failure_context.get("failure_class")

    lane = str(phase_plan.get("lane") or "code")
    phase_id = phase_plan.get("phase_id", "")
    pidx = phase_plan.get("phase_index", 0)
    if isinstance(pidx, bool) or not isinstance(pidx, int):
        pidx = 0
    subgoal = str(phase_plan.get("subgoal") or "")
    validation = phase_plan.get("validation")
    if not isinstance(validation, dict):
        if lane == "docs":
            validation = {
                "require_ranked_context": True,
                "require_explain_success": True,
                "min_candidates": 1,
            }
        else:
            validation = {
                "require_ranked_context": True,
                "require_explain_success": is_explain_like_instruction(subgoal),
                "min_candidates": 1,
            }
    retry_policy = phase_plan.get("retry_policy")
    if not isinstance(retry_policy, dict):
        retry_policy = {"max_parent_retries": 0}

    if lane == "docs":
        seed = _docs_seed_plan(parent_instruction)
        steps: list = []
        for s in seed.get("steps", []):
            if isinstance(s, dict):
                steps.append(dict(s))
        if not steps:
            raise ValueError("replan_docs_empty_steps")
        if isinstance(steps[0], dict) and steps[0].get("action") == "SEARCH_CANDIDATES":
            q = str(steps[0].get("query", "readme docs"))
            if fc in ("phase_validation_failed", "goal_not_satisfied"):
                q = (q + " readme documentation overview").strip()
            steps[0]["query"] = q
        candidate = _ensure_plan_id({"steps": steps})
        if not validate_plan({"steps": candidate["steps"]}):
            raise ValueError("replan_docs_validate_plan_failed")
        return {
            "phase_id": phase_id,
            "phase_index": pidx,
            "subgoal": subgoal,
            "lane": "docs",
            "steps": candidate["steps"],
            "plan_id": candidate["plan_id"],
            "validation": validation,
            "retry_policy": retry_policy,
        }

    code_flat = plan(subgoal)
    code_flat = _ensure_plan_id(code_flat)
    if not validate_plan({"steps": code_flat.get("steps", [])}):
        raise ValueError("replan_code_validate_plan_failed")
    return {
        "phase_id": phase_id,
        "phase_index": pidx,
        "subgoal": subgoal,
        "lane": "code",
        "steps": code_flat["steps"],
        "plan_id": code_flat["plan_id"],
        "validation": validation,
        "retry_policy": retry_policy,
    }


def _build_two_phase_parent_plan(
    instruction: str,
    trace_id: str | None = None,
    log_event_fn=None,
) -> dict:
    """
    Build a two-phase ParentPlan: Phase 0 (docs lane) + Phase 1 (code lane).
    Phase 1 planner receives phase_1_subgoal, not the parent instruction.
    Raises ValueError if either phase's steps fail validate_plan.
    """
    from agent.orchestrator.goal_evaluator import is_explain_like_instruction
    from agent.orchestrator.parent_plan import new_parent_plan_id, new_phase_id
    from planner.planner_utils import validate_plan

    _budget_phase_0 = _coerce_max_parent_retries(TWO_PHASE_DOCS_CODE_MAX_PARENT_RETRIES_PHASE_0)
    _budget_phase_1 = _coerce_max_parent_retries(TWO_PHASE_DOCS_CODE_MAX_PARENT_RETRIES_PHASE_1)
    phase_0_subgoal, phase_1_subgoal = _derive_phase_subgoals(instruction)

    # Phase 0 — docs lane
    docs_flat = _docs_seed_plan(instruction)
    if not validate_plan({"steps": docs_flat["steps"]}):
        raise ValueError("phase_0_validate_plan_failed")

    phase_0: dict = {
        "phase_id": new_phase_id(),
        "phase_index": 0,
        "subgoal": phase_0_subgoal,
        "lane": "docs",
        "steps": docs_flat["steps"],
        "plan_id": docs_flat["plan_id"],
        "validation": {
            "require_ranked_context": True,
            "require_explain_success": True,
            "min_candidates": 1,
        },
        "retry_policy": {"max_parent_retries": _budget_phase_0},
    }

    # Phase 1 — code lane
    code_flat = plan(phase_1_subgoal)
    code_flat = _ensure_plan_id(code_flat)
    if not validate_plan({"steps": code_flat["steps"]}):
        raise ValueError("phase_1_validate_plan_failed")

    phase_1: dict = {
        "phase_id": new_phase_id(),
        "phase_index": 1,
        "subgoal": phase_1_subgoal,
        "lane": "code",
        "steps": code_flat["steps"],
        "plan_id": code_flat["plan_id"],
        "validation": {
            "require_ranked_context": True,
            "require_explain_success": is_explain_like_instruction(phase_1_subgoal),
            "min_candidates": 1,
        },
        "retry_policy": {"max_parent_retries": _budget_phase_1},
    }

    return {
        "parent_plan_id": new_parent_plan_id(),
        "instruction": instruction,
        "decomposition_type": "two_phase_docs_code",
        "phases": [phase_0, phase_1],
        "compatibility_mode": False,
    }


def get_parent_plan(
    instruction: str,
    trace_id: str | None = None,
    log_event_fn=None,
    retry_context: dict | None = None,
) -> dict:
    """
    Stage 1: wraps get_plan() in a single-phase compatibility ParentPlan.
    Stage 2: adds mixed-intent detection before the compatibility fallback.
    Never raises; propagates get_plan() behavior on failure.
    """
    from agent.orchestrator.parent_plan import make_compatibility_parent_plan

    if _is_two_phase_docs_code_intent(instruction):
        try:
            parent_plan = _build_two_phase_parent_plan(
                instruction,
                trace_id=trace_id,
                log_event_fn=log_event_fn,
            )
            if log_event_fn and trace_id:
                try:
                    log_event_fn(trace_id, "parent_plan_created", {
                        "parent_plan_id": parent_plan["parent_plan_id"],
                        "decomposition_type": parent_plan["decomposition_type"],
                        "compatibility_mode": parent_plan["compatibility_mode"],
                        "phase_count": len(parent_plan["phases"]),
                        "instruction_preview": (instruction or "")[:200],
                    })
                except Exception:
                    pass
            return parent_plan
        except Exception as e:
            if log_event_fn and trace_id:
                try:
                    log_event_fn(trace_id, "two_phase_fallback", {
                        "reason": str(e)[:120],
                        "instruction_preview": (instruction or "")[:200],
                    })
                except Exception:
                    pass

    # Near-miss observability: docs + discovery markers but two-phase detection did not fire.
    if log_event_fn and trace_id and not _is_two_phase_docs_code_intent(instruction):
        il = (instruction or "").strip().lower()
        has_discovery = any(v in il for v in _DOCS_DISCOVERY_VERBS)
        has_docs = any(t in il for t in _DOCS_INTENT_TOKENS)
        if has_discovery and has_docs:
            try:
                log_event_fn(
                    trace_id,
                    "two_phase_near_miss",
                    {
                        "reason": "docs_and_discovery_but_no_code_marker",
                        "instruction_preview": (instruction or "")[:200],
                    },
                )
            except Exception:
                pass

    flat_plan = get_plan(
        instruction,
        trace_id=trace_id,
        log_event_fn=log_event_fn,
        retry_context=retry_context,
    )
    parent_plan = make_compatibility_parent_plan(flat_plan, instruction)
    if log_event_fn and trace_id:
        try:
            log_event_fn(trace_id, "parent_plan_created", {
                "parent_plan_id": parent_plan["parent_plan_id"],
                "decomposition_type": parent_plan["decomposition_type"],
                "compatibility_mode": parent_plan["compatibility_mode"],
                "phase_count": len(parent_plan["phases"]),
                "instruction_preview": (instruction or "")[:200],
            })
        except Exception:
            pass
    return parent_plan
