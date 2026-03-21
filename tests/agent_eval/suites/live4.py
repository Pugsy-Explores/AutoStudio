"""Stage 28 — live-model proof suite. Four tasks with evaluation_kind=full_agent.

Use: python3 -m tests.agent_eval.runner --execution-mode live_model --suite live4

Requires configured hosted model. Run valid only when model_call_count > 0 and no integrity failures.
"""

from __future__ import annotations

from dataclasses import replace

from tests.agent_eval.task_specs import TaskSpec

LIVE4_TASK_IDS: tuple[str, ...] = (
    "core12_mini_repair_calc",
    "core12_mini_repair_parse",
    "core12_mini_feature_flags",
    "core12_pin_typer_repair",
)


def load_live4_specs() -> list[TaskSpec]:
    from tests.agent_eval.suites.core12 import CORE12_TASKS

    by_id = {t.task_id: t for t in CORE12_TASKS}
    return [replace(by_id[tid], evaluation_kind="full_agent") for tid in LIVE4_TASK_IDS]
