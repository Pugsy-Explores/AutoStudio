"""
ReAct execution loop for AutoStudio Mode 2 (autonomous).

The model selects the next action each iteration via _react_get_next_action().
Observations are appended to react_history. Execution respects limits from
agent_config (max steps, tool calls, runtime, iterations).
"""

import json
import logging
import re
import time
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

from agent.execution.react_schema import validate_action
from agent.prompt_system.registry import get_registry
from config.agent_config import (
    MAX_LOOP_ITERATIONS,
    MAX_STEP_TIMEOUT_SECONDS,
    MAX_STEPS,
    MAX_TASK_RUNTIME_SECONDS,
    MAX_TOOL_CALLS,
)
from agent.execution.executor import StepExecutor
from agent.execution.policy_engine import ResultClassification
from agent.models.model_client import GuardrailError, call_reasoning_model
from agent.memory.state import AgentState
from agent.memory.step_result import StepResult

logger = logging.getLogger(__name__)


@dataclass
class LoopResult:
    """Result of execution_loop."""

    state: AgentState
    loop_output: dict | None  # completed_steps, patches_applied, files_modified, errors_encountered, tool_calls, plan_result, start_time, react_history, edit_telemetry


def _output_summary(output) -> str:
    """One-line summary of step output for logging."""
    if isinstance(output, dict):
        keys = list(output.keys())[:5]
        return "output_keys=" + ",".join(str(k) for k in keys)
    s = str(output)
    return "output=" + (s[:80] + "..." if len(s) > 80 else s)


_REACT_TO_STEP = {
    "search": "SEARCH",
    "open_file": "READ",
    "edit": "EDIT",
    "run_tests": "RUN_TEST",
}


def _react_parse_response(text: str) -> tuple[str | None, str | None, dict | None]:
    """Parse strict JSON from model output. Returns (thought, action, args) or (None, None, None)."""
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
    """Format tool/validation error for observation (clear, actionable)."""
    return f"""Tool: {action}

Result: failed

Error:
{error}

Fix your input and try again."""


