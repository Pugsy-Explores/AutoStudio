"""
Test-only fault injection at the Dispatcher boundary (Phase 6/7 live and integration tests).

Enabled only when env vars are set (never in production unless explicitly configured).
All state is keyed off ``state.context`` / ``state.metadata`` on the current ``AgentState``.
Hooks apply only when ``state`` is an :class:`~agent_v2.state.agent_state.AgentState`
(ExplorationRunner uses a private scratch dataclass so it never steals the one-shot fault).

Env:
  AGENT_V2_FAULT_OPEN_FILE_ONCE=1
      First ``open_file`` dispatch returns a deterministic failure without calling the
      real tool; subsequent dispatches are normal. Exercises per-step retry (Phase 6).

  AGENT_V2_FAULT_OPEN_FILE_HARD_UNTIL_REPLAN=1
      While ``state.metadata[\"replan_attempt\"]`` is 0, every dispatch for the *first*
      ``open_file`` step_id seen in this run fails. After replan bumps ``replan_attempt``,
      injection stops so the new plan can succeed (Phase 7).

If both are set, HARD wins (replan path). Use at most one for predictable tests.
"""
from __future__ import annotations

import os
from typing import Any

from agent_v2.state.agent_state import AgentState


_ENV_ONCE = "AGENT_V2_FAULT_OPEN_FILE_ONCE"
_ENV_HARD = "AGENT_V2_FAULT_OPEN_FILE_HARD_UNTIL_REPLAN"

_CTX_ONCE_DONE = "_agent_v2_fault_open_file_once_done"
_CTX_HARD_SID = "_agent_v2_fault_hard_open_sid"
_META_INJECT_COUNT = "agent_v2_fault_inject_count"


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")


def _bump_inject_metric(state: Any) -> None:
    md = getattr(state, "metadata", None)
    if isinstance(md, dict):
        md[_META_INJECT_COUNT] = int(md.get(_META_INJECT_COUNT, 0)) + 1


def _repl_attempt(state: Any) -> int:
    md = getattr(state, "metadata", None)
    if not isinstance(md, dict):
        return 0
    return int(md.get("replan_attempt", 0))


def _context_dict(state: Any) -> dict | None:
    ctx = getattr(state, "context", None)
    return ctx if isinstance(ctx, dict) else None


def synthetic_open_file_failure() -> dict[str, Any]:
    """Legacy-shaped dict for ``coerce_to_tool_result(..., tool_name='open_file')``."""
    return {
        "success": False,
        "output": {},
        "error": "agent_v2 fault_hooks: injected open_file failure",
    }


def maybe_inject_open_file_fault_raw(
    tool_name: str,
    step: dict,
    state: Any,
) -> dict[str, Any] | None:
    """
    If a fault rule applies for this ``open_file`` dispatch, return a raw dict to use
    **instead** of calling ``_execute_fn``. Otherwise return ``None``.

    Scoped to :class:`AgentState` only so ExplorationRunner's scratch state does not
    consume ``AGENT_V2_FAULT_OPEN_FILE_ONCE`` before PlanExecutor runs.
    """
    if tool_name != "open_file":
        return None
    if not isinstance(state, AgentState):
        return None
    ctx = _context_dict(state)
    if ctx is None:
        return None

    step_id = str(step.get("step_id") or step.get("id") or "")
    sid_key = step_id or "__open_file_fault_target__"

    if _env_truthy(_ENV_HARD) and _repl_attempt(state) < 1:
        if ctx.get(_CTX_HARD_SID) is None:
            ctx[_CTX_HARD_SID] = sid_key
        bound = ctx.get(_CTX_HARD_SID)
        if sid_key == bound:
            _bump_inject_metric(state)
            return synthetic_open_file_failure()
        return None

    if _env_truthy(_ENV_ONCE) and not _env_truthy(_ENV_HARD):
        if ctx.get(_CTX_ONCE_DONE):
            return None
        ctx[_CTX_ONCE_DONE] = True
        _bump_inject_metric(state)
        return synthetic_open_file_failure()

    return None
