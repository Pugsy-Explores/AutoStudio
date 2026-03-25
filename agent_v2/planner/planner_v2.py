"""
Planner v2 — Phase 4: PlannerInput → PlanDocument (STRICT).

Does not call tools or execute steps. LLM is injected as generate_fn(prompt) -> str
(typically wired to call_reasoning_model in bootstrap).
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from pydantic import ValidationError

from agent_v2.schemas.execution import ErrorType
from agent_v2.schemas.plan import (
    PlanDocument,
    PlanMetadata,
    PlanRisk,
    PlanSource,
    PlanStep,
    PlanStepExecution,
    PlanStepFailure,
    PlanStepLastResult,
)
from agent_v2.schemas.policies import ExecutionPolicy
from agent_v2.schemas.replan import PlannerInput, ReplanContext
from agent_v2.schemas.exploration import ExplorationResult
from agent_v2.observability.langfuse_helpers import (
    LANGFUSE_GENERATION_PROMPT_INPUT_MAX_CHARS,
    langfuse_generation_end_with_usage,
    langfuse_generation_input_with_prompt,
    try_langfuse_generation,
)
from agent_v2.validation.plan_validator import PlanValidationError, PlanValidator

DEFAULT_POLICY = ExecutionPolicy(max_steps=8, max_retries_per_step=2, max_replans=2)

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
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("LLM output must be a JSON object")
    return data


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


class PlannerV2:
    """
    Production planner: exploration- or replan-grounded structured plan only.

    Args:
        generate_fn: Callable taking a single prompt string, returning model text
            (must contain a JSON object with steps, understanding, etc.).
        policy: ExecutionPolicy; max_retries_per_step seeds PlanStep.execution.max_attempts.
    """

    def __init__(
        self,
        generate_fn: Callable[[str], str],
        policy: Optional[ExecutionPolicy] = None,
    ):
        self._generate_fn = generate_fn
        self._policy = policy or DEFAULT_POLICY

    def plan(
        self,
        instruction: str,
        planner_input: PlannerInput,
        deep: bool = False,
        langfuse_trace: Any = None,
        obs: Any = None,
    ) -> PlanDocument:
        # LIVE-TEST-002: Infer task mode from instruction
        task_mode = self._infer_task_mode(instruction)

        lf: Any = None
        if obs is not None and getattr(obs, "langfuse_trace", None) is not None:
            lf = obs.langfuse_trace
        elif langfuse_trace is not None:
            lf = langfuse_trace

        prompt = self._build_prompt(instruction, planner_input, deep, task_mode=task_mode)
        gen_name = "planner_replan" if isinstance(planner_input, ReplanContext) else "planner"
        planning_span: Any = None
        if lf is not None and hasattr(lf, "span"):
            try:
                planning_span = lf.span("planning", input={"instruction": instruction[:500]})
            except Exception:
                planning_span = None
        try:
            raw = self._call_llm(
                prompt,
                langfuse_trace=lf,
                parent_span=planning_span,
                gen_name=gen_name,
                generation_metadata={
                    "prompt_chars": len(prompt),
                    "deep": deep,
                    "is_replan": isinstance(planner_input, ReplanContext),
                },
            )
        finally:
            if planning_span is not None:
                try:
                    planning_span.end()
                except Exception:
                    pass
        plan = self._build_plan(raw, instruction)
        if not isinstance(plan, PlanDocument):
            raise TypeError(
                f"Planner internal error: expected PlanDocument, got {type(plan).__name__}"
            )
        
        PlanValidator.validate_plan(plan, policy=self._policy, task_mode=task_mode)
        return plan

    def _build_prompt(
        self,
        instruction: str,
        planner_input: PlannerInput,
        deep: bool,
        task_mode: Optional[str] = None,
    ) -> str:
        if isinstance(planner_input, ReplanContext):
            return self._build_replan_prompt(instruction, planner_input, deep, task_mode=task_mode)
        if isinstance(planner_input, ExplorationResult):
            return self._build_exploration_prompt(instruction, planner_input, deep, task_mode=task_mode)
        raise TypeError(f"Unsupported planner_input type: {type(planner_input)!r}")

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

    def _build_exploration_prompt(
        self,
        instruction: str,
        exploration: ExplorationResult,
        deep: bool,
        task_mode: Optional[str] = None,
    ) -> str:
        key_findings = "\n".join(f"- {k}" for k in exploration.summary.key_findings) or "(none)"
        knowledge_gaps = "\n".join(f"- {g}" for g in exploration.summary.knowledge_gaps) or "(none)"
        sources_lines = "\n".join(
            f"- {item.source.ref} ({item.type})" for item in exploration.items
        ) or "(no exploration items)"
        md = getattr(exploration, "metadata", None)
        source_summary = getattr(md, "source_summary", None) if md is not None else None
        if isinstance(source_summary, dict):
            ss_symbol = int(source_summary.get("symbol", 0) or 0)
            ss_line = int(source_summary.get("line", 0) or 0)
            ss_head = int(source_summary.get("head", 0) or 0)
        else:
            ss_symbol = ss_line = ss_head = 0

        item_lines: list[str] = []
        for item in exploration.items:
            ref = item.source.ref
            rs = item.read_source or "unknown"
            snippet = (item.snippet or "").strip()
            if snippet:
                snippet = snippet[:600]
            item_lines.append(f"- file: {ref}")
            item_lines.append(f"  read_source: {rs}")
            item_lines.append(f"  snippet: {snippet if snippet else '(none)'}")
            item_lines.append(f"  summary: {item.content.summary}")
            item_lines.append("")
        items_block = "\n".join(item_lines).rstrip() if item_lines else "(no exploration items)"
        deep_extra = ""
        if deep:
            deep_extra = (
                "\nDEEP PLANNING: Decompose into smaller verifiable steps, add risk analysis "
                "per step, and prefer conservative dependencies. Still output STRICT JSON only.\n"
            )
        
        # LIVE-TEST-002: Add task mode constraints
        allowed_actions = "search, open_file, edit, run_tests, shell, finish"
        task_mode_constraint = ""
        if task_mode == "read_only":
            allowed_actions = "search, open_file, finish"
            task_mode_constraint = (
                "\n⚠️  CRITICAL: This is a READ-ONLY task (explain/find/analyze).\n"
                "You MUST NOT use: edit, run_tests, shell\n"
                "Only allowed actions: search, open_file, finish\n"
            )

        return f"""You are a senior software engineer planning a solution.

