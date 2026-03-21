"""Live-model routing contract suite (Stage 39/40).

Probes instruction routing via plan_resolution_telemetry. Use with --execution-mode live_model.

Offline runs use execution_regression (stubs); routing assertions are only meaningful after live runs.

Use:
  python3 -m tests.agent_eval.runner --suite routing_contract --execution-mode live_model
  python3 -m tests.agent_eval.check_routing_contract --run-dir artifacts/agent_eval_runs/latest
"""

from __future__ import annotations

from dataclasses import replace

from tests.agent_eval.task_specs import EvaluationKind, TaskSpec

_MR01 = "mini_repos/mr01_arch"
_PYTEST = ("python3 -m pytest tests/test_smoke.py -q",)

_ROUTING_CONTRACT_BASE: tuple[TaskSpec, ...] = (
    TaskSpec(
        task_id="rc_doc",
        layer="mini_repo",
        repo_id="mr01_arch",
        repo_path=_MR01,
        instruction="Find the README in this repo",
        validation_commands=_PYTEST,
        grading_mode="validation_exit_code",
        orchestration_path="compat",
        tags=("routing_contract",),
    ),
    TaskSpec(
        task_id="rc_search",
        layer="mini_repo",
        repo_id="mr01_arch",
        repo_path=_MR01,
        instruction="Where is the main entry point defined?",
        validation_commands=_PYTEST,
        grading_mode="validation_exit_code",
        orchestration_path="compat",
        tags=("routing_contract",),
    ),
    TaskSpec(
        task_id="rc_explain",
        layer="mini_repo",
        repo_id="mr01_arch",
        repo_path=_MR01,
        instruction="Explain how the settings module works",
        validation_commands=_PYTEST,
        grading_mode="validation_exit_code",
        orchestration_path="compat",
        tags=("routing_contract",),
    ),
    TaskSpec(
        task_id="rc_edit",
        layer="mini_repo",
        repo_id="mr01_arch",
        repo_path=_MR01,
        instruction="Refactor the auth module to add logging",
        validation_commands=_PYTEST,
        grading_mode="validation_exit_code",
        orchestration_path="compat",
        tags=("routing_contract",),
    ),
    TaskSpec(
        task_id="rc_two_phase",
        layer="mini_repo",
        repo_id="mr01_arch",
        repo_path=_MR01,
        instruction="Find the architecture docs and explain the main flow",
        validation_commands=_PYTEST,
        grading_mode="validation_exit_code",
        orchestration_path="compat",
        tags=("routing_contract",),
    ),
    TaskSpec(
        task_id="rc_not_compound",
        layer="mini_repo",
        repo_id="mr01_arch",
        repo_path=_MR01,
        instruction="Add logging and run tests",
        validation_commands=_PYTEST,
        grading_mode="validation_exit_code",
        orchestration_path="compat",
        tags=("routing_contract",),
    ),
    TaskSpec(
        task_id="rc_not_validate",
        layer="mini_repo",
        repo_id="mr01_arch",
        repo_path=_MR01,
        instruction="Run pytest on tests/",
        validation_commands=_PYTEST,
        grading_mode="validation_exit_code",
        orchestration_path="compat",
        tags=("routing_contract",),
    ),
    TaskSpec(
        task_id="rc_vague",
        layer="mini_repo",
        repo_id="mr01_arch",
        repo_path=_MR01,
        instruction="Something is wrong",
        validation_commands=_PYTEST,
        grading_mode="validation_exit_code",
        orchestration_path="compat",
        tags=("routing_contract",),
    ),
    TaskSpec(
        task_id="rc_low_conf",
        layer="mini_repo",
        repo_id="mr01_arch",
        repo_path=_MR01,
        instruction="Find the login function",
        validation_commands=_PYTEST,
        grading_mode="validation_exit_code",
        orchestration_path="compat",
        tags=("routing_contract",),
    ),
)


def load_routing_contract_specs(evaluation_kind: EvaluationKind | None = None) -> list[TaskSpec]:
    """Load routing-contract tasks. Default is execution_regression; use full_agent with live_model."""
    ek: EvaluationKind = evaluation_kind if evaluation_kind is not None else "execution_regression"
    return [replace(t, evaluation_kind=ek) for t in _ROUTING_CONTRACT_BASE]
