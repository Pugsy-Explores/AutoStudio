"""
Planner: convert user instruction into a sequence of atomic steps (EDIT, SEARCH, EXPLAIN, INFRA).
Uses reasoning model only; output is always valid JSON-compatible structure.
"""

import json
import os
import re

from agent.core.actions import Action
from agent.models.model_client import call_reasoning_model
from agent.retrieval.query_rewriter import heuristic_condense_for_retrieval
from agent.models.model_config import get_model_call_params
from planner.planner_prompts import PLANNER_SYSTEM_PROMPT
from planner.planner_utils import normalize_actions, validate_plan

# Env override when config has no max_tokens. Config (task_params.planner) takes precedence.
_PLANNER_MAX_ENV = int(os.environ.get("PLANNER_MAX_TOKENS", "4096"))

# Routed intent value for edit tasks (maps to CODE_EDIT / INTENT_EDIT)
_INTENT_EDIT = "EDIT"


def _extract_json(text: str) -> str | None:
    """Strip markdown code fences and return the first JSON object string, or None."""
    if not text or not text.strip():
        return None
    text = text.strip()
    # Remove optional ```json ... ``` or ``` ... ```
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if match:
        text = match.group(1).strip()
    # Try to find {...}
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


_DOCS_LANE_ACTIONS = (Action.SEARCH_CANDIDATES.value, Action.BUILD_CONTEXT.value, Action.EXPLAIN.value)


def _has_explicit_docs_lane_steps(plan_dict: dict) -> bool:
    """
    True when the parsed plan explicitly uses docs lane on docs-compatible actions.
    This is an explicit signal only (artifact_mode must equal 'docs').
    """
    if not isinstance(plan_dict, dict):
        return False
    steps = plan_dict.get("steps") or []
    if not isinstance(steps, list):
        return False
    for s in steps:
        if not isinstance(s, dict):
            continue
        action = (s.get("action") or "").upper()
        if action in _DOCS_LANE_ACTIONS and s.get("artifact_mode") == "docs":
            return True
    return False


def _retry_context_has_docs_lane_lineage(retry_context: dict | None) -> bool:
    """
    True when retry_context explicitly indicates docs lane from previous attempts.
    Uses only structured prior plan data; does not infer from instruction text.
    """
    if not retry_context or not isinstance(retry_context, dict):
        return False
    previous_attempts = retry_context.get("previous_attempts") or []
    if not isinstance(previous_attempts, list):
        return False
    for att in previous_attempts:
        if not isinstance(att, dict):
            continue
        plan_att = att.get("plan") or {}
        if _has_explicit_docs_lane_steps(plan_att):
            return True
    return False


def _build_controlled_fallback_plan(
    instruction: str,
    *,
    retry_context: dict | None,
    parsed_plan: dict | None = None,
    error: str,
    reason: str,
    primary_intent: str | None = None,
) -> dict:
    """
    Planner controlled fallback (Phase 5B.2).

    Fallback is lane-aware only when explicit docs lineage exists:
    - from valid parsed steps with artifact_mode="docs" on docs-compatible actions, OR
    - from retry_context.previous_attempts containing a prior docs-lane plan.

    Intent-aware: for edit tasks (primary_intent == INTENT_EDIT), return SEARCH + EDIT
    so CODE_EDIT tasks can reach the EDIT step even under fallback.

    Shapes:
    - docs lane: SEARCH_CANDIDATES -> BUILD_CONTEXT -> EXPLAIN with artifact_mode='docs'
    - edit intent: SEARCH -> EDIT (minimal viable path for code modification)
    - default: single SEARCH
    """
    # Temporary checkpoint log: confirm fallback usage (print for visibility in eval output)
    print(f"[planner] fallback triggered (intent={primary_intent or 'unknown'})")
    docs_lane = _has_explicit_docs_lane_steps(parsed_plan or {}) or _retry_context_has_docs_lane_lineage(
        retry_context
    )
    if docs_lane:
        plan_dict = {
            "steps": [
                {
                    "id": 1,
                    "action": Action.SEARCH_CANDIDATES.value,
                    "artifact_mode": "docs",
                    "description": "Find README/docs artifacts",
                    "query": "readme docs",
                    "reason": reason,
                },
                {
                    "id": 2,
                    "action": Action.BUILD_CONTEXT.value,
                    "artifact_mode": "docs",
                    "description": "Build docs context from candidates",
                    "reason": "Read top docs files",
                },
                {
                    "id": 3,
                    "action": Action.EXPLAIN.value,
                    "artifact_mode": "docs",
                    "description": "Answer using docs context",
                    "reason": "Complete docs-shaped fallback plan",
                },
            ],
            "error": error,
            "fallback": True,
            "degraded": True,
            "degradation_reason": reason,
        }
        return plan_dict
    condensed = heuristic_condense_for_retrieval(instruction)
    query = condensed.strip() if condensed and condensed.strip() else (instruction or "").strip()[:200]
    if primary_intent == _INTENT_EDIT:
        return {
            "steps": [
                {
                    "id": 1,
                    "action": Action.SEARCH.value,
                    "description": "Locate relevant code for modification",
                    "query": query,
                    "reason": reason,
                },
                {
                    "id": 2,
                    "action": Action.EDIT.value,
                    "description": "Apply required code changes based on instruction",
                    "reason": reason,
                },
            ],
            "error": error,
            "fallback": True,
            "degraded": True,
            "degradation_reason": reason,
        }
    return {
        "steps": [
            {
                "id": 1,
                "action": Action.SEARCH.value,
                "description": f"Locate items mentioned in: {instruction[:200]}{'...' if len(instruction) > 200 else ''}",
                "query": query,
                "reason": reason,
            }
        ],
        "error": error,
        "fallback": True,
        "degraded": True,
        "degradation_reason": reason,
    }


