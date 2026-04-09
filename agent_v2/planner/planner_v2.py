"""
Planner v2 — Phase 4: PlannerPlanContext → PlanDocument (STRICT).

Does not call tools or execute steps. LLM is injected as ``generate_fn(user_prompt, system_prompt=None)``
(typically wired to ``call_reasoning_model`` in bootstrap; legacy single-arg ``generate_fn`` still works).
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from pydantic import ValidationError

from agent_v2.schemas.plan import (
    PlanDocument,
    PlanMetadata,
    PlannerControllerOutput,
    PlannerEngineOutput,
    PlannerEngineStepSpec,
    PlanRisk,
    PlanSource,
    PlanStep,
    PlannerPlannerTool,
)
from agent_v2.schemas.plan_state import PlanState
from agent_v2.schemas.policies import ExecutionPolicy
from agent_v2.schemas.exploration import QueryIntent, effective_exploration_budget
from agent_v2.schemas.replan import ReplanContext
from agent_v2.schemas.planner_plan_context import PlannerPlanContext
from agent_v2.schemas.final_exploration import FinalExplorationSchema
from agent_v2.observability.langfuse_helpers import (
    LANGFUSE_GENERATION_PROMPT_INPUT_MAX_CHARS,
    langfuse_generation_end_with_usage,
    langfuse_generation_input_with_prompt,
    try_langfuse_generation,
)
from agent_v2.config import PLANNER_PROMPT_MAX_LAST_RESULT_CHARS, get_config
from agent_v2.validation.plan_validator import PlanValidationError, PlanValidator
from agent_v2.runtime.phase1_tool_exposure import (
    PLANNER_ACT_TOOL_IDS,
    PLANNER_TOOL_TO_PLAN_STEP_ACTION,
)
from agent_v2.runtime.session_memory import SessionMemory, is_vague_user_text
from agent.prompt_system.registry import get_registry
from agent_v2.utils.json_extractor import JSONExtractor
from agent_v2.runtime.tool_policy import (
    PLAN_MODE_TOOL_POLICY,
    ToolPolicy,
    ToolPolicyViolationError,
    apply_tool_policy,
)

logger = logging.getLogger(__name__)

DEFAULT_POLICY = ExecutionPolicy(max_steps=8, max_retries_per_step=2, max_replans=2)

# Raised when tool JSON is still invalid after exactly one repair LLM call (no further retries).
TOOL_REPAIR_EXHAUSTED_PREFIX = "PLANNER_TOOL_REPAIR_EXHAUSTED"

# ---------------------------------------------------------------------------
# CRITICAL — TECH DEBT (remove or narrow when replanner module exists)
#
# The following maps and _coerce_step_action_and_type() are RCA-driven shims:
# they paper over invalid LLM JSON (intent vs tool confusion, junk in "steps").
#
# TODO(replanner): Replace with the production path from architecture freeze:
#   - Structured / schema-constrained planner output where possible
#   - On invalid PlanDocument: ReplanRequest → Replanner → revised plan (Phase 7+),
#     not silent coercion (which can hide model regressions and produce wrong tools).
#
# Until then, keep these shims; failing closed on every bad token blocks plan_execute.
# Tracked: replanner + planner output contract — see Docs/architecture_freeze/
# VALIDATION_REGISTRY.md (PlanValidator ownership) and PHASE_7_REPLANNER_CONTROL_LOOP.md.
# ---------------------------------------------------------------------------
_TOOL_ACTIONS = frozenset({"search", "open_file", "edit", "run_tests", "shell", "finish"})
_INTENT_TYPES = frozenset({"explore", "analyze", "modify", "validate", "finish"})
_INTENT_TO_TOOL = {
    "explore": "search",
    "analyze": "open_file",
    "modify": "edit",
    "validate": "run_tests",
    "finish": "finish",
}
_TOOL_TO_INTENT = {
    "search": "explore",
    "open_file": "analyze",
    "edit": "modify",
    "run_tests": "validate",
    "shell": "explore",
    "finish": "finish",
}


def _coerce_step_action_and_type(action_raw: Any, type_raw: Any) -> tuple[str, str]:
    """
    LLMs often confuse PlanStep.type (intent) with PlanStep.action (tool).
    Normalize to valid Literal pairs for Pydantic.

    CRITICAL: This is a temporary normalizer — see module block comment above.
    TODO(replanner): Prefer explicit replan or repair prompts over mutating model output here;
    coercion must not be the long-term source of truth for plan correctness.
    """
    a = str(action_raw or "").strip().lower()
    t = str(type_raw or "").strip().lower()

    if a in _TOOL_ACTIONS:
        tool = a
    elif a in _INTENT_TO_TOOL:
        tool = _INTENT_TO_TOOL[a]
    elif t in _TOOL_ACTIONS:
        tool = t
    elif t in _INTENT_TO_TOOL:
        tool = _INTENT_TO_TOOL[t]
    else:
        tool = "open_file"

    if t in _INTENT_TYPES:
        intent = t
    elif t in _TOOL_TO_INTENT:
        intent = _TOOL_TO_INTENT[t]
    elif a in _INTENT_TYPES:
        intent = a
    elif a in _TOOL_TO_INTENT:
        intent = _TOOL_TO_INTENT[a]
    else:
        intent = _TOOL_TO_INTENT.get(tool, "analyze")

    if tool == "finish":
        intent = "finish"
    return tool, intent


def _strip_json_fence(text: str) -> str:
    text = (text or "").strip()
    if "```" in text:
        m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
        if m:
            return m.group(1).strip()
    return text


def _parse_json_object(text: str) -> dict[str, Any]:
    raw = _strip_json_fence(text)
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    # Fallback handles reasoning + fenced JSON and last-valid-json behavior.
    return JSONExtractor.extract_final_json(text)


def _trim_plan_steps_preserving_finish(steps_raw: list[Any], max_steps: int) -> list[Any]:
    """
    Cap plan length at max_steps without silently dropping a terminal ``finish`` row.

    Previously ``steps_raw[:max_steps]`` could cut off the only ``action=finish`` step
    (e.g. model emitted 9+ steps with finish at index 9), causing PlanValidationError.
    """
    if len(steps_raw) <= max_steps:
        return steps_raw
    head = steps_raw[: max_steps - 1]
    finish_in_tail = next(
        (
            s
            for s in steps_raw[max_steps - 1 :]
            if isinstance(s, dict) and s.get("action") == "finish"
        ),
        None,
    )
    if finish_in_tail is None:
        return steps_raw[:max_steps]
    terminal = dict(finish_in_tail)
    terminal["type"] = "finish"
    terminal["action"] = "finish"
    prev_sid = "s1"
    for s in reversed(head):
        if isinstance(s, dict) and s.get("step_id") is not None:
            prev_sid = str(s.get("step_id"))
            break
        if isinstance(s, dict):
            prev_sid = str(s.get("step_id") or "s1")
            break
    used = {
        str(s.get("step_id"))
        for s in head
        if isinstance(s, dict) and s.get("step_id") is not None
    }
    terminal["dependencies"] = [prev_sid]
    tid = str(terminal.get("step_id") or f"s{max_steps}")
    if tid in used:
        tid = f"s{max_steps}"
    while tid in used:
        tid = f"{tid}_"
    terminal["step_id"] = tid
    return list(head) + [terminal]


def _truncate_for_planner_prompt(text: str, max_chars: int) -> str:
    t = (text or "").strip()
    if len(t) <= max_chars:
        return t
    return t[: max_chars - 40] + "\n…[truncated for planner context budget]…"


def _format_plan_state_block(plan_state: PlanState) -> str:
    cap = max(500, int(PLANNER_PROMPT_MAX_LAST_RESULT_CHARS))
    done = "\n".join(f"- {c.step_id}: {c.summary[:500]}" for c in plan_state.completed_steps) or "(none)"
    cur = plan_state.current_step_id or "(unknown)"
    idx = plan_state.current_step_index
    idx_s = str(idx) if idx is not None else "?"
    last_raw = (plan_state.last_result_summary or "").strip() or "(none)"
    last = _truncate_for_planner_prompt(last_raw, cap) if last_raw != "(none)" else "(none)"
    return f"""COMPLETED STEPS (summaries):
{done}

