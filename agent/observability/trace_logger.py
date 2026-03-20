"""Record execution trace for debugging and analysis."""

import json
import logging
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path

from config.observability_config import MAX_TRACE_SIZE_BYTES, get_trace_dir

logger = logging.getLogger(__name__)

# Phase 6: optional live listeners for step visualization
_event_listeners: list[Callable[[str, str, dict | None], None]] = []
_stage_listeners: list[Callable[[str, str, float, int | None, dict | None], None]] = []


def add_event_listener(fn: Callable[[str, str, dict | None], None]) -> None:
    """Register callback for log_event. Args: trace_id, event_type, payload."""
    _event_listeners.append(fn)


def add_stage_listener(fn: Callable[[str, str, float, int | None, dict | None], None]) -> None:
    """Register callback for log_stage. Args: trace_id, stage_name, latency_ms, step_id, summary."""
    _stage_listeners.append(fn)


def remove_event_listener(fn: Callable[[str, str, dict | None], None]) -> None:
    """Unregister event listener."""
    if fn in _event_listeners:
        _event_listeners.remove(fn)


def remove_stage_listener(fn: Callable[[str, str, float, int | None, dict | None], None]) -> None:
    """Unregister stage listener."""
    if fn in _stage_listeners:
        _stage_listeners.remove(fn)

STAGES = [
    "context_grounder",
    "intent_router",
    "planner",
    "query_rewrite",
    "retrieval",
    "symbol_expansion",
    "context_ranker",
    "context_pruner",
    "reasoning",
    "validation",
]


def summarize(value, max_chars: int = 200, _depth: int = 0, _max_depth: int = 32) -> str | dict:
    """Reduce LLM responses, retrieval results, code snippets to compact summaries."""
    if _depth > _max_depth:
        return "<max_depth>"
    if value is None:
        return {}
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if isinstance(v, (str, bytes)):
                s = str(v)[:max_chars]
                if len(str(v)) > max_chars:
                    s += "..."
                out[k] = s
            elif isinstance(v, (list, tuple)):
                out[k] = list(v)[:10]  # limit list length
            elif isinstance(v, (int, float, bool)):
                out[k] = v
            else:
                out[k] = summarize(v, max_chars, _depth + 1, _max_depth)
        return out
    if isinstance(value, (list, tuple)):
        return [summarize(x, max_chars, _depth + 1, _max_depth) for x in list(value)[:10]]
    s = str(value)
    if len(s) > max_chars:
        return s[:max_chars] + "..."
    return s


def _traces_dir(project_root: str | None = None) -> Path:
    """Return path to .agent_memory/traces/."""
    return get_trace_dir(project_root)


_trace_state: dict[str, dict] = {}


def start_trace(task_id: str, project_root: str | None = None, query: str | None = None) -> str:
    """
    Start a new trace. Returns trace_id.
    """
    trace_id = f"{task_id}_{int(time.time())}"
    logger.info("[trace] started trace_id=%s task_id=%s", trace_id, task_id)
    _trace_state[trace_id] = {
        "task_id": task_id,
        "started_at": time.time(),
        "events": [],
        "stages": [],
        "project_root": str(project_root or "."),
        "query": query,
    }
    return trace_id


def log_event(trace_id: str, event_type: str, payload: dict | None = None) -> None:
    """Append an event to the trace."""
    if trace_id not in _trace_state:
        return
    _trace_state[trace_id]["events"].append({
        "type": event_type,
        "payload": payload or {},
        "timestamp": time.time(),
    })
    for fn in _event_listeners:
        try:
            fn(trace_id, event_type, payload)
        except Exception as e:
            logger.debug("[trace] listener error: %s", e)


def log_stage(
    trace_id: str,
    stage_name: str,
    latency_ms: float,
    step_id: int | None = None,
    summary: dict | None = None,
) -> None:
    """Append a stage entry to the trace."""
    if trace_id not in _trace_state:
        return
    if stage_name not in STAGES:
        logger.warning("[trace] unknown stage name: %s (expected one of %s)", stage_name, STAGES)
    entry = {
        "step_id": step_id,
        "stage": stage_name,
        "latency_ms": round(latency_ms, 2),
        "summary": summary or {},
    }
    _trace_state[trace_id]["stages"].append(entry)
    logger.info("[trace] stage=%s latency_ms=%.0f", stage_name, latency_ms)
    for fn in _stage_listeners:
        try:
            fn(trace_id, stage_name, latency_ms, step_id, summary)
        except Exception as e:
            logger.debug("[trace] stage listener error: %s", e)


@contextmanager
def trace_stage(trace_id: str, stage_name: str, step_id: int | None = None):
    """Context manager for stage-level tracing. Caller populates summary dict."""
    start = time.time()
    summary = {}
    try:
        yield summary
    finally:
        latency_ms = (time.time() - start) * 1000
        log_stage(trace_id, stage_name, latency_ms, step_id, summary)


def finish_trace(trace_id: str) -> str | None:
    """
    Finalize trace and write to disk.
    Returns path to written file, or None if trace not found.
    """
    if trace_id not in _trace_state:
        return None
    state = _trace_state.pop(trace_id)
    project_root = state.get("project_root", ".")
    traces_path = _traces_dir(project_root)
    traces_path.mkdir(parents=True, exist_ok=True)
    file_path = traces_path / f"{trace_id}.json"

    output = {
        "trace_id": trace_id,
        "task_id": state.get("task_id", ""),
        "query": state.get("query"),
        "started_at": state.get("started_at"),
        "finished_at": time.time(),
        "events": state.get("events", []),
        "stages": state.get("stages", []),
    }

    raw = json.dumps(output, indent=2, default=str)
    size_bytes = len(raw.encode("utf-8"))
    if size_bytes > MAX_TRACE_SIZE_BYTES:
        logger.warning(
            "[trace] trace size %.1f KB exceeds limit %.1f KB; truncating stages",
            size_bytes / 1024,
            MAX_TRACE_SIZE_BYTES / 1024,
        )
        stages = list(state.get("stages", []))
        while stages:
            output["stages"] = stages
            output["_truncated"] = True
            raw = json.dumps(output, indent=2, default=str)
            if len(raw.encode("utf-8")) <= MAX_TRACE_SIZE_BYTES:
                break
            stages = stages[:-1]
        if not stages:
            output["stages"] = []
            output["_truncated"] = True
            raw = json.dumps(output, indent=2, default=str)

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(raw)

    logger.info(
        "[trace] finished trace_id=%s path=%s events=%d stages=%d",
        trace_id,
        file_path,
        len(state.get("events", [])),
        len(state.get("stages", [])),
    )
    return str(file_path)