TASK:
{instruction}

EXPLORATION SUMMARY:
{exploration.summary.overall}

EXPLORATION SOURCES:
- symbol reads: {ss_symbol}
- line reads: {ss_line}
- header reads: {ss_head}

EXPLORATION ITEMS:
{items_block}

KEY FINDINGS:
{key_findings}

KNOWLEDGE GAPS:
{knowledge_gaps}

SOURCES (from exploration only; do not invent paths):
{sources_lines}
{deep_extra}{task_mode_constraint}
REQUIREMENTS:

1. Create a step-by-step plan as STRICT JSON only (no markdown outside the JSON object).
2. Top-level keys MUST include: "steps", "understanding", "sources", "risks", "completion_criteria".
3. Each entry in "steps" MUST have:
   - step_id (string)
   - type (one of: explore, analyze, modify, validate, finish)
   - goal (string)
   - action (one of: {allowed_actions})
   - dependencies (array of step_id strings; use [] if none)
   - inputs (object, may be {{}})
   - outputs (object, may be {{}})
4. Allowed actions: {allowed_actions}
5. Indices will be assigned by the system; you may omit "index" or set it 1..N in order.
6. Constraints:
   - at most 8 steps
   - must include exactly one finish step as the LAST step (highest index): type=finish, action=finish
   - must NOT hallucinate files or APIs not supported by SOURCES above when citing paths
   - include at least one risk object with keys risk, impact (low|medium|high), mitigation
7. Evidence-use requirements (critical):
   - Use EXPLORATION ITEMS (snippets + read_source) as primary grounding, not just the exploration summary.
   - When referencing code, use the exact file paths shown in EXPLORATION ITEMS / SOURCES.
   - Prefer plan steps that open/read the specific files/symbols implied by snippets.
8. Tool-argument requirements (critical correctness):
   - If action == "open_file": inputs MUST include a non-empty "path" that exactly matches a file path from SOURCES/EXPLORATION ITEMS.
   - If action == "search": inputs MUST include a non-empty "query".
   - Never emit placeholder targets like "alternative_sources" or "community_discussions" as if they were files.
9. "sources" array: objects with type (file|search|other), ref, summary — align with exploration when possible.
10. "completion_criteria": non-empty array of strings.

Return a single JSON object only.
"""

    def _build_replan_prompt(
        self,
        instruction: str,
        ctx: ReplanContext,
        deep: bool,
        task_mode: Optional[str] = None,
    ) -> str:
        fc = ctx.failure_context
        completed = "\n".join(f"- {c.step_id}: {c.summary}" for c in ctx.completed_steps) or "(none)"
        es = ctx.exploration_summary
        explore_block = ""
        if es is not None:
            kf = "\n".join(f"- {x}" for x in es.key_findings) or "(none)"
            kg = "\n".join(f"- {x}" for x in es.knowledge_gaps) or "(none)"
            explore_block = f"""
