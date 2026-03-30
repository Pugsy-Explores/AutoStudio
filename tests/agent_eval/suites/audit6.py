"""Stage 12.1 — fixed six-task audit subset (3 mini + 3 pinned). Same TaskSpecs as core12; no new tasks."""

from __future__ import annotations

# Mini-repo: repair + feature (edit-heavy, compat path)
# Pinned: typer repair/feature + click multifile (compat path)
AUDIT6_TASK_IDS: tuple[str, ...] = (
    "core12_mini_repair_calc",
    "core12_mini_repair_parse",
    "core12_mini_feature_flags",
    "core12_pin_typer_repair",
    "core12_pin_typer_feature",
    "core12_pin_click_multifile",
)


def load_audit6_specs():
    from tests.agent_eval.suites.core12 import CORE12_TASKS

    by_id = {t.task_id: t for t in CORE12_TASKS}
    return [by_id[tid] for tid in AUDIT6_TASK_IDS]
