"""Human-readable trace output for terminal (Phase 9) and legacy row format."""

from __future__ import annotations

from typing import Any, Union

from agent_v2.schemas.trace import Trace


def print_trace(trace_or_rows: Union[Trace, list[dict[str, Any]], None]) -> None:
    """
    Print execution trace. Accepts a Phase 9 ``Trace`` model or legacy ``build_trace`` rows.
    """
    if trace_or_rows is None:
        return
    if isinstance(trace_or_rows, Trace):
        _print_structured_trace(trace_or_rows)
        return
    _print_legacy_rows(trace_or_rows)


def _print_structured_trace(trace: Trace) -> None:
    print("\n=== EXECUTION TRACE ===\n")

    for step in trace.steps:
        status = "OK" if step.success else "FAIL"
        tgt = step.target or "-"
        idx = step.plan_step_index
        print(f"[{idx}] {step.action} -> {tgt} -> {status}")
        if step.error is not None:
            print(f"   ERROR: {step.error.type.value}: {step.error.message}")

    print("\n--- SUMMARY ---")
    print(f"Steps: {trace.metadata.total_steps}")
    print(f"Total duration (ms): {trace.metadata.total_duration_ms}")
    print(f"Status: {trace.status}\n")


def _print_legacy_rows(rows: list[dict[str, Any]]) -> None:
    print("\n=== EXECUTION TRACE ===\n")

    for r in rows:
        status = "✓" if r.get("success") else "✗"
        target = r.get("target") or "-"

        print(f"[{r.get('step')}] {r.get('action')} -> {target} -> {status}")

        if r.get("error"):
            print(f"     error: {r['error']}")

    print("\n=======================\n")
