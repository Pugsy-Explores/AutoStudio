"""Task specification schema and validation for Stage 12 agent_eval."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

Layer = Literal["mini_repo", "pinned_repo"]
GradingMode = Literal["structural_loop", "validation_exit_code", "explain_artifact"]
OrchestrationPath = Literal["compat", "hierarchical"]
EvaluationKind = Literal["execution_regression", "full_agent"]


@dataclass(frozen=True)
class TaskSpec:
    task_id: str
    layer: Layer
    repo_id: str
    """Relative path under tests/agent_eval/fixtures/ (e.g. mini_repos/mr01_arch)."""
    repo_path: str
    instruction: str
    setup_commands: tuple[str, ...] = ()
    validation_commands: tuple[str, ...] = ()
    expected_artifacts: tuple[str, ...] = ()
    timeout_seconds: int = 120
    tags: tuple[str, ...] = ()
    grading_mode: GradingMode = "validation_exit_code"
    orchestration_path: OrchestrationPath = "hierarchical"
    """Substring checks for explain_artifact grading (artifact paths in expected_artifacts)."""
    explain_required_substrings: tuple[str, ...] = ()
    """Stage 28: execution_regression (plan injection ok) or full_agent (live model required)."""
    evaluation_kind: EvaluationKind = "execution_regression"


def get_fixtures_root() -> Path:
    return Path(__file__).resolve().parent / "fixtures"


def resolve_repo_dir(spec: TaskSpec) -> Path:
    p = get_fixtures_root() / spec.repo_path
    return p.resolve()


def validate_task_spec(spec: TaskSpec) -> None:
    if not spec.task_id.strip():
        raise ValueError("task_id required")
    if spec.layer not in ("mini_repo", "pinned_repo"):
        raise ValueError(f"{spec.task_id}: invalid layer")
    if not spec.repo_id.strip():
        raise ValueError(f"{spec.task_id}: repo_id required")
    root = resolve_repo_dir(spec)
    if not root.is_dir():
        raise ValueError(f"{spec.task_id}: repo_path not found: {root}")
    if spec.grading_mode not in ("structural_loop", "validation_exit_code", "explain_artifact"):
        raise ValueError(f"{spec.task_id}: invalid grading_mode")
    if spec.orchestration_path not in ("compat", "hierarchical"):
        raise ValueError(f"{spec.task_id}: invalid orchestration_path")
    if getattr(spec, "evaluation_kind", "execution_regression") not in ("execution_regression", "full_agent"):
        raise ValueError(f"{spec.task_id}: invalid evaluation_kind")
    if spec.timeout_seconds < 1:
        raise ValueError(f"{spec.task_id}: timeout_seconds must be >= 1")


def validate_suite(specs: list[TaskSpec]) -> None:
    seen: set[str] = set()
    for s in specs:
        if s.task_id in seen:
            raise ValueError(f"duplicate task_id: {s.task_id}")
        seen.add(s.task_id)
        validate_task_spec(s)


def task_spec_to_dict(spec: TaskSpec) -> dict[str, Any]:
    from dataclasses import asdict

    return asdict(spec)
