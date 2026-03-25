"""
Phase 12.6.G — Single runtime carrier for Langfuse handles (no per-call langfuse_trace threading).

Stored at ``state.metadata["obs"]``. ``langfuse_trace`` on metadata remains populated for
backward compatibility during migration.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Tuple

from agent_v2.observability.langfuse_client import create_agent_trace


@dataclass
class ObservabilityContext:
    """Langfuse projection context for one agent run (serialized phases: exploration → plan → execute)."""

    langfuse_trace: Any = None
    """Root LFTraceHandle for this run (agent_run observation)."""

    current_span: Any = None
    """Active span for nesting (e.g. executor.step while a plan step runs). Owned by one phase at a time."""

    exploration_parent_span: Any = None
    """Phase 12.6.G: parent ``exploration`` span — use for exploration-scoped events."""


def get_obs(state: Any) -> Optional[ObservabilityContext]:
    md = getattr(state, "metadata", None)
    if not isinstance(md, dict):
        return None
    obs = md.get("obs")
    return obs if isinstance(obs, ObservabilityContext) else None


def ensure_obs_with_trace(state: Any, langfuse_trace: Any) -> ObservabilityContext:
    """
    Return existing ObsContext or create one bound to ``langfuse_trace``.
    Does not write back to ``state`` unless creating.
    """
    existing = get_obs(state)
    if existing is not None and existing.langfuse_trace is not None:
        return existing
    obs = ObservabilityContext(langfuse_trace=langfuse_trace)
    if isinstance(getattr(state, "metadata", None), dict):
        state.metadata["obs"] = obs
    return obs


def get_or_create_root_trace(
    state: Any,
    *,
    instruction: str,
    mode: str = "act",
) -> Tuple[Any, bool]:
    """
    One logical agent run = one Langfuse root trace.

    Returns ``(trace, owns_root)``. When ``obs.langfuse_trace`` is already set (e.g. AgentRuntime),
    returns that handle and ``owns_root=False`` — the caller must not ``end()`` the root; the
    bootstrap ``finalize_agent_trace`` owns lifecycle. When creating a new trace, sets
    ``metadata["obs"]`` and ``metadata["langfuse_trace"]`` and returns ``owns_root=True``.
    """
    obs = get_obs(state)
    if obs is not None and getattr(obs, "langfuse_trace", None) is not None:
        md = getattr(state, "metadata", None)
        if isinstance(md, dict) and md.get("langfuse_trace") is None:
            md["langfuse_trace"] = obs.langfuse_trace
        return obs.langfuse_trace, False

    trace = create_agent_trace(instruction=instruction, mode=mode)
    ensure_obs_with_trace(state, trace)
    md = getattr(state, "metadata", None)
    if isinstance(md, dict):
        md["langfuse_trace"] = trace
    return trace, True
