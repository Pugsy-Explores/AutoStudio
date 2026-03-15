"""Live step visualization: print trace events as they arrive."""

import sys
from typing import Any

from agent.observability.trace_logger import add_event_listener, add_stage_listener, remove_event_listener, remove_stage_listener


def _format_event(trace_id: str, event_type: str, payload: dict | None) -> str | None:
    """Format event for display. Returns None to skip."""
    p = payload or {}
    if event_type == "planner_decision":
        plan = p.get("plan", {})
        steps = plan.get("steps", [])
        if steps:
            first = steps[0] if steps else {}
            action = first.get("action", "?")
            desc = (first.get("description") or "")[:50]
            return f"[Planner]   -> {action} {desc}{'...' if len(str(first.get('description', ''))) > 50 else ''}"
    if event_type == "step_executed":
        action = p.get("action", "?")
        tool = p.get("tool", "")
        success = "OK" if p.get("success") else "FAIL"
        return f"[Dispatcher] -> {action} tool={tool} {success}"
    if event_type == "patch_result":
        files = p.get("files_modified", [])
        count = p.get("patches_applied", 0)
        return f"[Patch]      -> {count} patches, files={files[:3]}{'...' if len(files) > 3 else ''}"
    if event_type == "error":
        err = p.get("error", p.get("type", "?"))
        return f"[Error]      -> {str(err)[:80]}"
    return None


def _format_stage(trace_id: str, stage_name: str, latency_ms: float, step_id: int | None, summary: dict | None) -> str:
    """Format stage for display."""
    stage_label = stage_name.replace("_", " ").title()
    results = ""
    if summary:
        r = summary.get("results")
        if r is not None:
            results = f" ({r} results)"
    return f"[{stage_label}]  {latency_ms:.0f}ms{results}"


def install_live_listeners() -> tuple[list[Any], list[Any]]:
    """
    Install listeners that print events to stdout.
    Returns (event_listeners, stage_listeners) for later removal.
    """
    event_fns = []
    stage_fns = []

    def on_event(tid: str, etype: str, payload: dict | None) -> None:
        line = _format_event(tid, etype, payload)
        if line:
            print(f"  {line}", flush=True)

    def on_stage(tid: str, name: str, lat: float, sid: int | None, summary: dict | None) -> None:
        line = _format_stage(tid, name, lat, sid, summary)
        print(f"  {line}", flush=True)

    add_event_listener(on_event)
    add_stage_listener(on_stage)
    event_fns.append(on_event)
    stage_fns.append(on_stage)
    return event_fns, stage_fns


def uninstall_live_listeners(event_fns: list, stage_fns: list) -> None:
    """Remove previously installed listeners."""
    for fn in event_fns:
        remove_event_listener(fn)
    for fn in stage_fns:
        remove_stage_listener(fn)
