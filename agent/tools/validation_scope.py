"""
Resolve which test command the edit→test inner loop should run.

Benchmark harness sets AUTOSTUDIO_INNER_VALIDATION_CMD to the task's validation command
(pytest preferred; else first shell check script) so the inner loop does not widen to
repo-root discovery from pyproject.toml.
"""

from __future__ import annotations

import os
from pathlib import Path

# Set by tests.agent_eval.real_execution for real-mode benchmark runs (pytest only).
ENV_INNER_VALIDATION_CMD = "AUTOSTUDIO_INNER_VALIDATION_CMD"

# Optional explicit override from orchestration (future use).
CTX_KEY_INNER_VALIDATION_CMD = "inner_validation_test_cmd"


def resolve_inner_loop_validation(project_root: str, context: dict | None) -> dict:
    """
    Decide test_cmd for run_tests and emit scope telemetry.

    Returns a dict with:
      test_cmd: str | None — None → run_tests uses project auto-detection (repo-wide).
      requested_validation_target: str | None
      resolved_validation_command: str
      resolved_validation_cwd: str
      validation_scope_kind: "exact" | "benchmark_local" | "repo_wide"
    """
    ctx = context if isinstance(context, dict) else {}
    cwd = str(Path(project_root).resolve())
    requested: str | None = None
    inner = ctx.get(CTX_KEY_INNER_VALIDATION_CMD)
    if isinstance(inner, str) and inner.strip():
        requested = inner.strip()
    if not requested:
        env_val = os.environ.get(ENV_INNER_VALIDATION_CMD)
        if isinstance(env_val, str) and env_val.strip():
            requested = env_val.strip()

    if requested and requested.strip():
        kind = _scope_kind_for_command(requested)
        return {
            "test_cmd": requested,
            "requested_validation_target": requested,
            "resolved_validation_command": requested,
            "resolved_validation_cwd": cwd,
            "validation_scope_kind": kind,
        }

    return {
        "test_cmd": None,
        "requested_validation_target": requested,
        "resolved_validation_command": "",
        "resolved_validation_cwd": cwd,
        "validation_scope_kind": "repo_wide",
    }


def _scope_kind_for_command(cmd: str) -> str:
    c = cmd.replace("\\", "/")
    if "benchmark_local/" in c or "benchmark_local\\" in cmd:
        return "benchmark_local"
    return "exact"
