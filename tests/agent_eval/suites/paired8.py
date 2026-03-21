"""Stage 35 — Policy-grade paired suite: cross-section of task types.

Covers: repair, feature, docs_consistency, explain_artifact, multi_file.
Use for release-gating policy evaluation.
"""

from __future__ import annotations

from dataclasses import replace

from tests.agent_eval.task_specs import TaskSpec

# Canonical task types for policy reporting
TASK_TYPE_REPAIR = "repair"
TASK_TYPE_FEATURE = "feature"
TASK_TYPE_DOCS_CONSISTENCY = "docs_consistency"
TASK_TYPE_EXPLAIN_ARTIFACT = "explain_artifact"
TASK_TYPE_MULTI_FILE = "multi_file"

# One task per type for breadth; two where we have strong coverage
PAIRED8_TASK_IDS: tuple[str, ...] = (
    "core12_mini_repair_calc",      # repair
    "core12_pin_typer_repair",      # repair
    "core12_mini_feature_flags",    # feature
    "core12_pin_typer_feature",     # feature
    "core12_mini_docs_version",     # docs_consistency
    "core12_pin_click_docs_code",   # docs_consistency
    "core12_pin_requests_explain_trace",  # explain_artifact
    "core12_pin_click_multifile",   # multi_file
)


def load_paired8_specs(*, evaluation_kind: str = "execution_regression") -> list[TaskSpec]:
    """Load paired8 specs. evaluation_kind=full_agent for live_model."""
    from tests.agent_eval.suites.core12 import CORE12_TASKS

    by_id = {t.task_id: t for t in CORE12_TASKS}
    specs = [by_id[tid] for tid in PAIRED8_TASK_IDS]
    if evaluation_kind == "full_agent":
        return [replace(s, evaluation_kind="full_agent") for s in specs]
    return list(specs)
