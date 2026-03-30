"""ContextVar for the active TraceEmitter (Phase 13 — LLM trace from model_client)."""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from agent_v2.runtime.trace_emitter import TraceEmitter

_active_trace_emitter: ContextVar[Optional["TraceEmitter"]] = ContextVar(
    "active_trace_emitter", default=None
)


def set_active_trace_emitter(emitter: Optional["TraceEmitter"]) -> None:
    _active_trace_emitter.set(emitter)


def get_active_trace_emitter() -> Optional["TraceEmitter"]:
    return _active_trace_emitter.get()


def clear_active_trace_emitter() -> None:
    _active_trace_emitter.set(None)
