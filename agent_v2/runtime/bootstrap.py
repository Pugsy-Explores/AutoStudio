"""Runtime bootstrap and legacy wiring seam.

This is the only module in agent_v2/runtime allowed to import legacy agent.*.
"""

from __future__ import annotations

import json
import re
from typing import Any

from agent.execution.react_schema import validate_action
from agent.execution.step_dispatcher import _dispatch_react
from agent.models.model_client import call_reasoning_model
from agent.prompt_system.registry import get_registry
from agent_v2.config import get_execution_policy
from agent_v2.observability.langfuse_helpers import (
    LANGFUSE_GENERATION_PROMPT_INPUT_MAX_CHARS,
    langfuse_generation_end_with_usage,
    langfuse_generation_input_with_prompt,
    try_langfuse_generation,
)
from agent_v2.planner.planner_v2 import PlannerV2
from agent_v2.runtime.plan_argument_generator import PlanArgumentGenerator
from agent_v2.schemas.exploration import ExplorationResult
from agent_v2.schemas.policies import ExecutionPolicy
from agent_v2.schemas.replan import PlannerInput

from agent_v2.runtime.replanner import Replanner
from agent_v2.runtime.runtime import AgentRuntime
from agent_v2.runtime.exploration_runner import ExplorationRunner
from agent_v2.runtime.action_generator import ActionGenerator
from agent_v2.runtime.dispatcher import Dispatcher
from agent_v2.runtime.react_context import (
    build_react_task_section,
    classify_react_task_mode,
    format_react_history_for_prompt,
    json_action_list_for_mode,
)

_REACT_TO_STEP = {
    "search": "SEARCH",
    "open_file": "READ",
    "edit": "EDIT",
    "run_tests": "RUN_TEST",
}


_DEFAULT_V2_POLICY = get_execution_policy()


def _planner_v2_generate(prompt: str) -> str:
    return call_reasoning_model(prompt, task_name="PLANNER_V2")


class V2PlannerAdapter:
    """
    Runs bounded exploration, then Planner v2 (Phase 4).
    exploration_runner MUST be the same instance ModeManager uses (shared wiring).
    """

    def __init__(self, generate_fn, policy: ExecutionPolicy | None = None):
        self._inner = PlannerV2(generate_fn=generate_fn, policy=policy or _DEFAULT_V2_POLICY)

    def plan(
        self,
        instruction: str,
        deep: bool = False,
        exploration_runner=None,
        exploration: ExplorationResult | None = None,
        planner_input: PlannerInput | None = None,
        langfuse_trace: Any = None,
        obs: Any = None,
        **kwargs,
    ):
        if planner_input is not None:
            return self._inner.plan(
                instruction,
                planner_input,
                deep=deep,
                langfuse_trace=langfuse_trace,
                obs=obs,
            )
        if exploration is not None:
            ex = exploration
        elif exploration_runner is not None:
            ex = exploration_runner.run(instruction)
        else:
            raise ValueError(
                "Planner v2 requires exploration_runner, a precomputed ExplorationResult, "
                "or planner_input (e.g. ReplanContext)."
            )
        return self._inner.plan(
            instruction, ex, deep=deep, langfuse_trace=langfuse_trace, obs=obs
        )


def _validate_step(step: dict):
    action_raw = (step.get("_react_action_raw") or "").strip().lower()
    args = step.get("_react_args")
    if not isinstance(args, dict):
        args = {}
    valid, err = validate_action(action_raw, args)
    if not valid:
        raise ValueError(err or "Invalid action")
    return valid


def _next_action(loop_state):
    return _react_get_next_action(loop_state.instruction, loop_state)


def _react_parse_response(text: str) -> tuple[str | None, str | None, dict | None]:
    text = (text or "").strip()
    if "```" in text:
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if m:
            text = m.group(1).strip()
    try:
        data = json.loads(text)
        if not isinstance(data, dict):
            return None, None, None
        thought = data.get("thought", "") or ""
        action = (data.get("action") or "").strip().lower()
        args = data.get("args")
        if not isinstance(args, dict):
            args = {}
        if not action:
            return None, None, None
        return thought, action, args
    except (json.JSONDecodeError, TypeError):
        return None, None, None


def _format_react_error(action: str, error: str) -> str:
    return f"""Tool: {action}

Result: failed

Error:
{error}

Fix your input and try again."""