PRIOR EXPLORATION (summary):
Overall: {es.overall}
Key findings:
{kf}
Knowledge gaps:
{kg}
"""
        deep_extra = ""
        if deep:
            deep_extra = (
                "\nDEEP PLANNING: Analyze the failure, adjust strategy, preserve completed work "
                "where possible. STRICT JSON only.\n"
            )
        
        # LIVE-TEST-002: Add task mode constraints
        allowed_actions = "search, open_file, edit, run_tests, shell, finish"
        task_mode_constraint = ""
        if task_mode == "read_only":
            allowed_actions = "search, open_file, finish"
            task_mode_constraint = (
                "\n⚠️  CRITICAL: This is a READ-ONLY task (explain/find/analyze).\n"
                "You MUST NOT use: edit, run_tests, shell\n"
                "Only allowed actions: search, open_file, finish\n"
            )

        return f"""You are a senior software engineer revising a plan after execution failure.

ORIGINAL TASK:
{instruction}

FAILURE:
- step_id: {fc.step_id}
- error_type: {fc.error.type}
- message: {fc.error.message}
- attempts: {fc.attempts}
- last_output_summary: {fc.last_output_summary}

COMPLETED STEPS:
{completed}
{explore_block}
{deep_extra}{task_mode_constraint}
REQUIREMENTS:

1. Output STRICT JSON only with keys: steps, understanding, sources, risks, completion_criteria.
2. Follow the same step schema as initial planning (type, action, goal, step_id, dependencies, inputs, outputs).
3. At most 8 steps; final step MUST be type=finish, action=finish.
4. Allowed actions: {allowed_actions}
5. At least one risk with risk, impact, mitigation.
6. Address the failure and remaining work; do not repeat failed assumptions.

Return a single JSON object only.
"""

    def _call_llm(
        self,
        prompt: str,
        *,
        langfuse_trace: Any = None,
        parent_span: Any = None,
        gen_name: str = "planner",
        generation_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        last_err: Optional[Exception] = None
        for attempt in range(2):
            gen = try_langfuse_generation(
                parent_span,
                langfuse_trace,
                name=gen_name,
                input=langfuse_generation_input_with_prompt(
                    prompt,
                    extra={"attempt": attempt},
                ),
            )
            try:
                text = self._generate_fn(prompt)
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
                    prompt = (
                        prompt
                        + "\n\nYour previous output was not valid JSON. "
                        "Reply with ONE JSON object only, no prose or fences."
                    )
        raise PlanValidationError(f"Planner LLM output was not valid JSON: {last_err}") from last_err

    def _build_plan(self, raw: dict[str, Any], instruction: str) -> PlanDocument:
        created = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        plan_id = f"plan_{uuid.uuid4().hex[:12]}"
        steps_raw = raw.get("steps") or []
        if not isinstance(steps_raw, list):
            steps_raw = []

        steps: list[PlanStep] = []
        max_attempts = self._policy.max_retries_per_step

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

            exec_raw = s.get("execution")
            if isinstance(exec_raw, dict):
                lr = exec_raw.get("last_result")
                last_result = PlanStepLastResult()
                if isinstance(lr, dict):
                    last_result = PlanStepLastResult(
                        success=lr.get("success"),
                        error=lr.get("error"),
                        output_summary=lr.get("output_summary"),
                    )
                st = exec_raw.get("status", "pending")
                if st not in ("pending", "in_progress", "completed", "failed"):
                    st = "pending"
                execution = PlanStepExecution(
                    status=st,
                    attempts=int(exec_raw.get("attempts", 0)),
                    max_attempts=max_attempts,
                    started_at=exec_raw.get("started_at"),
                    completed_at=exec_raw.get("completed_at"),
                    last_result=last_result,
                )
            else:
                execution = PlanStepExecution(max_attempts=max_attempts)

            fail_raw = s.get("failure")
            if isinstance(fail_raw, dict):
                ft_raw = fail_raw.get("failure_type")
                failure_type = None
                if ft_raw is not None:
                    try:
                        failure_type = ErrorType(str(ft_raw))
                    except ValueError:
                        failure_type = None
                rs = fail_raw.get("retry_strategy", "retry_same")
                if rs not in ("retry_same", "adjust_inputs", "abort"):
                    rs = "retry_same"
                failure = PlanStepFailure(
                    is_recoverable=bool(fail_raw.get("is_recoverable", True)),
                    failure_type=failure_type,
                    retry_strategy=rs,
                    replan_required=bool(fail_raw.get("replan_required", False)),
                )
            else:
                failure = PlanStepFailure()

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
                        execution=execution,
                        failure=failure,
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