CURRENT STEP: {cur} (index {idx_s})
LAST RESULT SUMMARY:
{last}
"""


class PlannerV2:
    """
    Production planner: decision-first JSON (default) or legacy multi-step JSON.

    Args:
        generate_fn: ``(user_prompt, system_prompt=None) -> str`` preferred; or legacy
            ``(prompt: str) -> str`` (system+user merged when needed).
        policy: ExecutionPolicy; max_retries_per_step is applied at compile time in DagExecutor.
    """

    def __init__(
        self,
        generate_fn: Callable[[str], str],
        policy: Optional[ExecutionPolicy] = None,
        *,
        strict_tool: Optional[bool] = None,
        tool_policy: Optional[ToolPolicy] = None,
    ):
        self._generate_fn = generate_fn
        self._policy = policy or DEFAULT_POLICY
        self._strict_tool = bool(strict_tool) if strict_tool is not None else bool(get_config().planner.strict_tool)
        self._tool_policy = tool_policy if tool_policy is not None else PLAN_MODE_TOOL_POLICY

    def _resolve_planner_model_task_name(self, ctx: PlannerPlanContext) -> str:
        """
        ``models_config.json`` task key for this planner call (``task_models`` + ``task_params``).

        - Failure replans (``PlannerPlanContext.replan``): ``PLANNER_REPLAN_PLAN`` or
          ``PLANNER_REPLAN_ACT`` (matches ``tool_policy.mode``).
        - Safe / read-only tool policy (CLI ``--mode plan``): ``PLANNER_DECISION_PLAN``
        - Act / execute tool policy (CLI ``--mode act``): ``PLANNER_DECISION_ACT``
        """
        if ctx.replan is not None:
            return "PLANNER_REPLAN_PLAN" if self._tool_policy.mode == "plan" else "PLANNER_REPLAN_ACT"
        if self._tool_policy.mode == "plan":
            return "PLANNER_DECISION_PLAN"
        return "PLANNER_DECISION_ACT"

    def _planner_prompt_model_name(self) -> Optional[str]:
        """Display name of the model for prompt_versions (follows active planner model task)."""
        try:
            from agent.models.model_config import get_model_for_task, get_model_name
            from agent_v2.planner.planner_model_call_context import get_active_planner_model_task

            task = get_active_planner_model_task() or "PLANNER_DECISION_ACT"
            key = get_model_for_task(task)
            name = str(get_model_name(key) or "").strip()
            return name or None
        except Exception:
            return None

    def _registry_prompt_text(self, name: str, variables: dict[str, str]) -> str:
        from agent.prompt_system.loader import load_prompt

        t = load_prompt(
            name,
            version="latest",
            variables=variables,
            model_name=self._planner_prompt_model_name(),
        )
        text = (t.system_prompt or t.instructions or "").strip()
        if not text:
            raise PlanValidationError(f"Empty prompt from registry: {name!r}")
        return text

    def _require_controller_fragment(self, require: bool) -> str:
        if not require:
            return ""
        from agent.prompt_system.loader import load_prompt

        t = load_prompt(
            "planner.v2.require_controller_json",
            version="latest",
            variables=None,
            model_name=self._planner_prompt_model_name(),
        )
        return ("\n" + (t.system_prompt or t.instructions or "").strip() + "\n")

    def plan(
        self,
        instruction: str,
        planner_context: PlannerPlanContext | FinalExplorationSchema | ReplanContext,
        deep: bool = False,
        langfuse_trace: Any = None,
        obs: Any = None,
        *,
        plan_state: PlanState | None = None,
        prior_plan_document: PlanDocument | None = None,
        require_controller_json: bool = False,
        validation_task_mode: Optional[str] = None,
    ) -> PlanDocument:
        from agent_v2.planner.planner_model_call_context import planner_model_task_scope
        from agent_v2.runtime.exploration_planning_input import normalize_planner_plan_context

        ctx = normalize_planner_plan_context(planner_context)

        # LIVE-TEST-002: Infer task mode from instruction unless caller pins validator mode
        # (e.g. plan_safe for iterative PLAN mode — distinct from read_only).
        task_mode = (
            validation_task_mode
            if validation_task_mode is not None
            else self._infer_task_mode(instruction)
        )

        lf: Any = None
        if obs is not None and getattr(obs, "langfuse_trace", None) is not None:
            lf = obs.langfuse_trace
        elif langfuse_trace is not None:
            lf = langfuse_trace

        model_task = self._resolve_planner_model_task_name(ctx)
        with planner_model_task_scope(model_task):
            return self._execute_plan_llm_roundtrip(
                instruction=instruction,
                ctx=ctx,
                task_mode=task_mode,
                deep=deep,
                langfuse_trace=lf,
                plan_state=plan_state,
                prior_plan_document=prior_plan_document,
                require_controller_json=require_controller_json,
                model_task=model_task,
            )

    def _execute_plan_llm_roundtrip(
        self,
        *,
        instruction: str,
        ctx: PlannerPlanContext,
        task_mode: str,
        deep: bool,
        langfuse_trace: Any,
        plan_state: PlanState | None,
        prior_plan_document: PlanDocument | None,
        require_controller_json: bool,
        model_task: str,
    ) -> PlanDocument:
        user_p, system_p = self._build_plan_prompt_parts(
            instruction,
            ctx,
            deep,
            task_mode=task_mode,
            plan_state=plan_state,
            require_controller_json=require_controller_json,
        )
        lf_prompt = self._combined_prompt_for_telemetry(user_p, system_p)
        gen_name = "planner_replan" if ctx.replan is not None else "planner"
        lf = langfuse_trace
        planning_span: Any = None
        if lf is not None and hasattr(lf, "span"):
            try:
                planning_span = lf.span("planning", input={"instruction": instruction[:500]})
            except Exception:
                planning_span = None
        try:
            raw = self._call_llm(
                user_p,
                system_prompt=system_p,
                langfuse_trace=lf,
                parent_span=planning_span,
                gen_name=gen_name,
                generation_metadata={
                    "prompt_chars": len(lf_prompt),
                    "deep": deep,
                    "is_replan": ctx.replan is not None,
                    "model_task": model_task,
                },
                telemetry_prompt=lf_prompt,
            )
        finally:
            if planning_span is not None:
                try:
                    planning_span.end()
                except Exception:
                    pass

        override_triggered = False
        plan: PlanDocument | None = None
        repair_attempted = False
        tool_repair_failed = False
        last_tool_err: Optional[BaseException] = None
        while True:
            try:
                plan, override_triggered = self._build_plan(
                    raw,
                    instruction,
                    require_controller_json=require_controller_json,
                    task_mode=task_mode,
                    planner_context=ctx,
                    prior_plan_document=prior_plan_document,
                )
                last_tool_err = None
                if isinstance(ctx.session, SessionMemory):
                    ctx.session.last_planner_validation_error = ""
                break
            except PlanValidationError as e:
                last_tool_err = e
                if isinstance(e, ToolPolicyViolationError):
                    logger.error(
                        "planner_telemetry %s",
                        json.dumps(
                            {
                                "component": "planner_telemetry",
                                "tool_policy_violation": {
                                    "tool": e.policy_tool,
                                    "reason": e.policy_reason,
                                    "command": e.policy_command,
                                },
                                "tool_policy_mode": self._tool_policy.mode,
                                "error": str(e)[:800],
                            },
                            default=str,
                        ),
                    )
                    raise
                if isinstance(ctx.session, SessionMemory):
                    ctx.session.last_planner_validation_error = str(e)[:2000]
                if not self._is_planner_tool_validation_error(e):
                    raise
                if repair_attempted:
                    tool_repair_failed = True
                    logger.error(
                        "planner_telemetry %s",
                        json.dumps(
                            {
                                "component": "planner_telemetry",
                                "tool_repair_failed": True,
                                "tool_repair_attempted": True,
                                "error": str(e)[:800],
                            },
                            default=str,
                        ),
                    )
                    raise PlanValidationError(
                        f"{TOOL_REPAIR_EXHAUSTED_PREFIX}: invalid tool/decision JSON after one repair "
                        f"attempt; refusing further LLM retries. Original: {e}"
                    ) from e
                repair_attempted = True
                ru, rs = self._append_suffix_to_prompt_parts(
                    user_p,
                    system_p,
                    self._tool_repair_suffix() + PlannerV2._validation_error_repair_appendix(e),
                )
                lf_rep = self._combined_prompt_for_telemetry(ru, rs)
                raw = self._call_llm(
                    ru,
                    system_prompt=rs,
                    langfuse_trace=lf,
                    parent_span=None,
                    gen_name=gen_name + "_tool_repair",
                    generation_metadata={
                        "prompt_chars": len(lf_rep),
                        "deep": deep,
                        "is_replan": ctx.replan is not None,
                        "tool_repair": True,
                        "model_task": model_task,
                    },
                    telemetry_prompt=lf_rep,
                )
        if plan is None:
            assert last_tool_err is not None
            raise last_tool_err

        if not isinstance(plan, PlanDocument):
            raise TypeError(
                f"Planner internal error: expected PlanDocument, got {type(plan).__name__}"
            )

        PlanValidator.validate_plan(plan, policy=self._policy, task_mode=task_mode)

        mem = ctx.session if isinstance(ctx.session, SessionMemory) else None
        eng = plan.engine
        logger.info(
            "planner_telemetry %s",
            json.dumps(
                {
                    "component": "planner_telemetry",
                    "decision": getattr(eng, "decision", None),
                    "tool": getattr(eng, "tool", None),
                    "explore_streak": getattr(mem, "explore_streak", 0) if mem is not None else 0,
                    "override_triggered": override_triggered,
                    "tool_repair_attempted": repair_attempted,
                    "tool_repair_failed": tool_repair_failed,
                    "strict_tool": self._strict_tool,
                    "tool_policy_mode": self._tool_policy.mode,
                    "model_task": model_task,
                },
                default=str,
            ),
        )
        return plan

    @staticmethod
    def _combined_prompt_for_telemetry(
        user_prompt: str, system_prompt: Optional[str]
    ) -> str:
        u, s = (user_prompt or "").strip(), (system_prompt or "").strip()
        if s and u:
            return f"[SYSTEM]\n{s}\n\n[USER]\n{u}"
        return u or s

    @staticmethod
    def _append_suffix_to_prompt_parts(
        user_prompt: str,
        system_prompt: Optional[str],
        suffix: str,
    ) -> tuple[str, Optional[str]]:
        if (user_prompt or "").strip():
            return user_prompt + suffix, system_prompt
        if (system_prompt or "").strip():
            return user_prompt, (system_prompt or "") + suffix
        return user_prompt + suffix, system_prompt

    def _invoke_generate(
        self, user_prompt: str, system_prompt: Optional[str]
    ) -> str:
        fn = self._generate_fn
        try:
            return fn(user_prompt, system_prompt)  # type: ignore[misc, call-arg]
        except TypeError:
            pass
        su = (system_prompt or "").strip()
        uu = (user_prompt or "").strip()
        if su:
            if uu:
                return fn(f"{su}\n\n{uu}")  # type: ignore[misc, call-arg]
            return fn(su)  # type: ignore[misc, call-arg]
        return fn(user_prompt)  # type: ignore[misc, call-arg]

    @staticmethod
    def _effective_query_intent(planner_context: PlannerPlanContext) -> Optional[QueryIntent]:
        if planner_context.query_intent is not None:
            return planner_context.query_intent
        if planner_context.replan is not None and planner_context.replan.query_intent is not None:
            return planner_context.replan.query_intent
        ex = planner_context.exploration
        if ex is not None:
            q = getattr(ex, "query_intent", None)
            if q is not None:
                return q  # type: ignore[no-any-return]
        return None

    @staticmethod
    def _exploration_budget_advisory_block(planner_context: PlannerPlanContext) -> str:
        qi = PlannerV2._effective_query_intent(planner_context)
        cap = planner_context.exploration_budget
        if cap is None:
            cap = effective_exploration_budget(qi)
        mem = planner_context.session if isinstance(planner_context.session, SessionMemory) else None
        explores = int(getattr(mem, "explore_decisions_total", 0) or 0) if mem is not None else 0
        last_steps = (
            int(getattr(mem, "last_exploration_engine_steps", 0) or 0) if mem is not None else 0
        )
        return (
            "--------------------------------\n"
            "EXPLORATION BUDGET (advisory — planner authority; not a code-enforced stop)\n"
            "--------------------------------\n"
            "Exploration is expensive and limited. Each EXPLORE decision consumes one unit of budget "
            "toward the advisory cap below.\n"
            "If you continue exploring unnecessarily, you may not be able to complete the task. "
            "Only explore when it is REQUIRED to make progress.\n"
            "Prefer ACT when you can make progress (e.g. search_code, open_file). "
            "Avoid repeating exploration queries.\n\n"
            f"Session usage: {explores} planner EXPLORE decision(s) this task "
            f"(advisory cap: {cap}).\n"
            f"Last exploration run inner effort: {last_steps} engine loop step(s) "
            "(one planner EXPLORE can trigger many inner steps).\n"
            "--------------------------------"
        )

    # Max chars of user instruction to embed when QueryIntent was never parsed (prompt scope signal).
    _USER_TASK_INTENT_FALLBACK_INSTRUCTION_CHARS = 1200

    @staticmethod
    def _validation_feedback_section(planner_context: PlannerPlanContext) -> str:
        vf = planner_context.validation_feedback
        if vf is None:
            return ""
        issues = "\n".join(f"- {i}" for i in vf.issues) or "(none)"
        missing = "\n".join(f"- {m}" for m in vf.missing_context) or "(none)"
        if not vf.is_complete:
            mandate = (
                'MANDATORY: Next decision MUST NOT be "synthesize". '
                'Prioritize "explore" and use missing_context / issues to drive retrieval. '
                '"act" is allowed only with a concrete next tool step.'
            )
        else:
            mandate = (
                "Answer validation passed (is_complete=true). "
                "You may stop, act, or synthesize per task policy."
            )
        vreason = (vf.validation_reason or "").strip() or "(none)"
        return (
            "--------------------------------\n"
            "VALIDATION FEEDBACK (post-synthesis; authoritative)\n"
            "--------------------------------\n"
            f"is_complete: {vf.is_complete}\n"
            f"confidence: {vf.confidence}\n"
            f"validation_reason: {vreason}\n"
            "issues:\n"
            f"{issues}\n"
            "missing_context:\n"
            f"{missing}\n"
            f"{mandate}\n"
            "--------------------------------"
        )

    @staticmethod
    def _user_task_intent_section(
        planner_context: PlannerPlanContext,
        instruction: str = "",
    ) -> str:
        qi = PlannerV2._effective_query_intent(planner_context)
        if qi is None:
            hint = (instruction or "").strip()
            if hint:
                clipped = _truncate_for_planner_prompt(
                    hint, PlannerV2._USER_TASK_INTENT_FALLBACK_INSTRUCTION_CHARS
                )
                body = (
                    "structured_query_intent: not parsed by exploration engine\n"
                    "scope_from_user_instruction:\n"
                    f"{clipped}"
                )
            else:
                body = (
                    "structured_query_intent: not parsed; no user instruction in planner call "
                    "(rely on EXPLORATION and KEY FINDINGS below)."
                )
        else:
            lines: list[str] = []
            if qi.intent_type:
                lines.append(f"intent_type: {qi.intent_type}")
            if qi.target and str(qi.target).strip():
                lines.append(f"target: {str(qi.target).strip()}")
            if qi.scope:
                lines.append(f"scope: {qi.scope}")
            if qi.focus:
                lines.append(f"focus: {qi.focus}")
            if qi.intents:
                lines.append(
                    "retrieval_intents: "
                    + "; ".join(str(x) for x in qi.intents[:6] if str(x).strip())
                )
            body = "\n".join(lines) if lines else "(structured task fields empty)"
        return (
            "--------------------------------\n"
            "USER TASK INTENT:\n"
            f"{body}\n"
            "--------------------------------"
        )

    def _compose_exploration_context_block(
        self,
        planner_context: PlannerPlanContext,
        deep: bool,
        task_mode: Optional[str],
        *,
        plan_state: PlanState | None,
        instruction: str = "",
    ) -> str:
        exploration = planner_context.exploration
        if exploration is None:
            raise TypeError("PlannerPlanContext missing exploration for non-replan path")
        insufficiency = planner_context.insufficiency
        es = exploration.exploration_summary
        key_findings = "\n".join(f"- {k}" for k in es.key_findings) or "(none)"
        knowledge_gaps = "\n".join(f"- {g}" for g in es.knowledge_gaps) or "(none)"
        available_symbols = "\n".join(
            f"- {s}" for s in (planner_context.available_symbols or [])[:20]
        ) or "(none)"
        missing_symbols = "\n".join(
            f"- {s}" for s in (planner_context.missing_symbols or [])[:20]
        ) or "(none)"
        conf_band = exploration.confidence
        summary_text = (es.overall or "").strip() or "(none)"

        plan_progress = ""
        if plan_state is not None:
            plan_progress = (
                "\n--------------------------------\nPLAN PROGRESS (read-only):\n"
                + _format_plan_state_block(plan_state)
            )

        insufficiency_tail = ""
        if insufficiency is not None:
            insufficiency_tail = (
                f"\nNote: exploration may be incomplete ({insufficiency.termination_reason or 'unknown'}).\n"
            )

        deep_tail = ""
        if deep:
            deep_tail = "\nNote: prefer conservative decisions; avoid unnecessary exploration.\n"

        ro_tail = ""
        if task_mode == "read_only":
            ro_tail = (
                "\n⚠️ READ-ONLY task: if you choose \"act\", the next step will be search or open_file only "
                "(no edits).\n"
            )
        plan_safe_tail = ""
        if task_mode == "plan_safe":
            plan_safe_tail = (
                '\n⚠️ PLAN-SAFE task: if you choose "act", step.action must be search, open_file, '
                "run_tests, or shell only — not edit.\n"
            )

        session_block = self._format_session_memory_block(planner_context.session)

        exploration_block = f"""--------------------------------