def plan(
    instruction: str,
    retry_context: dict | None = None,
    *,
    primary_intent: str | None = None,
) -> dict:
    """
    Convert instruction into a structured plan: list of steps with action, description, reason.
    Each step action is one of EDIT, SEARCH, EXPLAIN, INFRA.
    On parse/validation failure returns {"steps": [...], "error": "..."} with a safe default.

    Phase 5: retry_context may contain previous_attempts and critic_feedback; these are
    included in the prompt so the planner can produce a better plan on retry.
    """
    print("[workflow] planner")
    prompt = instruction
    if retry_context:
        previous_attempts = retry_context.get("previous_attempts") or []
        feedback = retry_context.get("critic_feedback") or {}
        strategy_hint = retry_context.get("strategy_hint") or ""

        # [Previous Attempts]: plan structure summary (diversity guard)
        previous_attempts_lines = ["Previous attempt plans:"]
        for i, att in enumerate(previous_attempts, 1):
            plan_att = att.get("plan") or {}
            steps_att = plan_att.get("steps") or []
            actions = [str(s.get("action", "?")) for s in steps_att if isinstance(s, dict)]
            arrow = " → "
            previous_attempts_lines.append(f"- Plan {i}: {arrow.join(actions) or '(no steps)'}")
        previous_attempts_summary = "\n".join(previous_attempts_lines)

        planning_guidance = """Avoid repeating the same plan structure as previous attempts.
Generate a different strategy if the previous attempt failed.
Focus on actions that address the failure reason."""

        parts = [
            "[Strategy Hint]",
            strategy_hint,
            "",
            "[Previous Attempts]",
            previous_attempts_summary,
            "",
            "[Planning Guidance]",
            planning_guidance,
            "",
            "[Instruction]",
            instruction,
        ]
        if feedback:
            parts.append("")
            parts.append("Critic feedback:")
            parts.append(f"  failure_reason: {feedback.get('failure_reason', '')}")
            parts.append(f"  analysis: {feedback.get('analysis', '')}")
            parts.append(f"  recommendation: {feedback.get('recommendation', '')}")
        prompt = "\n".join(parts)
    params = get_model_call_params("planner")
    max_tokens = params.get("max_tokens") or _PLANNER_MAX_ENV
    try:
        response = call_reasoning_model(
            prompt,
            system_prompt=PLANNER_SYSTEM_PROMPT,
            max_tokens=max_tokens,
            task_name="planner",
            prompt_name="planner",
        )
    except RuntimeError as e:
        # GuardrailError (and other RuntimeError) — model retry pipeline already exhausted
        return _build_controlled_fallback_plan(
            instruction,
            retry_context=retry_context,
            parsed_plan=None,
            error=str(e),
            reason=str(e),
            primary_intent=primary_intent,
        )

    raw_json = _extract_json(response)
    if not raw_json:
        return _build_controlled_fallback_plan(
            instruction,
            retry_context=retry_context,
            parsed_plan=None,
            error="No JSON found in response",
            reason="Parse failed; controlled fallback",
            primary_intent=primary_intent,
        )

    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as e:
        return _build_controlled_fallback_plan(
            instruction,
            retry_context=retry_context,
            parsed_plan=None,
            error=str(e),
            reason="Invalid JSON from planner; controlled fallback",
            primary_intent=primary_intent,
        )

    if not isinstance(data, dict) or "steps" not in data:
        data = {"steps": []}
    if not isinstance(data["steps"], list):
        data["steps"] = []

    # Ensure each step has id, action, description, reason
    for i, step in enumerate(data["steps"]):
        if not isinstance(step, dict):
            data["steps"][i] = {
                "id": i + 1,
                "action": Action.EXPLAIN.value,
                "description": "Invalid step",
                "reason": "Malformed",
            }
            continue
        step.setdefault("id", i + 1)
        step.setdefault("action", Action.EXPLAIN.value)
        step.setdefault("description", "")
        step.setdefault("reason", "")

    data = normalize_actions(data)
    if not validate_plan(data):
        # Controlled fallback: planner produced an invalid plan structure.
        return _build_controlled_fallback_plan(
            instruction,
            retry_context=retry_context,
            parsed_plan=data,
            error="Validation failed: invalid plan or invalid step fields",
            reason="Planner output validation failed; controlled fallback",
            primary_intent=primary_intent,
        )
    # Temporary checkpoint log: plan passed guardrail and validation (print for visibility in eval output)
    print("[planner] plan accepted by guardrail")
    return data
