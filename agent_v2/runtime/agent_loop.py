"""Composable ReAct loop runtime."""
# DO NOT import from agent.* here

from agent_v2.schemas.execution import ExecutionResult
from agent_v2.runtime.langfuse_client import langfuse
from agent_v2.runtime.react_context import (
    MAX_OBS_CHARS,
    normalize_path_for_dedup,
    truncate_observation,
)


MAX_RETRIES = 2
MAX_FAILURE_STREAK = 3


def is_failure(result) -> bool:
    """Normalize result objects/dicts to a single failure predicate."""
    if isinstance(result, ExecutionResult):
        return not result.success
    if isinstance(result, dict):
        return not result.get("success", False)
    return not getattr(result, "success", False)


def _compact_value(value, max_chars: int = 500):
    """Keep span payloads compact for UI usability."""
    if value is None:
        return None
    if isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if isinstance(v, (dict, list, tuple)):
                out[k] = str(v)[:max_chars]
            else:
                s = str(v)
                out[k] = s[:max_chars] + ("..." if len(s) > max_chars else "")
        return out
    s = str(value)
    return s[:max_chars] + ("..." if len(s) > max_chars else "")


class AgentLoop:
    def __init__(
        self,
        dispatcher,
        validator,
        action_generator,
        observation_builder,
        *,
        should_stop=None,
        max_repeat_warning_threshold=3,
    ):
        self.dispatcher = dispatcher
        self.validator = validator
        self.action_generator = action_generator
        self.observation_builder = observation_builder
        self._should_stop = should_stop
        self._max_repeat_warning_threshold = max_repeat_warning_threshold

    def run(self, state):
        failure_streak = int(state.metadata.get("failure_streak", 0) or 0)
        state.metadata.setdefault("retry_count", 0)
        trace = langfuse.trace(
            name="agent_run",
            input={"instruction": state.instruction},
        )
        try:
            while True:
                step = self.action_generator.next_action(state)
                if step is None:
                    break
                if not isinstance(step, dict):
                    failure_streak += 1
                    state.last_error = f"Invalid step type: {type(step).__name__}"
                    state.metadata["failure_streak"] = failure_streak
                    break
                if self.should_stop(step, state):
                    break

                step_args = step.get("args")
                if not isinstance(step_args, dict):
                    step_args = step.get("_react_args", {}) if isinstance(step.get("_react_args", {}), dict) else {}
                span = trace.span(
                    name=(step.get("action") or "unknown").lower(),
                    input={
                        "action": step.get("action"),
                        "args": _compact_value(step_args),
                    },
                )
                self.validator.validate(step)
                retry_count = 0
                while True:
                    result = self.dispatcher.execute(step, state)
                    failed = is_failure(result)
                    if failed:
                        failure_streak += 1
                        if isinstance(result, ExecutionResult):
                            err = result.error
                            state.last_error = err.message if err is not None else None
                        else:
                            state.last_error = (
                                result.get("error")
                                if isinstance(result, dict)
                                else getattr(result, "error", None)
                            )
                        if state.last_error:
                            observation = f"ERROR: {state.last_error}"
                        else:
                            observation = self.observation_builder.build(step.get("action"), result)
                        self._update_state(state, step, observation, result)

                        # Retry the same step up to MAX_RETRIES.
                        if retry_count < MAX_RETRIES:
                            retry_count += 1
                            state.retry_count = retry_count
                            state.metadata["retry_count"] = retry_count
                            continue
                    else:
                        failure_streak = 0
                        state.retry_count = 0
                        state.metadata["retry_count"] = 0
                        state.last_error = None
                        observation = self.observation_builder.build(step.get("action"), result)
                        self._update_state(state, step, observation, result)

                    state.metadata["failure_streak"] = failure_streak
                    break
                span.end(
                    output={
                        "success": (
                            result.success
                            if isinstance(result, ExecutionResult)
                            else (
                                result.get("success")
                                if isinstance(result, dict)
                                else getattr(result, "success", None)
                            )
                        ),
                        "error": _compact_value(
                            (
                                (result.error.message if result.error else None)
                                if isinstance(result, ExecutionResult)
                                else (
                                    result.get("error")
                                    if isinstance(result, dict)
                                    else getattr(result, "error", None)
                                )
                            )
                        ),
                        "output": _compact_value(
                            (
                                (result.output.summary if result.output else None)
                                if isinstance(result, ExecutionResult)
                                else (
                                    result.get("output")
                                    if isinstance(result, dict)
                                    else getattr(result, "output", None)
                                )
                            )
                        ),
                        "target": _compact_value(
                            (step_args or {}).get("path")
                            or (step_args or {}).get("query")
                            or (step_args or {}).get("command")
                        ),
                    }
                )

                if self.should_stop(step, state):
                    break
        finally:
            trace.end(output={"steps": len(state.step_results or [])})

        return state

    def should_stop(self, step, state):
        if state.retry_count > MAX_RETRIES:
            return True
        if state.metadata.get("failure_streak", 0) >= MAX_FAILURE_STREAK:
            return True
        if (step.get("action") or "").lower() == "finish":
            return True
        if self._should_stop:
            return self._should_stop(step, state)
        return False

    def _repeated_action_warning(self, history, action):
        threshold = self._max_repeat_warning_threshold
        if len(history) < threshold:
            return None
        recent = [h.get("action") for h in history[-threshold:] if isinstance(h, dict)]
        if len(recent) >= threshold and all(a == action for a in recent):
            return "You are repeating the same action. Try a different approach."
        return None

    def _update_state(self, state, step, observation, result):
        action_raw = step.get("_react_action_raw", (step.get("action") or "").lower())
        warn = self._repeated_action_warning(state.history, action_raw)
        obs = (observation or "") + ("\n" + warn if warn else "")

        args = step.get("_react_args") or {}
        if not isinstance(args, dict):
            args = {}
        if action_raw == "open_file":
            norm = normalize_path_for_dedup(args.get("path", ""))
            opened = state.metadata.setdefault("react_opened_paths", [])
            if norm and not is_failure(result):
                if norm in opened:
                    obs = "Already read above"
                else:
                    opened.append(norm)
        obs = truncate_observation(obs, MAX_OBS_CHARS)

        state.history.append({
            "thought": step.get("_react_thought", ""),
            "action": action_raw,
            "args": step.get("_react_args", {}),
            "observation": obs,
        })
        if isinstance(result, ExecutionResult):
            out_summary = result.output.summary if result.output else ""
            err_out = result.error.message if result.error else None
            succ = result.success
        else:
            out_summary = result.get("output", "") if isinstance(result, dict) else getattr(result, "output", "")
            err_out = result.get("error") if isinstance(result, dict) else getattr(result, "error", None)
            succ = (
                result.get("success", False) if isinstance(result, dict) else getattr(result, "success", False)
            )
        state.step_results.append({
            "step_id": step.get("id"),
            "action": step.get("action"),
            "success": succ,
            "output": out_summary,
            "error": err_out,
        })
        state.debug_last_action = action_raw or None
