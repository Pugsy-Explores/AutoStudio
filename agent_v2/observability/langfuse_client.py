"""
Phase 11 — Langfuse client (singleton) + trace/span/generation facades.

Maps Langfuse 4.x `start_observation` API to the hierarchy from PHASE_11_LANGFUSE_OBSERVABILITY.md:
  trace (agent run) → spans (plan steps) → generations (LLM) → events (retry / replan).

Internal serializable graph remains agent_v2.schemas.trace.Trace — use metadata key ``langfuse_trace``
for the Langfuse handle (never ``trace`` alone).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Load repo .env before reading LANGFUSE_* (keys are not in the shell by default)
# ---------------------------------------------------------------------------
def _load_dotenv_if_present() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    # agent_v2/observability/langfuse_client.py -> repo root is parents[2]
    root = Path(__file__).resolve().parents[2]
    load_dotenv(root / ".env")


_load_dotenv_if_present()

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


def _langfuse_host() -> str:
    """
    Langfuse Python SDK uses `host` (LANGFUSE_HOST).
    Many deployments set LANGFUSE_BASE_URL in .env — support both.
    """
    h = (os.environ.get("LANGFUSE_HOST") or "").strip()
    if h:
        return h.rstrip("/")
    base = (os.environ.get("LANGFUSE_BASE_URL") or "").strip()
    if base:
        return base.rstrip("/")
    return "https://cloud.langfuse.com"


def _has_langfuse_keys() -> bool:
    return bool(os.getenv("LANGFUSE_PUBLIC_KEY")) and bool(os.getenv("LANGFUSE_SECRET_KEY"))


def _get_client() -> Any:
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT
    if Langfuse is None or not _has_langfuse_keys():
        return None
    _CLIENT = Langfuse(
        public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
        secret_key=os.environ["LANGFUSE_SECRET_KEY"],
        host=_langfuse_host(),
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

    def event(self, name: str, metadata: Any = None, **kwargs: Any) -> None:
        del name, metadata, kwargs


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

    def event(self, name: str, metadata: Any = None, **kwargs: Any) -> None:
        """Phase 12.6.G — observation-scoped event (prefer over root trace for causality)."""
        fn = getattr(self._obs, "create_event", None)
        if callable(fn):
            try:
                fn(name=name, metadata=metadata or {}, **kwargs)
            except Exception:
                pass


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


_LANGFUSE_ROOT_NAME_MAX = 200
# Pytest sets ``AGENT_V2_LANGFUSE_ROOT_NAME`` per test (see tests/conftest.py):
# ``live::<nodeid>`` for ``@pytest.mark.agent_v2_live``, else ``<nodeid> - offline``.


def _sanitize_langfuse_root_name(label: str) -> str:
    """Observation names must be printable, bounded length (Langfuse UI / API limits)."""
    s = (label or "").strip()
    if not s:
        return "agent_run"
    if " (" in s:
        s = s.split(" (", 1)[0].strip()
    out = "".join(ch if ch.isprintable() and ch not in "\r\n\t" else "_" for ch in s)
    if len(out) > _LANGFUSE_ROOT_NAME_MAX:
        out = out[: _LANGFUSE_ROOT_NAME_MAX - 3] + "..."
    return out or "agent_run"


def _resolve_root_trace_metadata(explicit_name: Optional[str]) -> tuple[str, dict[str, Any]]:
    """
    Root span ``name`` for Langfuse + optional metadata (e.g. full pytest node id).

    Priority: explicit ``name`` → ``AGENT_V2_LANGFUSE_ROOT_NAME`` → ``agent_run``.
    """
    extra: dict[str, Any] = {}
    if explicit_name is not None and str(explicit_name).strip():
        return _sanitize_langfuse_root_name(str(explicit_name)), extra
    env_nodeid = os.environ.get("AGENT_V2_LANGFUSE_ROOT_NAME", "").strip()
    if env_nodeid:
        raw_nid = os.environ.get("AGENT_V2_PYTEST_NODEID", "").strip()
        if raw_nid:
            extra["pytest_nodeid"] = raw_nid[:500]
        extra["langfuse_test_label"] = env_nodeid[:500]
        return _sanitize_langfuse_root_name(env_nodeid), extra
    return "agent_run", extra


def create_agent_trace(
    *,
    instruction: str,
    mode: str,
    name: Optional[str] = None,
) -> LFTraceHandle | _NoopTrace:
    """Create root Langfuse observation for one agent run (Phase 11 Step 2).

    When ``name`` is omitted, the root span name is ``agent_run`` unless
    ``AGENT_V2_LANGFUSE_ROOT_NAME`` is set (pytest: ``live::…`` or ``… - offline``; see tests/conftest.py).
    """
    client = _get_client()
    if client is None or TraceContext is None:
        return _NoopTrace()

    root_name, meta_extra = _resolve_root_trace_metadata(name)
    trace_id = client.create_trace_id()
    tc = TraceContext(trace_id=trace_id)
    md = {"runtime": "agent_v2", **meta_extra}
    root = client.start_observation(
        name=root_name,
        as_type="span",
        trace_context=tc,
        input={"instruction": instruction, "mode": mode},
        metadata=md,
    )
    return LFTraceHandle(client, root, trace_id)


def finalize_agent_trace(
    lf: Any,
    *,
    status: str,
    plan_id: Optional[str] = None,
) -> None:
    """Phase 11 Step 9 — final trace output + end.

    ``plan_id`` is the planner's id when a plan exists; explore-only entrypoints
    pass a correlation id (e.g. ``explore_<langfuse_trace_id>``) so output is not null.
    """
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
            name=(input or {}).get("trace_name"),
        )


langfuse = _LangfuseFacade()
