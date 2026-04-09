"""
Phase 5 — Plan argument generator: LLM fills tool arguments only; tool name comes from ExecutionTask.

Seam module: imports agent.* for model_client and react_schema (same boundary as bootstrap).
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable

from agent.execution.react_schema import validate_action
from agent.models.model_client import call_reasoning_model
from agent.tools.react_registry import get_tool_by_name

from agent_v2.observability.langfuse_helpers import (
    LANGFUSE_GENERATION_PROMPT_INPUT_MAX_CHARS,
    langfuse_generation_end_with_usage,
    langfuse_generation_input_with_prompt,
    try_langfuse_generation,
)
from agent_v2.schemas.execution_task import ExecutionTask


def _model_task_for_tool_args(state: Any) -> str:
    """Plan-safe execution (``state.context['plan_safe_execute']``) vs act / plan_execute."""
    ctx = getattr(state, "context", None)
    if isinstance(ctx, dict) and ctx.get("plan_safe_execute"):
        return "PLANNER_TOOL_ARGS_PLAN"
    return "PLANNER_TOOL_ARGS_ACT"


def _strip_json_fence(text: str) -> str:
    text = (text or "").strip()
    if "```" in text:
        m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
        if m:
            return m.group(1).strip()
    return text


class PlanArgumentGenerator:
    """
    Given a fixed ExecutionTask.tool, asks the reasoning model for JSON args only.
    """

    def __init__(self, generate_fn=None):
        self._generate_fn: Callable[[str], str] | None = generate_fn

    def _call_llm(self, prompt: str, state: Any) -> str:
        if self._generate_fn is not None:
            return self._generate_fn(prompt)
        return call_reasoning_model(prompt, task_name=_model_task_for_tool_args(state))

    def _generate_with_langfuse(self, prompt: str, task: ExecutionTask, state: Any) -> str:
        md = getattr(state, "metadata", None) or {}
        obs = md.get("obs")
        span = None
        if obs is not None and getattr(obs, "current_span", None) is not None:
            span = obs.current_span
        if span is None:
            span = md.get("_current_langfuse_span")
        lf = md.get("langfuse_trace")
        if obs is not None and getattr(obs, "langfuse_trace", None) is not None:
            lf = obs.langfuse_trace
        gen = try_langfuse_generation(
            span,
            lf,
            name="argument_generation",
            input=langfuse_generation_input_with_prompt(
                prompt,
                extra={"step_goal": task.goal, "action": task.tool},
            ),
        )
        text = ""
        try:
            text = self._call_llm(prompt, state)
            return text
        finally:
            if gen is not None:
                try:
                    langfuse_generation_end_with_usage(
                        gen,
                        output={
                            "response": (text or "")[:LANGFUSE_GENERATION_PROMPT_INPUT_MAX_CHARS]
                        },
                    )
                except Exception:
                    pass

    def generate(self, task: ExecutionTask, state: Any) -> dict:
        if task.tool == "finish":
            return {}

        if task.tool == "shell":
            return self._shell_args(task, state)

        tool = get_tool_by_name(task.tool)
        if tool is None:
            return self._args_from_inputs_only(task)

        prompt = self._build_prompt(task, state, tool.required_args)
        raw_text = self._generate_with_langfuse(prompt, task, state)
        parsed = self._parse_args_json(raw_text)
        merged = self._merge_filtered(task, parsed)
        valid, err = validate_action(task.tool, merged)
        if not valid:
            fallback = self._args_from_inputs_only(task)
            v2, _ = validate_action(task.tool, fallback)
            return fallback if v2 else {}
        return merged

    def _shell_args(self, task: ExecutionTask, state: Any) -> dict:
        prompt = self._build_prompt(task, state, ["command"])
        raw_text = self._generate_with_langfuse(prompt, task, state)
        parsed = self._parse_args_json(raw_text)
        hints = task.input_hints if isinstance(task.input_hints, dict) else {}
        cmd = str(parsed.get("command") or hints.get("command") or "").strip()
        return {"command": cmd} if cmd else {}

    def _build_prompt(self, task: ExecutionTask, state: Any, required_keys: list[str]) -> str:
        hist_lines = []
        if getattr(state, "history", None):
            for h in state.history[-8:]:
                if not isinstance(h, dict):
                    continue
                a = h.get("action") or h.get("plan_action", "")
                obs = h.get("observation", "")
                hist_lines.append(f"- {a}: {str(obs)[:400]}")
        history_block = "\n".join(hist_lines) if hist_lines else "(none)"

        exploration_block = ""
        ctx = getattr(state, "context", None) or {}
        exp = ctx.get("exploration_summary_text")
        if exp:
            exploration_block = f"\nEXPLORATION SUMMARY:\n{exp}\n"
        elif ctx.get("exploration_result"):
            exploration_block = f"\nEXPLORATION (JSON fragment):\n{str(ctx.get('exploration_result'))[:2000]}\n"

        keys = ", ".join(f'"{k}"' for k in required_keys)
        hints = task.input_hints if isinstance(task.input_hints, dict) else {}
        return f"""You are filling tool arguments for a fixed plan step. Do NOT choose a different tool.

USER TASK:
{getattr(state, "instruction", "")}

STEP GOAL:
{task.goal}

FIXED ACTION (non-negotiable): {task.tool}
REQUIRED JSON keys: {keys}

PLAN STEP INPUTS (hints, may be incomplete):
{json.dumps(hints, indent=2)}

PRIOR OBSERVATIONS:
{history_block}
{exploration_block}
Return a single JSON object with ONLY the keys needed for action {task.tool!r} ({keys}).
No markdown, no prose, no extra keys beyond what the tool schema allows.
"""

    def _parse_args_json(self, text: str) -> dict:
        try:
            data = json.loads(_strip_json_fence(text))
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    def _filter_to_tool_schema(self, action: str, d: dict) -> dict:
        tool = get_tool_by_name(action)
        if not tool:
            return {}
        req = tool.required_args
        return {k: d[k] for k in req if k in d and d[k] is not None}

    def _merge_filtered(self, task: ExecutionTask, parsed: dict) -> dict:
        hints = task.input_hints if isinstance(task.input_hints, dict) else {}
        base = self._filter_to_tool_schema(task.tool, hints)
        overlay = self._filter_to_tool_schema(task.tool, parsed if isinstance(parsed, dict) else {})
        return {**base, **overlay}

    def _args_from_inputs_only(self, task: ExecutionTask) -> dict:
        hints = task.input_hints if isinstance(task.input_hints, dict) else {}
        return self._filter_to_tool_schema(task.tool, dict(hints))
