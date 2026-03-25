"""
Phase 11 — Langfuse client (singleton) + trace/span/generation facades.

Maps Langfuse 4.x `start_observation` API to the hierarchy from PHASE_11_LANGFUSE_OBSERVABILITY.md:
  trace (agent run) → spans (plan steps) → generations (LLM) → events (retry / replan).

Internal serializable graph remains agent_v2.schemas.trace.Trace — use metadata key ``langfuse_trace``
for the Langfuse handle (never ``trace`` alone).
"""
from __future__ import annotations

import os
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Optional Langfuse SDK
# ---------------------------------------------------------------------------
Langfuse: Any = None
TraceContext: Any = None
try:
    from langfuse import Langfuse as _Langfuse
    from langfuse.types import TraceContext as _TraceContext

    Langfuse = _Langfuse
    TraceContext = _TraceContext
except Exception:  # pragma: no cover - optional dependency
    pass

_CLIENT: Any = None
_HAS_KEYS = bool(os.getenv("LANGFUSE_PUBLIC_KEY")) and bool(os.getenv("LANGFUSE_SECRET_KEY"))


def _get_client() -> Any:
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT
    if Langfuse is None or not _HAS_KEYS:
        return None
    _CLIENT = Langfuse(
        public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
        secret_key=os.environ["LANGFUSE_SECRET_KEY"],
        host=os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com"),
    )
    return _CLIENT


# ---------------------------------------------------------------------------
# No-op facades (disabled SDK or missing keys)
# ---------------------------------------------------------------------------


class _NoopGen:
    def end(self, output: Any = None, **kwargs: Any) -> None:
        del output, kwargs


class _NoopSpan:
    def end(self, output: Any = None, metadata: Any = None, **kwargs: Any) -> None:
        del output, metadata, kwargs

    def update(self, metadata: Any = None, **kwargs: Any) -> None:
        del metadata, kwargs

    def generation(self, name: str, input: Any = None, **kwargs: Any) -> _NoopGen:
        del name, input, kwargs
        return _NoopGen()

    def span(self, name: str, input: Any = None, **kwargs: Any) -> "_NoopSpan":
        del name, input, kwargs
        return _NoopSpan()


class _NoopTrace:
    def span(self, name: str, input: Any = None, **kwargs: Any) -> _NoopSpan:
        del name, input, kwargs
        return _NoopSpan()

    def generation(self, name: str, input: Any = None, **kwargs: Any) -> _NoopGen:
        del name, input, kwargs
        return _NoopGen()

    def event(self, name: str, metadata: Any = None, **kwargs: Any) -> None:
        del name, metadata, kwargs

    def update(self, output: Any = None, **kwargs: Any) -> None:
        del output, kwargs

    def end(self, **kwargs: Any) -> None:
        del kwargs


# ---------------------------------------------------------------------------
# Real facades (Langfuse 4.x observations)
# ---------------------------------------------------------------------------


class LFGenerationHandle:
    def __init__(self, obs: Any) -> None:
        self._obs = obs

    def end(self, output: Any = None, **kwargs: Any) -> None:
        if output is not None or kwargs:
            self._obs.update(output=output, **kwargs)
        self._obs.end()


class LFSpanHandle:
    """One PlanStep execution span; may host nested generations (e.g. argument_generation)."""

    def __init__(self, obs: Any) -> None:
        self._obs = obs

    def end(self, output: Any = None, metadata: Any = None, **kwargs: Any) -> None:
        if output is not None or metadata is not None or kwargs:
            self._obs.update(output=output, metadata=metadata, **kwargs)
        self._obs.end()

    def update(self, metadata: Any = None, **kwargs: Any) -> None:
        self._obs.update(metadata=metadata, **kwargs)

    def generation(self, name: str, input: Any = None, **kwargs: Any) -> LFGenerationHandle:
        child = self._obs.start_observation(
            name=name, as_type="generation", input=input, **kwargs
        )
        return LFGenerationHandle(child)

    def span(self, name: str, input: Any = None, **kwargs: Any) -> "LFSpanHandle":
        child = self._obs.start_observation(name=name, as_type="span", input=input, **kwargs)
        return LFSpanHandle(child)


class LFTraceHandle:
    """
    One agent run: root observation under a trace_id (maps to Langfuse trace).
    """

    def __init__(self, client: Any, root_obs: Any, trace_id: str) -> None:
        self._client = client
        self._root = root_obs
        self.trace_id = trace_id

    def span(self, name: str, input: Any = None, **kwargs: Any) -> LFSpanHandle:
        o = self._root.start_observation(name=name, as_type="span", input=input, **kwargs)
        return LFSpanHandle(o)

    def generation(self, name: str, input: Any = None, **kwargs: Any) -> LFGenerationHandle:
        o = self._root.start_observation(
            name=name, as_type="generation", input=input, **kwargs
        )
        return LFGenerationHandle(o)

    def event(self, name: str, metadata: Any = None, **kwargs: Any) -> None:
        self._root.create_event(name=name, metadata=metadata or {}, **kwargs)

    def update(self, output: Any = None, **kwargs: Any) -> None:
        self._root.update(output=output, **kwargs)

    def end(self, **kwargs: Any) -> None:
        del kwargs
        self._root.end()

    def flush(self) -> None:
        fn = getattr(self._client, "flush", None)
        if callable(fn):
            fn()


def create_agent_trace(*, instruction: str, mode: str) -> LFTraceHandle | _NoopTrace:
    """Create root Langfuse observation for one agent run (Phase 11 Step 2)."""
    client = _get_client()
    if client is None or TraceContext is None:
        return _NoopTrace()

    trace_id = client.create_trace_id()
    tc = TraceContext(trace_id=trace_id)
    root = client.start_observation(
        name="agent_run",
        as_type="span",
        trace_context=tc,
        input={"instruction": instruction, "mode": mode},
        metadata={"runtime": "agent_v2"},
    )
    return LFTraceHandle(client, root, trace_id)


def finalize_agent_trace(
    lf: Any,
    *,
    status: str,
    plan_id: Optional[str] = None,
) -> None:
    """Phase 11 Step 9 — final trace output + end."""
    if lf is None or isinstance(lf, _NoopTrace):
        return
    try:
        lf.update(output={"status": status, "plan_id": plan_id})
    except Exception:
        pass
    try:
        lf.end()
    except Exception:
        pass
    try:
        lf.flush()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Module singleton compatible with legacy `langfuse.trace(...)` calls
# ---------------------------------------------------------------------------


class _LangfuseFacade:
    """Process-wide entry: ``langfuse.trace(name=..., input=...)``."""

    def trace(self, *, name: str, input: Any = None, **kwargs: Any) -> LFTraceHandle | _NoopTrace:
        del kwargs
        if name != "agent_run":
            # AgentLoop legacy: still create a full trace with custom name in input
            client = _get_client()
            if client is None or TraceContext is None:
                return _NoopTrace()
            trace_id = client.create_trace_id()
            tc = TraceContext(trace_id=trace_id)
            root = client.start_observation(
                name=name,
                as_type="span",
                trace_context=tc,
                input=input,
            )
            return LFTraceHandle(client, root, trace_id)
        return create_agent_trace(
            instruction=(input or {}).get("instruction", ""),
            mode=(input or {}).get("mode", "act"),
        )


langfuse = _LangfuseFacade()