EXPLORATION (source of truth for repo facts)
--------------------------------

CURRENT UNDERSTANDING:
{summary_text}

KEY FINDINGS:
{key_findings}

KNOWLEDGE GAPS:
{knowledge_gaps}

AVAILABLE SYMBOLS:
{available_symbols}

MISSING SYMBOLS:
{missing_symbols}

CONFIDENCE:
{conf_band}{insufficiency_tail}{deep_tail}{ro_tail}{plan_safe_tail}{plan_progress}{PlannerV2._last_planner_validation_block(planner_context.session)}
"""

        intent_section = self._user_task_intent_section(planner_context, instruction=instruction)
        validation_block = PlannerV2._validation_feedback_section(planner_context)
        parts = [intent_section.strip(), exploration_block.strip(), session_block.strip()]
        if validation_block.strip():
            parts = [
                intent_section.strip(),
                exploration_block.strip(),
                validation_block.strip(),
                session_block.strip(),
            ]
        return "\n\n".join(p for p in parts if p)

    def _compose_replan_context_block(
        self,
        planner_context: PlannerPlanContext,
        deep: bool,
        task_mode: Optional[str],
        *,
        plan_state: PlanState | None,
        instruction: str = "",
    ) -> str:
        ctx = planner_context.replan
        if ctx is None:
            raise TypeError("replan context missing")
        fc = ctx.failure_context
        completed = "\n".join(f"- {c.step_id}: {c.summary}" for c in ctx.completed_steps) or "(none)"
        es = ctx.exploration_summary
        summary_text = "(none)"
        key_findings = "(none)"
        knowledge_gaps = "(none)"
        if es is not None:
            summary_text = (es.overall or "").strip() or "(none)"
            key_findings = "\n".join(f"- {x}" for x in es.key_findings) or "(none)"
            knowledge_gaps = "\n".join(f"- {x}" for x in es.knowledge_gaps) or "(none)"
        trig = getattr(ctx, "trigger", "failure") or "failure"
        trigger_note = ""
        if trig == "insufficiency":
            trigger_note = (
                "TRIGGER: insufficiency — adjust strategy; preserve completed work.\n"
            )
        plan_progress = ""
        if plan_state is not None:
            plan_progress = (
                "\n--------------------------------\nPLAN PROGRESS (read-only):\n"
                + _format_plan_state_block(plan_state)
            )

        deep_tail = ""
        if deep:
            deep_tail = "\nNote: after a failure, prefer \"replan\" or \"explore\" only if needed.\n"

        ro_tail = ""
        if task_mode == "read_only":
            ro_tail = (
                "\n⚠️ READ-ONLY task: if you choose \"act\", the next step will be search or open_file only.\n"
            )
        plan_safe_tail = ""
        if task_mode == "plan_safe":
            plan_safe_tail = (
                '\n⚠️ PLAN-SAFE task: if you choose "act", step.action must be search, open_file, '
                "run_tests, or shell only — not edit.\n"
            )

        cap = max(500, int(PLANNER_PROMPT_MAX_LAST_RESULT_CHARS))
        los = _truncate_for_planner_prompt(str(fc.last_output_summary or ""), cap)
        failure_block = f"""- step_id: {fc.step_id}