def _react_get_next_action(
    instruction: str,
    state,
    *,
    retried: bool = False,
    langfuse_trace: Any = None,
) -> dict | None:
    history = state.history
    mode = classify_react_task_mode(instruction)
    react_history_str = format_react_history_for_prompt(history)
    prompt = get_registry().get_instructions(
        "react_action",
        variables={
            "instruction": instruction,
            "react_history": react_history_str,
            "react_task_section": build_react_task_section(mode),
            "react_json_action_list": json_action_list_for_mode(mode),
        },
    )
    gen = try_langfuse_generation(
        langfuse_trace,
        name="exploration_step",
        input=langfuse_generation_input_with_prompt(
            prompt,
            extra={"task": "REACT_ACTION"},
        ),
    )
    out = ""
    try:
        out = call_reasoning_model(prompt, task_name="REACT_ACTION")
    finally:
        if gen is not None:
            try:
                langfuse_generation_end_with_usage(
                    gen,
                    output={
                        "response": (out or "")[:LANGFUSE_GENERATION_PROMPT_INPUT_MAX_CHARS]
                    },
                )
            except Exception:
                pass
    thought, action_raw, args = _react_parse_response(out)

    if thought is None and action_raw is None:
        if not retried:
            history.append(
                {
                    "thought": "(parse failed)",
                    "action": "unknown",
                    "args": {},
                    "observation": _format_react_error(
                        "unknown",
                        "Parse error: output must be valid JSON with thought, action, args. No markdown.",
                    ),
                }
            )
            return _react_get_next_action(
                instruction, state, retried=True, langfuse_trace=langfuse_trace
            )
        return None

    valid, err = validate_action(action_raw or "", args or {})
    if not valid:
        if not retried:
            history.append(
                {
                    "thought": thought or "",
                    "action": action_raw or "",
                    "args": args or {},
                    "observation": _format_react_error(action_raw or "unknown", err or "Invalid action."),
                }
            )
            return _react_get_next_action(
                instruction, state, retried=True, langfuse_trace=langfuse_trace
            )
        return None

    if mode == "read_only" and action_raw in ("edit", "run_tests"):
        if not retried:
            history.append(
                {
                    "thought": thought or "",
                    "action": action_raw or "",
                    "args": args or {},
                    "observation": _format_react_error(
                        action_raw or "unknown",
                        "This task is read-only: do not call edit or run_tests. "
                        "Use search, open_file, then finish.",
                    ),
                }
            )
            return _react_get_next_action(
                instruction, state, retried=True, langfuse_trace=langfuse_trace
            )
        return None

    if action_raw == "finish":
        return {
            "id": len(history) + 1,
            "action": "finish",
            "artifact_mode": "code",
            "_react_thought": thought,
            "_react_action_raw": action_raw,
            "_react_args": args or {},
        }

    step_action = _REACT_TO_STEP[action_raw]
    step = {
        "id": len(history) + 1,
        "action": step_action,
        "artifact_mode": "code",
        "_react_thought": thought,
        "_react_action_raw": action_raw,
        "_react_args": args or {},
    }

    if action_raw == "search":
        step["query"] = args["query"]
        step["description"] = step["query"]
    elif action_raw == "open_file":
        step["path"] = args["path"]
        step["description"] = step["path"]
    elif action_raw == "edit":
        step["description"] = args["instruction"]
        step["path"] = args.get("path", "")
        step["edit_target_path"] = args.get("path", "")
    elif action_raw == "run_tests":
        step["description"] = ""
    return step


def _format_exploration_history(items: list) -> str:
    """Format (step, result) exploration items as a readable history string."""
    if not items:
        return "(no exploration steps yet)"
    lines = []
    for step, result in items:
        action = step.get("_react_action_raw") or step.get("action", "")
        args = step.get("_react_args") or {}
        observation = (
            result.output.summary
            if hasattr(result, "output") and hasattr(result.output, "summary")
            else str(result)
        )
        lines.append(f"Action: {action}")
        lines.append(f"Args: {json.dumps(args)}")
        lines.append(f"Observation: {observation}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _exploration_action_fn(
    instruction: str,
    items: list,
    *,
    langfuse_trace: Any = None,
) -> dict | None:
    """
    Generate the next step for the exploration phase.

    Reuses the existing ReAct infrastructure but with a minimal isolated state
    that carries only the exploration history (no main-loop history pollution).
    The ExplorationRunner enforces the allowed-action filter after this returns.
    """
    from dataclasses import dataclass, field

    @dataclass
    class _ExplorationCallState:
        instruction: str
        history: list = field(default_factory=list)

    state = _ExplorationCallState(instruction=instruction)

    # Convert collected (step, result) pairs to a history-compatible format
    for step, result in items:
        observation = (
            result.output.summary
            if hasattr(result, "output") and hasattr(result.output, "summary")
            else str(result)
        )
        state.history.append({
            "thought": step.get("_react_thought", ""),
            "action": step.get("_react_action_raw", step.get("action", "")),
            "args": step.get("_react_args", {}),
            "observation": observation,
        })

    return _react_get_next_action(instruction, state, langfuse_trace=langfuse_trace)


def create_runtime():
    planner = V2PlannerAdapter(_planner_v2_generate, policy=_DEFAULT_V2_POLICY)
    arg_gen = PlanArgumentGenerator()
    replanner = Replanner(planner, policy=_DEFAULT_V2_POLICY)
    return AgentRuntime(
        planner=planner,
        action_fn=_next_action,
        validate_fn=_validate_step,
        dispatch_fn=_dispatch_react,
        exploration_fn=_exploration_action_fn,
        plan_argument_generator=arg_gen,
        replanner=replanner,
        execution_policy=_DEFAULT_V2_POLICY,
        exploration_llm_fn=lambda prompt: call_reasoning_model(
            prompt, task_name="EXPLORATION_V2"
        ),
    )


def create_exploration_runner(dispatch_fn=None) -> ExplorationRunner:
    """
    Convenience factory for standalone ExplorationRunner use.

    Args:
        dispatch_fn: tool dispatch function injected into the Dispatcher.
                     Defaults to the legacy ReAct dispatch (_dispatch_react).
    """
    fn = dispatch_fn or _dispatch_react
    action_gen = ActionGenerator(fn=_next_action, exploration_fn=_exploration_action_fn)
    dispatcher = Dispatcher(execute_fn=fn)
    return ExplorationRunner(
        action_generator=action_gen,
        dispatcher=dispatcher,
        llm_generate_fn=lambda prompt: call_reasoning_model(
            prompt, task_name="EXPLORATION_V2"
        ),
    )