def _format_react_history(history: list) -> str:
    """Format react_history for prompt injection."""
    if not history:
        return "(none yet)"
    lines = []
    for entry in history:
        lines.append(f"Thought: {entry.get('thought', '')}")
        lines.append(f"Action: {entry.get('action', '')}")
        lines.append(f"Args: {json.dumps(entry.get('args', {}))}")
        obs = entry.get("observation", "")
        lines.append(f"Observation: {obs}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _react_get_next_action(instruction: str, state: AgentState, *, retried: bool = False) -> dict | None:
    """Call LLM to get next ReAct step. Returns step dict or None (finish)."""
    history = state.context.setdefault("react_history", [])
    react_history_str = _format_react_history(history)
    prompt = get_registry().get_instructions(
        "react_action",
        variables={"instruction": instruction, "react_history": react_history_str},
    )
    out = call_reasoning_model(prompt, task_name="REACT_ACTION")
    thought, action_raw, args = _react_parse_response(out)

    if thought is None and action_raw is None:
        if not retried:
            history.append({
                "thought": "(parse failed)",
                "action": "unknown",
                "args": {},
                "observation": _format_react_error("unknown", "Parse error: output must be valid JSON with thought, action, args. No markdown."),
            })
            return _react_get_next_action(instruction, state, retried=True)
        return None

    valid, err = validate_action(action_raw or "", args or {})
    if not valid:
        if not retried:
            history.append({
                "thought": thought or "",
                "action": action_raw or "",
                "args": args or {},
                "observation": _format_react_error(action_raw or "unknown", err or "Invalid action."),
            })
            return _react_get_next_action(instruction, state, retried=True)
        return None

    if action_raw == "finish":
        state.context["react_finish"] = True
        return None

    step_action = _REACT_TO_STEP[action_raw]
    step = {"id": len(history) + 1, "action": step_action, "artifact_mode": "code", "_react_thought": thought, "_react_action_raw": action_raw, "_react_args": args or {}}

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


def _build_react_observation(step: dict, result, action: str) -> str:
    """Build readable observation for react_history. No heavy nested JSON."""
    success = getattr(result, "success", result.get("success") if isinstance(result, dict) else True)
    err = getattr(result, "error", None) or (result.get("error", "") if isinstance(result, dict) else "")

    def _fail_obs(tool_name: str, msg: str) -> str:
        return _format_react_error(tool_name, msg)

    if action == "SEARCH":
        if not success and err:
            return _fail_obs(step.get("_react_action_raw", "search"), err)
        out = result.output if hasattr(result, "output") else result.get("output", {})
        if isinstance(out, dict):
            results = out.get("results") or out.get("candidates") or []
            lines = [f"Found {len(results)} result(s)."]
            for r in results[:12]:
                if isinstance(r, dict):
                    f = r.get("file", "")
                    s = (r.get("snippet") or r.get("content") or "")[:300].replace("\n", " ")
                    lines.append(f"  {f}: {s}...")
            return "\n".join(lines)
        return str(out)[:2000]
    if action == "READ":
        if not success and err:
            return _fail_obs(step.get("_react_action_raw", "open_file"), err)
        content = result.output if hasattr(result, "output") else result.get("output", "")
        return str(content)[:8000] if content else ""
    if action == "EDIT":
        out = result.output if hasattr(result, "output") else result.get("output", {})
        if isinstance(out, dict):
            files = out.get("files_modified") or out.get("target_files") or []
            files_str = ", ".join(str(f) for f in files[:5] if f) if files else "—"
            test_out = (
                (out.get("stdout") or "")
                + "\n"
                + (out.get("stderr") or "")
            ).strip() or (out.get("reason") or "") or (out.get("test_output") or "")
            fr = out.get("failure_reason_code", "")
            patch_applied = out.get("patch_applied", False)
            tests_passed = out.get("tests_passed", True)

            if success:
                return f"Patch applied successfully.\nModified file(s): {files_str}\nTests passed.\n{str(test_out)[:500]}".strip()
            if fr == "syntax_error":
                syn_err = out.get("syntax_error") or out.get("reason") or err
                return f"Syntax error:\n{syn_err}\n\nModified file(s) before rollback: {files_str}"
            if patch_applied and not tests_passed:
                return f"Patch applied successfully.\nModified file(s): {files_str}\n\nTests failed:\n\n{test_out[:2000]}"
            return f"Edit failed: {err or 'unknown'}.\nTarget file(s): {files_str}\n\nOutput: {str(test_out)[:1500]}"
        if not success and err:
            return _fail_obs(step.get("_react_action_raw", "edit"), err)
        return f"Edit {'succeeded' if success else 'failed'}: {str(out)[:500]}"
    if action == "RUN_TEST":
        out = result.output if hasattr(result, "output") else result.get("output", {})
        if isinstance(out, dict):
            raw = (out.get("stdout") or "") + "\n" + (out.get("stderr") or "")
        else:
            raw = str(out)[:5000]
        if success:
            return (f"All tests passed. If task is complete, call finish.\n\n{raw}".strip() if raw
                    else "All tests passed. If task is complete, call finish.")
        return f"Tests failed:\n\n{raw}\n\nUse this to fix the issue."
    return f"Result: success={success}, error={err}" + (f", output={str(result)[:500]}" if result else "")


def _repeated_action_guard(history: list, action: str, threshold: int = 3) -> str | None:
    """If same action repeated > threshold, return warning to inject into observation."""
    if len(history) < threshold:
        return None
    recent = [h.get("action") for h in history[-threshold:] if isinstance(h, dict)]
    if len(recent) >= threshold and all(a == action for a in recent):
        return "You are repeating the same action. Try a different approach."
    return None


def _should_stop_loop(
    state: AgentState,
    iteration: int,
    tool_call_count: int,
    start_time: float,
    execution_limits: dict,
) -> tuple[bool, str | None]:
    """Check if loop should stop due to limits. Returns (should_stop, limit_reason)."""
    if iteration > MAX_LOOP_ITERATIONS:
        return True, "max_loop_iterations"
    max_runtime = execution_limits.get("max_runtime_seconds", MAX_TASK_RUNTIME_SECONDS)
    if time.perf_counter() - start_time > max_runtime:
        return True, "max_task_runtime_exceeded"
    if len(state.completed_steps) >= MAX_STEPS:
        return True, "max_steps"
    if tool_call_count >= MAX_TOOL_CALLS:
        return True, "max_tool_calls"
    return False, None


def execution_loop(
    state: AgentState,
    instruction: str,
    *,
    trace_id=None,
    log_event_fn=None,
    max_runtime_seconds: int | None = None,
) -> LoopResult:
    """
    ReAct execution loop. Model selects next action via _react_get_next_action.

    Args:
        state: AgentState with instruction, plan, context.
        instruction: Task instruction for the model.
        trace_id: Optional trace ID for logging.
        log_event_fn: Optional (trace_id, event, payload) callback.
        max_runtime_seconds: Override max task runtime (default: MAX_TASK_RUNTIME_SECONDS).

    Returns:
        LoopResult with state and loop_output (completed_steps, patches_applied,
        files_modified, errors_encountered, tool_calls, plan_result, start_time,
        react_history, edit_telemetry).
    """
    log_fn = log_event_fn or (lambda *args, **kwargs: None)
    start_time = time.perf_counter()
    iteration = 0
    tool_call_count = 0
    errors_encountered: list = []
    executor = StepExecutor()

    execution_limits = state.context.setdefault("execution_limits", {})
    execution_limits.update({
        "max_steps": MAX_STEPS,
        "max_tool_calls": MAX_TOOL_CALLS,
        "max_runtime_seconds": max_runtime_seconds if max_runtime_seconds is not None else MAX_TASK_RUNTIME_SECONDS,
        "max_step_timeout_seconds": MAX_STEP_TIMEOUT_SECONDS,
    })
    if trace_id:
        log_fn(trace_id, "execution_limits", execution_limits)

    state.context.setdefault("react_history", [])
    state.context["react_finish"] = False
    state.context["react_mode"] = True

    while not state.context.get("react_finish", False):
        iteration += 1
        should_stop, limit_reason = _should_stop_loop(
            state, iteration, tool_call_count, start_time, execution_limits
        )
        if should_stop and limit_reason:
            errors_encountered.append(limit_reason)
            logger.warning("[execution_loop] %s, stopping", limit_reason)
            if trace_id:
                log_fn(trace_id, "limit_reached" if "runtime" not in limit_reason else "error", {"type": limit_reason})
            break

        step = _react_get_next_action(instruction, state)
        if step is None:
            break

        step_id = step.get("id", "?")
        action = (step.get("action") or "EXPLAIN").upper()
        description = (step.get("description") or "")
        logger.info("[execution_loop] step_id=%s action=%s %s", step_id, action, description)

        state.context["current_step_id"] = step.get("id")
        tool_call_count += 1

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(executor.execute_step, step, state)
            try:
                result = future.result(timeout=MAX_STEP_TIMEOUT_SECONDS)
            except GuardrailError as e:
                err_msg = str(e)
                logger.error("[execution_loop] guardrail failure: %s", err_msg)
                result = StepResult(
                    step_id=step.get("id", 0),
                    action=action,
                    success=False,
                    output="",
                    latency_seconds=0,
                    error="guardrail_failure",
                    classification=ResultClassification.FATAL_FAILURE.value,
                )
                errors_encountered.append(err_msg)
                if trace_id:
                    log_fn(trace_id, "guardrail_failure", {"step_id": step_id, "action": action, "error": err_msg})
                entry = {
                    "thought": step.get("_react_thought", ""),
                    "action": step.get("_react_action_raw", action.lower()),
                    "args": step.get("_react_args", {}),
                    "observation": _format_react_error(step.get("_react_action_raw", action.lower()), err_msg),
                }
                state.context["react_history"].append(entry)
                if trace_id:
                    log_fn(trace_id, "react_step", {"step_id": step_id, "json_action": {"thought": entry["thought"], "action": entry["action"], "args": entry["args"]}, "success": False})
                continue
            except FuturesTimeoutError:
                logger.warning(
                    "[execution_loop] step %s timed out after %ss", step_id, MAX_STEP_TIMEOUT_SECONDS
                )
                result = StepResult(
                    step_id=step.get("id", 0),
                    action=action,
                    success=False,
                    output="",
                    latency_seconds=MAX_STEP_TIMEOUT_SECONDS,
                    error=f"Step timed out after {MAX_STEP_TIMEOUT_SECONDS}s",
                    classification=ResultClassification.RETRYABLE_FAILURE.value,
                )
                if trace_id:
                    log_fn(trace_id, "step_timeout", {"step_id": step_id, "action": action})

        if trace_id:
            log_fn(
                trace_id,
                "step_executed",
                {
                    "step_id": step_id,
                    "action": action,
                    "success": result.success,
                    "error": getattr(result, "error", None),
                },
            )

        out_summary = _output_summary(result.output)
        logger.info(
            "Step %s completed in %.3fs success=%s %s",
            step_id,
            result.latency_seconds,
            result.success,
            out_summary,
        )

        obs = _build_react_observation(step, result, action)
        warn = _repeated_action_guard(
            state.context["react_history"],
            step.get("_react_action_raw", action.lower()),
        )
        if warn:
            obs = (obs or "") + "\n" + warn
        entry = {
            "thought": step.get("_react_thought", ""),
            "action": step.get("_react_action_raw", action.lower()),
            "args": step.get("_react_args", {}),
            "observation": obs,
        }
        state.context["react_history"].append(entry)
        if trace_id:
            log_fn(trace_id, "react_step", {"step_id": step_id, "json_action": {"thought": entry["thought"], "action": entry["action"], "args": entry["args"]}, "success": result.success})

        # ReAct: never block on failure. Record and continue; model decides next action.
        state.record(step, result)

    state.context["execution_counts"] = {
        "steps_completed": len(state.completed_steps),
        "tool_calls": tool_call_count,
    }
    if trace_id:
        log_fn(trace_id, "execution_counts", state.context["execution_counts"])

    completed_steps = list(state.completed_steps)
    patch_count = 0
    files_modified: list = []
    for sr in state.step_results:
        pm = getattr(sr, "patch_size", None)
        if isinstance(pm, int):
            patch_count += pm
        elif isinstance(pm, list):
            patch_count += len(pm)
        fm = getattr(sr, "files_modified", None) or []
        if isinstance(fm, list):
            files_modified.extend(fm)
    fm_distinct = [x for x in dict.fromkeys(files_modified) if isinstance(x, str)]

    loop_output = {
        "completed_steps": completed_steps,
        "patches_applied": patch_count,
        "files_modified": files_modified,
        "errors_encountered": errors_encountered,
        "tool_calls": tool_call_count,
        "plan_result": state.current_plan,
        "start_time": start_time,
        "react_history": state.context.get("react_history", []),
    }
    loop_output["edit_telemetry"] = {
        "attempted_target_files": state.context.get("search_target_candidates"),
        "chosen_target_file": state.context.get("edit_target_file"),
        "chosen_symbol": state.context.get("edit_target_symbol"),
        "edit_failure_reason": state.context.get("edit_failure_reason"),
        "patches_applied": patch_count,
        "changed_files_count": len(fm_distinct),
        **(state.context.get("edit_patch_telemetry") or {}),
    }
    if trace_id:
        log_fn(trace_id, "react_history_full", {"react_history": state.context.get("react_history", [])})

    return LoopResult(state=state, loop_output=loop_output)