- error_type: {fc.error.type}
- message: {fc.error.message}
- attempts: {fc.attempts}
- last_output_summary: {los}
- replan_trigger: {trig}
{trigger_note}"""

        session_block = self._format_session_memory_block(planner_context.session)

        exploration_block = f"""--------------------------------
EXPLORATION / CONTEXT (replan)
--------------------------------

CURRENT UNDERSTANDING:
{summary_text}

KEY FINDINGS:
{key_findings}

KNOWLEDGE GAPS:
{knowledge_gaps}

CONFIDENCE:
(context: replan — use failure below)

FAILURE / STATUS:
{failure_block}

COMPLETED STEPS:
{completed}
{deep_tail}{ro_tail}{plan_safe_tail}{plan_progress}{PlannerV2._last_planner_validation_block(planner_context.session)}
"""

        intent_section = self._user_task_intent_section(planner_context, instruction=instruction)
        budget_block = self._exploration_budget_advisory_block(planner_context)
        validation_block = PlannerV2._validation_feedback_section(planner_context)
        parts = [
            intent_section.strip(),
            budget_block.strip(),
            exploration_block.strip(),
            session_block.strip(),
        ]
        if validation_block.strip():
            parts = [
                intent_section.strip(),
                budget_block.strip(),
                exploration_block.strip(),
                validation_block.strip(),
                session_block.strip(),
            ]
        return "\n\n".join(p for p in parts if p)

    def _build_plan_prompt_parts(
        self,
        instruction: str,
        planner_context: PlannerPlanContext,
        deep: bool,
        task_mode: Optional[str] = None,
        *,
        plan_state: PlanState | None = None,
        require_controller_json: bool = False,
    ) -> tuple[str, Optional[str]]:
        reg = get_registry()
        mn = self._planner_prompt_model_name()

        if planner_context.replan is not None:
            ctx_block = self._compose_replan_context_block(
                planner_context, deep, task_mode, plan_state=plan_state, instruction=instruction
            )
            if self._tool_policy.mode == "plan":
                system, user = reg.render_prompt_parts(
                    "planner.replan.v1",
                    version="latest",
                    variables={
                        "instruction": instruction,
                        "context_block": ctx_block,
                    },
                    model_name=mn,
                )
                user = (user or "").strip()
                system = (system or "").strip()
                if require_controller_json:
                    user = user + self._require_controller_fragment(True).rstrip("\n")
                return user, system or None
            system, user = reg.render_prompt_parts(
                "planner.replan.act",
                version="latest",
                variables={
                    "instruction": instruction,
                    "context_block": ctx_block,
                    "req_decision": self._require_controller_fragment(
                        require_controller_json
                    ),
                },
                model_name=mn,
            )
            system = (system or "").strip()
            user = (user or "").strip()
            if user:
                return user, system or None
            return system, None

        ctx_block = self._compose_exploration_context_block(
            planner_context, deep, task_mode, plan_state=plan_state, instruction=instruction
        )
        if self._tool_policy.mode == "plan":
            system, user = reg.render_prompt_parts(
                "planner.decision.v1",
                version="latest",
                variables={
                    "instruction": instruction,
                    "context_block": ctx_block,
                },
                model_name=mn,
            )
            user = (user or "").strip()
            system = (system or "").strip()
            if require_controller_json:
                user = user + self._require_controller_fragment(True).rstrip("\n")
            return user, system or None
        system, user = reg.render_prompt_parts(
            "planner.decision.act",
            version="latest",
            variables={
                "instruction": instruction,
                "context_block": ctx_block,
                "req_decision": self._require_controller_fragment(
                    require_controller_json
                ),
            },
            model_name=mn,
        )
        system = (system or "").strip()
        user = (user or "").strip()
        if user:
            return user, system or None
        return system, None

    def _infer_task_mode(self, instruction: str) -> Optional[str]:
        """
        Infer task mode from instruction text.
        Returns "read_only" for exploratory questions, None for write tasks.
        """
        instruction_lower = instruction.lower()
        
        # Read-only indicators
        read_only_keywords = [
            "where", "what", "how", "why", "which", "explain", "describe",
            "find", "locate", "search", "analyze", "understand", "show me",
            "tell me", "what is", "what are", "how does", "where is"
        ]
        
        # Write task indicators
        write_keywords = [
            "add", "create", "implement", "fix", "modify", "update", "change",
            "build", "refactor", "remove", "delete", "edit", "write", "generate"
        ]
        
        # Check for write keywords first (higher priority)
        if any(keyword in instruction_lower for keyword in write_keywords):
            return None  # Write task
        
        # Check for read-only keywords
        if any(instruction_lower.startswith(keyword) for keyword in read_only_keywords):
            return "read_only"
        
        # Default: assume write task (conservative - prevent accidental writes)
        return None

    @staticmethod
    def _format_session_memory_block(session: Any) -> str:
        if session is None or not isinstance(session, SessionMemory):
            return ""
        body = session.to_prompt_block()
        if not body:
            return ""
        return (
            "\n--------------------------------\nSESSION MEMORY "
            "(read-only; use for vague references. If it conflicts with exploration, trust exploration.):\n"
            f"{body}\n"
        )

    @staticmethod
    def _is_planner_tool_validation_error(err: BaseException) -> bool:
        msg = str(err).lower()
        return (
            "tool" in msg
            or "plannerengineoutput" in msg
            or "invalid decision engine" in msg
        )

    def _tool_repair_suffix(self) -> str:
        sub = (
            "planner.v2.tool_repair.plan"
            if self._tool_policy.mode == "plan"
            else "planner.v2.tool_repair.act"
        )
        return self._registry_prompt_text(sub, {})

    def _call_llm(
        self,
        user_prompt: str,
        *,
        system_prompt: Optional[str] = None,
        langfuse_trace: Any = None,
        parent_span: Any = None,
        gen_name: str = "planner",
        generation_metadata: dict[str, Any] | None = None,
        telemetry_prompt: Optional[str] = None,
    ) -> dict[str, Any]:
        lf_in = telemetry_prompt or self._combined_prompt_for_telemetry(
            user_prompt, system_prompt
        )
        u, s = user_prompt, system_prompt
        last_err: Optional[Exception] = None
        for attempt in range(2):
            gen = try_langfuse_generation(
                parent_span,
                langfuse_trace,
                name=gen_name,
                input=langfuse_generation_input_with_prompt(
                    lf_in,
                    extra={"attempt": attempt},
                ),
            )
            try:
                text = self._invoke_generate(u, s)
                if gen is not None:
                    try:
                        meta = dict(generation_metadata or {})
                        meta["attempt"] = attempt
                        langfuse_generation_end_with_usage(
                            gen,
                            output={
                                "response": text[:LANGFUSE_GENERATION_PROMPT_INPUT_MAX_CHARS]
                            },
                            metadata=meta,
                        )
                    except Exception:
                        pass
                return _parse_json_object(text)
            except (json.JSONDecodeError, TypeError, ValueError) as e:
                last_err = e
                if gen is not None:
                    try:
                        meta = dict(generation_metadata or {})
                        meta["attempt"] = attempt
                        langfuse_generation_end_with_usage(
                            gen,
                            output={"error": str(e)[:2000]},
                            metadata=meta,
                        )
                    except Exception:
                        pass
                if attempt == 0:
                    suffix = self._registry_prompt_text("planner.v2.invalid_json_retry", {})
                    u, s = self._append_suffix_to_prompt_parts(u, s, suffix)
                    lf_in = self._combined_prompt_for_telemetry(u, s)
        raise PlanValidationError(f"Planner LLM output was not valid JSON: {last_err}") from last_err

    @staticmethod
    def _validate_controller_pairing(controller: PlannerControllerOutput) -> None:
        q = (controller.exploration_query or "").strip()
        if controller.action == "explore":
            if not q:
                raise PlanValidationError(
                    "controller.action is 'explore' but exploration_query is empty"
                )
        elif controller.action == "stop":
            if q:
                raise PlanValidationError(
                    "controller.action is 'stop' but exploration_query is non-empty"
                )
        elif q:
            raise PlanValidationError(
                "controller.exploration_query must be empty unless action is 'explore'"
            )

    @staticmethod
    def _validate_engine_pairing(engine: PlannerEngineOutput) -> None:
        q = (engine.query or "").strip()
        if engine.decision == "explore":
            if not q:
                raise PlanValidationError('decision "explore" requires non-empty "query"')
        elif q:
            raise PlanValidationError('"query" must be empty unless decision is "explore"')

    @staticmethod
    def _validate_engine_task_mode(
        engine: PlannerEngineOutput, task_mode: Optional[str]
    ) -> None:
        if engine.decision != "act":
            return
        spec = engine.step
        if spec is None:
            return
        if task_mode == "read_only":
            # read_only: no edits or test runs; shell is allowed (e.g. ls/rg/cat) like search/open_file.
            if spec.action in ("edit", "run_tests"):
                raise PlanValidationError(
                    f'read_only task: "act" cannot use step.action={spec.action!r}'
                )
        elif task_mode == "plan_safe" and spec.action == "edit":
            raise PlanValidationError(
                f'plan_safe task: "act" cannot use step.action={spec.action!r}'
            )

    @staticmethod
    def _act_controller_hint(engine: PlannerEngineOutput) -> str:
        if engine.step is not None:
            s = engine.step
            part = f"{s.action}: {s.input}".strip()
            return part or (engine.reason or "")
        return engine.reason or ""

    @staticmethod
    def _sync_controller_from_engine(engine: PlannerEngineOutput) -> PlannerControllerOutput:
        if engine.decision == "explore":
            return PlannerControllerOutput(
                action="explore",
                next_step_instruction=engine.reason,
                exploration_query=engine.query,
            )
        if engine.decision == "stop":
            return PlannerControllerOutput(
                action="stop",
                next_step_instruction=engine.reason,
                exploration_query="",
            )
        if engine.decision == "replan":
            return PlannerControllerOutput(
                action="replan",
                next_step_instruction=engine.reason,
                exploration_query="",
            )
        return PlannerControllerOutput(
            action="continue",
            next_step_instruction=PlannerV2._act_controller_hint(engine),
            exploration_query="",
        )

    _TOOL_TO_STEP_ACTION: dict[str, str] = PLANNER_TOOL_TO_PLAN_STEP_ACTION

    @staticmethod
    def _primary_input_from_spec(spec: PlannerEngineStepSpec) -> str:
        inp = (spec.input or "").strip()
        if inp:
            return inp[:8000]
        md = spec.metadata or {}
        for k in ("query", "path", "command", "instruction"):
            v = md.get(k)
            if v is not None and str(v).strip():
                return str(v).strip()[:8000]
        return ""

    def _resolve_act_step_spec(
        self, engine: PlannerEngineOutput, instruction: str
    ) -> PlannerEngineStepSpec:
        if engine.step is not None:
            return engine.step
        return PlannerEngineStepSpec(action="search", input=instruction[:8000])

    @staticmethod
    def _allocate_engine_step_slots(
        prior: PlanDocument | None, decision: str
    ) -> tuple[int, int, int, int]:
        """
        Numeric suffixes and 1-based indices for synthesized PlanSteps.

        Returns (work_suffix, work_index, finish_suffix, finish_index) for decision \"act\".
        For stop/explore/replan, only the first two values are used (single finish step).
        """
        max_idx = 0
        max_n = 0
        if prior is not None and prior.steps:
            max_idx = max(s.index for s in prior.steps)
            for s in prior.steps:
                m = re.match(r"^s(\d+)$", (s.step_id or "").strip(), re.IGNORECASE)
                if m:
                    max_n = max(max_n, int(m.group(1)))
            if max_n == 0:
                max_n = len(prior.steps)
        d = (decision or "").strip().lower()
        if d in ("stop", "explore", "replan"):
            fs = max_n + 1
            fi = max_idx + 1
            return (fs, fi, fs, fi)
        ws = max_n + 1
        wi = max_idx + 1
        fs = max_n + 2
        fi = max_idx + 2
        return (ws, wi, fs, fi)

    def _step_spec_to_plan_step(
        self,
        spec: PlannerEngineStepSpec,
        instruction: str,
        *,
        step_id: str = "s1",
        index: int = 1,
    ) -> PlanStep:
        a = spec.action
        inp = self._primary_input_from_spec(spec)
        md = spec.metadata or {}
        goal = (inp or instruction)[:2000]

        if a == "search":
            inputs: dict[str, Any] = {}
            q = inp or str(md.get("query") or "").strip()
            if q:
                inputs["query"] = q
            return PlanStep(
                step_id=step_id,
                index=index,
                type="explore",
                goal=goal,
                action="search",
                inputs=inputs,
                outputs={},
                dependencies=[],
            )
        if a == "open_file":
            inputs = {}
            pth = inp or str(md.get("path") or "").strip()
            if pth:
                inputs["path"] = pth
            return PlanStep(
                step_id=step_id,
                index=index,
                type="analyze",
                goal=goal,
                action="open_file",
                inputs=inputs,
                outputs={},
                dependencies=[],
            )
        if a == "edit":
            # Keep consistent with _validate_act_tool_inputs(edit).
            md_p = str(md.get("path") or "").strip()
            md_i = str(md.get("instruction") or "").strip()
            inp_s = (inp or "").strip()
            inputs = {}
            if md_p:
                inputs["path"] = md_p
                if inp_s or md_i:
                    inputs["instruction"] = inp_s or md_i
            elif md_i and inp_s:
                inputs["path"] = inp_s
                inputs["instruction"] = md_i
            else:
                if inp_s:
                    inputs["instruction"] = inp_s
                if md_p:
                    inputs["path"] = md_p
            return PlanStep(
                step_id=step_id,
                index=index,
                type="modify",
                goal=goal,
                action="edit",
                inputs=inputs,
                outputs={},
                dependencies=[],
            )
        if a == "run_tests":
            inputs = {}
            if inp:
                inputs["description"] = inp
            return PlanStep(
                step_id=step_id,
                index=index,
                type="validate",
                goal=goal,
                action="run_tests",
                inputs=inputs,
                outputs={},
                dependencies=[],
            )
        if a == "shell":
            cmd = inp or str(md.get("command") or "").strip()
            return PlanStep(
                step_id=step_id,
                index=index,
                type="explore",
                goal=goal,
                action="shell",
                inputs={"command": cmd} if cmd else {},
                outputs={},
                dependencies=[],
            )
        return PlanStep(
            step_id=step_id,
            index=index,
            type="explore",
            goal=goal,
            action="search",
            inputs={},
            outputs={},
            dependencies=[],
        )

    def _synthesize_steps_from_engine(
        self,
        engine: PlannerEngineOutput,
        instruction: str,
        *,
        prior_plan_document: PlanDocument | None = None,
    ) -> list[PlanStep]:
        def _finish(sid: str, idx: int, deps: list[str]) -> PlanStep:
            return PlanStep(
                step_id=sid,
                index=idx,
                type="finish",
                goal="Complete",
                action="finish",
                inputs={},
                outputs={},
                dependencies=deps,
            )

        d = engine.decision
        if d in ("stop", "explore", "replan"):
            fs, fi, _, _ = self._allocate_engine_step_slots(prior_plan_document, d)
            steps = [
                _finish(f"s{fs}", fi, []),
            ]
        else:
            spec = self._resolve_act_step_spec(engine, instruction)
            ws, wi, fs, fi = self._allocate_engine_step_slots(prior_plan_document, d)
            wid, fid = f"s{ws}", f"s{fs}"
            work = self._step_spec_to_plan_step(spec, instruction, step_id=wid, index=wi)
            fin = _finish(fid, fi, [wid])
            steps = [work, fin]

        # Continuation step_id suffixes (s3, s4, …) avoid collisions with prior_plan_document,
        # but PlanValidator requires each PlanDocument.steps to use index 1..len(steps) exactly.
        return [s.model_copy(update={"index": i}) for i, s in enumerate(steps, start=1)]

    @staticmethod
    def _infer_planner_tool(engine: PlannerEngineOutput) -> PlannerPlannerTool:
        d = engine.decision
        if d == "explore":
            return "explore"
        if d in ("stop", "replan"):
            return "none"
        spec = engine.step
        if spec is None:
            return "search_code"
        mapping: dict[str, PlannerPlannerTool] = {
            "search": "search_code",
            "open_file": "open_file",
            "shell": "run_shell",
            "edit": "edit",
            "run_tests": "run_tests",
        }
        return mapping.get(spec.action, "search_code")

    @staticmethod
    def _reason_implies_planner_thinks_task_done(reason: str) -> bool:
        """Heuristic: model used act + empty tool/search but prose says the task is finished."""
        r = (reason or "").lower()
        if not r.strip():
            return False
        needles = (
            "can be answered",
            "already answered",
            "answered from",
            "from the current findings",
            "from current findings",
            "task is complete",
            "task complete",
            "nothing more to do",
            "no further action",
            "no additional",
            "sufficient information",
            "fully addressed",
            "does not require",
            "no tool needed",
            "does not need",
        )
        return any(n in r for n in needles)

    @staticmethod
    def _coerce_stop_when_act_semantically_complete(engine: PlannerEngineOutput) -> PlannerEngineOutput:
        """
        Small models emit decision=act, tool=none, empty search, while reason says the task is done.
        Normalize to stop before tool inference / pairing (avoids invalid act + search_code w/ empty query).
        """
        if engine.decision != "act":
            return engine
        if not PlannerV2._reason_implies_planner_thinks_task_done(engine.reason):
            return engine
        raw_tool = str(engine.tool or "").strip().lower()
        spec = engine.step
        if raw_tool in ("none", ""):
            logger.info(
                "planner_output_normalization %s",
                json.dumps(
                    {
                        "component": "planner_output_normalization",
                        "field": "decision",
                        "reason": "act_mislabeled_stop",
                        "prior_tool": raw_tool,
                        "reason_preview": (engine.reason or "")[:240],
                    },
                    default=str,
                ),
            )
            return engine.model_copy(
                update={"decision": "stop", "tool": "none", "query": "", "step": None}
            )
        if raw_tool == "search_code":
            pin = PlannerV2._primary_input_from_spec(spec) if spec is not None else ""
            if not (pin or "").strip():
                logger.info(
                    "planner_output_normalization %s",
                    json.dumps(
                        {
                            "component": "planner_output_normalization",
                            "field": "decision",
                            "reason": "act_empty_search_stop_intent",
                            "prior_tool": raw_tool,
                            "reason_preview": (engine.reason or "")[:240],
                        },
                        default=str,
                    ),
                )
                return engine.model_copy(
                    update={"decision": "stop", "tool": "none", "query": "", "step": None}
                )
        return engine

    @staticmethod
    def _last_planner_validation_block(session: Any) -> str:
        if not isinstance(session, SessionMemory):
            return ""
        msg = (session.last_planner_validation_error or "").strip()
        if not msg:
            return ""
        return (
            "\n--------------------------------\n"
            "LAST PLANNER JSON VALIDATION ERROR (fix on next output; do not repeat):\n"
            f"{msg[:2000]}\n"
            "--------------------------------"
        )

    @staticmethod
    def _validation_error_repair_appendix(err: BaseException) -> str:
        return (
            "\n\n--------------------------------\n"
            "VALIDATION ERROR (previous JSON was rejected by the runtime):\n"
            f"{str(err)[:2000]}\n"
            "--------------------------------"
        )

    def _normalize_engine_tool(
        self, engine: PlannerEngineOutput, strict_tool: bool
    ) -> PlannerEngineOutput:
        if engine.tool != "none":
            return engine
        if strict_tool:
            d = engine.decision
            if d in ("act", "explore"):
                raise PlanValidationError(
                    f'strict_tool mode: explicit non-"none" "tool" required for decision {d!r}'
                )
            return engine
        inferred = self._infer_planner_tool(engine)
        return engine.model_copy(update={"tool": inferred})

    @staticmethod
    def _concrete_hint_from_recent_steps(memory: SessionMemory) -> str:
        """Last path-like or code-ish snippet from compressed history (deterministic)."""
        for step in reversed(memory.recent_steps):
            s = (step.summary or "").strip()
            if not s:
                continue
            if ".py" in s or "/" in s:
                return s[:500]
            if re.search(r"\b[\w./-]+\.(py|ts|js|go|rs|java)\b", s):
                return s[:500]
        return ""

    @staticmethod
    def _explore_cap_search_query(memory: SessionMemory, instruction: str) -> str:
        """
        When intent_anchor.target is empty or vague, do not chain vague last_user_instruction.
        Prefer entity, recent-step concrete hint, active_file, concrete instruction, then fail-safe.
        """
        target = (memory.intent_anchor.target or "").strip()
        if target and not is_vague_user_text(target):
            return target[:500]
        entity = (memory.intent_anchor.entity or "").strip()
        if entity:
            return entity[:500]
        rs = PlannerV2._concrete_hint_from_recent_steps(memory)
        if rs:
            return rs
        af = memory.active_file
        if af and str(af).strip():
            from pathlib import Path

            name = Path(str(af).strip()).name
            if name:
                return f"relevant code for {name}"[:500]
        ins = (instruction or "").strip()
        if ins and not is_vague_user_text(ins):
            return ins[:500]
        lu = (memory.last_user_instruction or "").strip()
        if lu and not is_vague_user_text(lu):
            return lu[:500]
        ct = (memory.current_task or "").strip()
        if ct:
            return f"relevant code for {ct}"[:500]
        return "relevant code for session task"[:500]

    @staticmethod
    def _apply_explore_cap_override(
        engine: PlannerEngineOutput,
        memory: SessionMemory | None,
        instruction: str,
    ) -> tuple[PlannerEngineOutput, bool]:
        if memory is None or int(memory.explore_streak) < 3:
            return engine, False
        if engine.decision != "explore":
            return engine, False
        q = PlannerV2._explore_cap_search_query(memory, instruction)
        reason = ((engine.reason or "").strip() + " [explore_cap_override→act+search_code]").strip()
        new_eng = engine.model_copy(
            update={
                "decision": "act",
                "tool": "search_code",
                "query": "",
                "reason": reason[:2000],
                "step": PlannerEngineStepSpec(action="search", input=q, metadata={}),
            }
        )
        return new_eng, True

    @staticmethod
    def _maybe_warn_tool_selection_heuristics(
        engine: PlannerEngineOutput, memory: SessionMemory | None
    ) -> None:
        """Observability only — does not mutate engine (7B may ignore prompt rules)."""
        if memory is None:
            return
        if engine.decision == "explore":
            af = memory.active_file
            if af and str(af).strip():
                logger.warning(
                    "planner_tool_heuristic_warning code=explore_while_active_file_set active_file=%r",
                    str(af)[:240],
                )
            syms = memory.active_symbols or []
            if syms:
                logger.warning(
                    "planner_tool_heuristic_warning code=explore_while_active_symbols_set symbols=%r",
                    syms[:5],
                )
        if engine.decision == "act" and engine.tool == "search_code":
            af2 = memory.active_file
            if af2 and str(af2).strip():
                logger.warning(
                    "planner_tool_heuristic_warning code=search_code_while_active_file_known "
                    "active_file=%r (selection rules prefer open_file when path is known)",
                    str(af2)[:240],
                )

    @staticmethod
    def _validate_engine_tool_pairing(engine: PlannerEngineOutput) -> None:
        tool = engine.tool
        d = engine.decision
        if d == "explore":
            if tool != "explore":
                raise PlanValidationError(f'decision "explore" requires tool "explore", got {tool!r}')
            return
        if d in ("stop", "replan"):
            if tool != "none":
                raise PlanValidationError(f'decision "{d}" requires tool "none", got {tool!r}')
            return
        act_tools = PLANNER_ACT_TOOL_IDS
        if tool not in act_tools:
            raise PlanValidationError(f'decision "act" requires a concrete act tool, got {tool!r}')
        want = PlannerV2._TOOL_TO_STEP_ACTION[tool]
        spec = engine.step
        if spec is None:
            raise PlanValidationError('decision "act" requires a non-null "step"')
        if spec.action != want:
            raise PlanValidationError(
                f'tool {tool!r} requires step.action {want!r}, got {spec.action!r}'
            )
        PlannerV2._validate_act_tool_inputs(tool, spec)

    @staticmethod
    def _validate_act_tool_inputs(tool: str, spec: PlannerEngineStepSpec) -> None:
        """Strict planner-side inputs for act tools (fail before synthesis / executor)."""
        md = spec.metadata or {}
        inp = (spec.input or "").strip()
        if tool == "search_code":
            q = inp or str(md.get("query") or "").strip()
            if not q:
                raise PlanValidationError(
                    'tool "search_code" requires non-empty search query (step.input or metadata.query)'
                )
            return
        if tool == "open_file":
            pth = inp or str(md.get("path") or "").strip()
            if not pth:
                raise PlanValidationError(
                    'tool "open_file" requires non-empty path (step.input or metadata.path)'
                )
            return
        if tool == "run_shell":
            cmd = inp or str(md.get("command") or "").strip()
            if not cmd:
                raise PlanValidationError(
                    'tool "run_shell" requires non-empty command (step.input or metadata.command)'
                )
            return
        if tool == "edit":
            # Path: metadata.path, or step.input as path when metadata.instruction is set (unambiguous).
            # Instruction: step.input or metadata.instruction when path is in metadata.
            md_p = str(md.get("path") or "").strip()
            md_i = str(md.get("instruction") or "").strip()
            inp_s = (inp or "").strip()
            if md_p:
                pth = md_p
                instr = inp_s or md_i
            elif md_i and inp_s:
                pth, instr = inp_s, md_i
            else:
                pth, instr = md_p, inp_s or md_i
            if not pth:
                raise PlanValidationError(
                    'tool "edit" requires non-empty path (metadata.path, or step.input when '
                    "metadata.instruction is set)"
                )
            if not instr:
                raise PlanValidationError(
                    'tool "edit" requires non-empty instruction (step.input or metadata.instruction)'
                )
            return
        if tool == "run_tests":
            # Optional inputs (registry required_args=[]); planner does not require path/query.
            return
        raise PlanValidationError(f"internal: act tool {tool!r} missing input validation branch")

    def _build_plan(
        self,
        raw: dict[str, Any],
        instruction: str,
        *,
        require_controller_json: bool = False,
        task_mode: Optional[str] = None,
        planner_context: Optional[PlannerPlanContext] = None,
        prior_plan_document: Optional[PlanDocument] = None,
    ) -> tuple[PlanDocument, bool]:
        dec_raw = raw.get("decision")
        has_decision = isinstance(dec_raw, str) and str(dec_raw).strip() != ""
        if has_decision:
            return self._build_plan_from_engine_json(
                raw,
                instruction,
                require_controller_json=require_controller_json,
                task_mode=task_mode,
                planner_context=planner_context,
                prior_plan_document=prior_plan_document,
            )
        # Legacy: Replanner / tests may still emit steps + controller without "decision".
        if require_controller_json and isinstance(raw.get("controller"), dict):
            return self._build_plan_legacy_steps(raw, instruction), False
        if require_controller_json:
            raise PlanValidationError(
                'Missing required "decision" or legacy "controller" for orchestration JSON'
            )
        return self._build_plan_legacy_steps(raw, instruction), False

    def _build_plan_from_engine_json(
        self,
        raw: dict[str, Any],
        instruction: str,
        *,
        require_controller_json: bool = False,
        task_mode: Optional[str] = None,
        planner_context: Optional[PlannerPlanContext] = None,
        prior_plan_document: Optional[PlanDocument] = None,
    ) -> tuple[PlanDocument, bool]:
        created = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        plan_id = f"plan_{uuid.uuid4().hex[:12]}"
        try:
            d_raw = str(raw.get("decision") or "").strip().lower()
            if d_raw not in ("act", "explore", "replan", "stop"):
                raise ValueError("invalid decision")
            engine = PlannerEngineOutput.model_validate(
                {
                    "decision": d_raw,
                    "tool": raw.get("tool"),
                    "reason": str(raw.get("reason") or ""),
                    "query": str(raw.get("query") or ""),
                    "step": raw.get("step"),
                }
            )
        except (ValidationError, ValueError, TypeError) as e:
            raise PlanValidationError(f"Invalid decision engine JSON (check decision/tool/step): {e}") from e

        # Small models often echo "query" alongside replan/stop/act; strip so pairing validation
        # and tool policy run instead of failing early on schema noise.
        d_norm = str(engine.decision or "").strip().lower()
        _q_strip = (engine.query or "").strip()
        if d_norm != "explore" and _q_strip:
            logger.info(
                "planner_output_normalization %s",
                json.dumps(
                    {
                        "component": "planner_output_normalization",
                        "field": "query",
                        "reason": "invalid_for_decision",
                        "decision": d_norm,
                        "query_preview": _q_strip[:240],
                    },
                    default=str,
                ),
            )
            engine = engine.model_copy(update={"query": ""})

        engine = self._coerce_stop_when_act_semantically_complete(engine)

        mem = (
            planner_context.session
            if planner_context is not None and isinstance(planner_context.session, SessionMemory)
            else None
        )
        engine = self._normalize_engine_tool(engine, self._strict_tool)
        apply_tool_policy(engine, self._tool_policy)
        engine, override_triggered = self._apply_explore_cap_override(engine, mem, instruction)
        apply_tool_policy(engine, self._tool_policy)
        self._maybe_warn_tool_selection_heuristics(engine, mem)
        self._validate_engine_tool_pairing(engine)
        self._validate_engine_pairing(engine)
        self._validate_engine_task_mode(engine, task_mode)

        steps = self._synthesize_steps_from_engine(
            engine, instruction, prior_plan_document=prior_plan_document
        )
        sources = [
            PlanSource(type="other", ref="decision_engine", summary=engine.reason[:500] or "planner decision")
        ]
        risks = [
            PlanRisk(
                risk="Execution depends on tool and environment",
                impact="medium",
                mitigation="Re-evaluate after the next step",
            )
        ]
        completion = ["Next decision after this step's outcome"]
        metadata = PlanMetadata(created_at=created, version=1)
        ctrl = self._sync_controller_from_engine(engine)

        return (
            PlanDocument(
                plan_id=plan_id,
                instruction=instruction,
                understanding=engine.reason,
                sources=sources,
                steps=steps,
                risks=risks,
                completion_criteria=completion,
                metadata=metadata,
                engine=engine,
                controller=ctrl,
            ),
            override_triggered,
        )

    def _build_plan_legacy_steps(
        self,
        raw: dict[str, Any],
        instruction: str,
    ) -> PlanDocument:
        created = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        plan_id = f"plan_{uuid.uuid4().hex[:12]}"

        ctrl_out: Optional[PlannerControllerOutput] = None
        cr = raw.get("controller")
        if isinstance(cr, dict):
            try:
                ctrl_out = PlannerControllerOutput.model_validate(cr)
            except ValidationError as e:
                raise PlanValidationError(f"Invalid controller object: {e}") from e
        if ctrl_out is not None:
            self._validate_controller_pairing(ctrl_out)

        steps_raw = raw.get("steps") or []
        if not isinstance(steps_raw, list):
            steps_raw = []

        steps: list[PlanStep] = []

        steps_raw = _trim_plan_steps_preserving_finish(steps_raw, self._policy.max_steps)
        for idx, s in enumerate(steps_raw):
            if not isinstance(s, dict):
                continue
            # CRITICAL / TODO(replanner): Skip risk blobs nested under "steps" (invalid JSON).
            # Replanner path should log + surface malformed plans instead of only dropping rows.
            if s.get("risk") is not None and s.get("step_id") is None and s.get("action") is None:
                continue
            sid = str(s.get("step_id") or f"s{idx + 1}")
            stype = s.get("type")
            action = s.get("action")
            if stype is None and action is None:
                continue
            if stype is None:
                stype = action
            if action is None:
                action = stype
            # CRITICAL / TODO(replanner): Client-side fixup; replace with structured output + replan.
            action, stype = _coerce_step_action_and_type(action, stype)
            deps = s.get("dependencies")
            if not isinstance(deps, list):
                deps = []
            deps = [str(d) for d in deps]
            inputs = s.get("inputs")
            if not isinstance(inputs, dict):
                inputs = {}
            outputs = s.get("outputs")
            if not isinstance(outputs, dict):
                outputs = {}

            try:
                steps.append(
                    PlanStep(
                        step_id=sid,
                        index=idx + 1,
                        type=stype,
                        goal=str(s.get("goal") or ""),
                        action=action,
                        inputs=inputs,
                        outputs=outputs,
                        dependencies=deps,
                    )
                )
            except ValidationError as e:
                raise PlanValidationError(f"Invalid plan step from LLM: {e}") from e

        sources = self._normalize_sources(raw.get("sources"))
        risks = self._normalize_risks(raw.get("risks"))
        completion = raw.get("completion_criteria") or []
        if not isinstance(completion, list):
            completion = []
        completion = [str(c) for c in completion if c is not None and str(c).strip()]
        if not completion:
            completion = ["All plan steps completed successfully"]

        metadata = PlanMetadata(created_at=created, version=1)

        return PlanDocument(
            plan_id=plan_id,
            instruction=instruction,
            understanding=str(raw.get("understanding") or ""),
            sources=sources,
            steps=steps,
            risks=risks,
            completion_criteria=completion,
            metadata=metadata,
            engine=None,
            controller=ctrl_out,
        )

    def _normalize_sources(self, raw: Any) -> list[PlanSource]:
        if not isinstance(raw, list) or not raw:
            return [PlanSource(type="other", ref="exploration", summary="Derived from exploration phase")]
        out: list[PlanSource] = []
        for item in raw:
            if isinstance(item, str):
                out.append(PlanSource(type="other", ref=item[:500], summary=""))
                continue
            if not isinstance(item, dict):
                continue
            t = item.get("type", "other")
            if t not in ("file", "search", "other"):
                t = "other"
            ref = str(item.get("ref") or "")
            summ = str(item.get("summary") or "")
            if not ref:
                continue
            out.append(PlanSource(type=t, ref=ref, summary=summ))
        return out or [
            PlanSource(type="other", ref="exploration", summary="Derived from exploration phase")
        ]

    def _normalize_risks(self, raw: Any) -> list[PlanRisk]:
        if not isinstance(raw, list):
            raw = []
        out: list[PlanRisk] = []
        for item in raw:
            if isinstance(item, str):
                out.append(PlanRisk(risk=item, impact="medium", mitigation="Monitor and adjust"))
                continue
            if not isinstance(item, dict):
                continue
            risk = str(item.get("risk") or item.get("description") or "Unknown risk")
            impact = item.get("impact", "medium")
            if impact not in ("low", "medium", "high"):
                impact = "medium"
            mit = str(item.get("mitigation") or "Re-evaluate after each step")
            out.append(PlanRisk(risk=risk, impact=impact, mitigation=mit))
        if not out:
            out.append(
                PlanRisk(
                    risk="Plan execution may fail on environment or missing context",
                    impact="medium",
                    mitigation="Re-run exploration or replan with updated context",
                )
            )
        return out
