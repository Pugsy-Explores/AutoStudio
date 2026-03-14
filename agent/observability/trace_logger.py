"""Record execution trace for debugging and analysis."""

import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

AGENT_MEMORY_DIR = ".agent_memory"
TRACES_SUBDIR = "traces"


def _traces_dir(project_root: str | None = None) -> Path:
    """Return path to .agent_memory/traces/."""
    root = Path(project_root or ".").resolve()
    return root / AGENT_MEMORY_DIR / TRACES_SUBDIR


_trace_state: dict[str, dict] = {}


def start_trace(task_id: str, project_root: str | None = None) -> str:
    """
    Start a new trace. Returns trace_id.
    """
    trace_id = f"{task_id}_{int(time.time())}"
    _trace_state[trace_id] = {
        "task_id": task_id,
        "started_at": time.time(),
        "events": [],
        "project_root": str(project_root or "."),
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
        "started_at": state.get("started_at"),
        "finished_at": time.time(),
        "events": state.get("events", []),
    }

    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, default=str)

    return str(file_path)
