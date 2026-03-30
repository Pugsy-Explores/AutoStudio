"""Stage 33 — Paired offline/live_model evaluation suite.

Same task set for both execution modes. Use for gap audit:
  python3 -m tests.agent_eval.run_paired --output artifacts/agent_eval_runs/paired_latest

Tasks: 4 representative edit-heavy tasks from core12 (same IDs as live4 subset).
"""

from __future__ import annotations

from dataclasses import replace

from tests.agent_eval.task_specs import TaskSpec

PAIRED4_TASK_IDS: tuple[str, ...] = (
    "core12_mini_repair_calc",
    "core12_mini_repair_parse",
    "core12_mini_feature_flags",
    "core12_pin_typer_repair",
)


def load_paired4_specs(*, evaluation_kind: str = "execution_regression") -> list[TaskSpec]:
    """Load paired4 specs. evaluation_kind=full_agent for live_model; execution_regression for offline."""
    from tests.agent_eval.suites.core12 import CORE12_TASKS

    by_id = {t.task_id: t for t in CORE12_TASKS}
    specs = [by_id[tid] for tid in PAIRED4_TASK_IDS]
    if evaluation_kind == "full_agent":
        return [replace(s, evaluation_kind="full_agent") for s in specs]
    return list(specs)
