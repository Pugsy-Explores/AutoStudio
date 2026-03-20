"""Stage 14 — full 12-task audit suite (all core12 in real mode)."""

from __future__ import annotations

from tests.agent_eval.suites.core12 import CORE12_TASKS


def load_audit12_specs():
    """All 12 core12 tasks for real-mode benchmark expansion."""
    return list(CORE12_TASKS)
