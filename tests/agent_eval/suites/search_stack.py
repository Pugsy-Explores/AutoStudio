"""SEARCH stack eval tasks (retrieval + policy + repo_map contracts).

Easy → harder: exact-ish lookup, NL, path-ish, negative miss, retry-sensitive phrasing.

Run:
  python3 -m tests.agent_eval.runner --suite search_stack --execution-mode offline --output artifacts/agent_eval_runs/latest
  python3 -m tests.agent_eval.check_search_stack --run-dir artifacts/agent_eval_runs/latest

Live (real runtime; requires API keys as for other live suites):
  python3 -m tests.agent_eval.runner --suite search_stack --execution-mode live_model --output artifacts/agent_eval_runs/latest
"""

from __future__ import annotations

from dataclasses import replace

from tests.agent_eval.task_specs import EvaluationKind, TaskSpec

_MR01 = "mini_repos/mr01_arch"
_PYTEST = ("python3 -m pytest tests/test_smoke.py -q",)

_SEARCH_STACK_BASE: tuple[TaskSpec, ...] = (
    TaskSpec(
        task_id="ss_find_dispatch",
        layer="mini_repo",
        repo_id="mr01_arch",
        repo_path=_MR01,
        instruction="Find where the dispatch function is defined in flowkit",
        validation_commands=_PYTEST,
        grading_mode="validation_exit_code",
        orchestration_path="compat",
        tags=("search_stack", "easy"),
    ),
    TaskSpec(
        task_id="ss_path_oriented",
        layer="mini_repo",
        repo_id="mr01_arch",
        repo_path=_MR01,
        instruction="Locate code under src/flowkit related to the engine module",
        validation_commands=_PYTEST,
        grading_mode="validation_exit_code",
        orchestration_path="compat",
        tags=("search_stack", "medium"),
    ),
    TaskSpec(
        task_id="ss_natural_language",
        layer="mini_repo",
        repo_id="mr01_arch",
        repo_path=_MR01,
        instruction="Where is the main entry point for this package and how does it connect to settings?",
        validation_commands=_PYTEST,
        grading_mode="validation_exit_code",
        orchestration_path="compat",
        tags=("search_stack", "medium"),
    ),
    TaskSpec(
        task_id="ss_negative_miss",
        layer="mini_repo",
        repo_id="mr01_arch",
        repo_path=_MR01,
        instruction="Find class ZZZNonexistentSymbol99999 in this codebase",
        validation_commands=_PYTEST,
        grading_mode="validation_exit_code",
        orchestration_path="compat",
        tags=("search_stack", "hard", "negative"),
    ),
    TaskSpec(
        task_id="ss_retry_stress",
        layer="mini_repo",
        repo_id="mr01_arch",
        repo_path=_MR01,
        instruction="Search for the settings module, then explain how configuration is loaded",
        validation_commands=_PYTEST,
        grading_mode="validation_exit_code",
        orchestration_path="compat",
        tags=("search_stack", "hard", "multi_step"),
    ),
)

# Offline retrieval-quality oriented prompts (metrics via tests/agent_eval/check_retrieval_quality.py).
_RETRIEVAL_QUALITY_SPECS: tuple[TaskSpec, ...] = (
    TaskSpec(
        task_id="sq_entrypoint_arch",
        layer="mini_repo",
        repo_id="mr01_arch",
        repo_path=_MR01,
        instruction="Where is the main entry point and how does it connect to settings?",
        validation_commands=_PYTEST,
        grading_mode="validation_exit_code",
        orchestration_path="compat",
        tags=("search_stack", "retrieval_quality", "architecture"),
    ),
    TaskSpec(
        task_id="sq_symbol_exact",
        layer="mini_repo",
        repo_id="mr01_arch",
        repo_path=_MR01,
        instruction="Find the dispatch function definition in flowkit",
        validation_commands=_PYTEST,
        grading_mode="validation_exit_code",
        orchestration_path="compat",
        tags=("search_stack", "retrieval_quality", "symbol"),
    ),
    TaskSpec(
        task_id="sq_fallback_guard",
        layer="mini_repo",
        repo_id="mr01_arch",
        repo_path=_MR01,
        instruction="Where is the fallback branch when the engine errors?",
        validation_commands=_PYTEST,
        grading_mode="validation_exit_code",
        orchestration_path="compat",
        tags=("search_stack", "retrieval_quality", "region", "architecture"),
    ),
    TaskSpec(
        task_id="sq_impl_not_tests",
        layer="mini_repo",
        repo_id="mr01_arch",
        repo_path=_MR01,
        instruction="Show implementation of the main engine class not in tests",
        validation_commands=_PYTEST,
        grading_mode="validation_exit_code",
        orchestration_path="compat",
        tags=("search_stack", "retrieval_quality", "implementation"),
    ),
    TaskSpec(
        task_id="sq_2hop_arch",
        layer="mini_repo",
        repo_id="mr01_arch",
        repo_path=_MR01,
        instruction="How does flow from the CLI entry point connect to the engine module?",
        validation_commands=_PYTEST,
        grading_mode="validation_exit_code",
        orchestration_path="compat",
        tags=("search_stack", "retrieval_quality", "architecture"),
    ),
    TaskSpec(
        task_id="sq_config_settings",
        layer="mini_repo",
        repo_id="mr01_arch",
        repo_path=_MR01,
        instruction="Where is settings or config loaded for this package?",
        validation_commands=_PYTEST,
        grading_mode="validation_exit_code",
        orchestration_path="compat",
        tags=("search_stack", "retrieval_quality", "file", "architecture"),
    ),
)

# Harder architecture tasks for selector alpha eval (local-only; tagged selector_hard).
_SELECTOR_HARD_SPECS: tuple[TaskSpec, ...] = (
    TaskSpec(
        task_id="sq_hard_entrypoint_settings",
        layer="mini_repo",
        repo_id="mr01_arch",
        repo_path=_MR01,
        instruction="Trace the flow from the main package entrypoint to where settings are loaded and used at runtime.",
        validation_commands=_PYTEST,
        grading_mode="validation_exit_code",
        orchestration_path="compat",
        tags=("search_stack", "retrieval_quality", "architecture", "selector_hard"),
    ),
    TaskSpec(
        task_id="sq_hard_config_runtime",
        layer="mini_repo",
        repo_id="mr01_arch",
        repo_path=_MR01,
        instruction="Where is the config object loaded and how is it passed into the runtime execution path?",
        validation_commands=_PYTEST,
        grading_mode="validation_exit_code",
        orchestration_path="compat",
        tags=("search_stack", "retrieval_quality", "architecture", "selector_hard"),
    ),
    TaskSpec(
        task_id="sq_hard_fallback_callers",
        layer="mini_repo",
        repo_id="mr01_arch",
        repo_path=_MR01,
        instruction="Where is the fallback guard when the engine errors, and which callers reach that path?",
        validation_commands=_PYTEST,
        grading_mode="validation_exit_code",
        orchestration_path="compat",
        tags=("search_stack", "retrieval_quality", "architecture", "region", "selector_hard"),
    ),
    TaskSpec(
        task_id="sq_hard_dispatch_executor",
        layer="mini_repo",
        repo_id="mr01_arch",
        repo_path=_MR01,
        instruction="How does the dispatch or router hand off to the executor in this package?",
        validation_commands=_PYTEST,
        grading_mode="validation_exit_code",
        orchestration_path="compat",
        tags=("search_stack", "retrieval_quality", "architecture", "selector_hard"),
    ),
    TaskSpec(
        task_id="sq_hard_impl_not_tests",
        layer="mini_repo",
        repo_id="mr01_arch",
        repo_path=_MR01,
        instruction="Show the implementation of the core runtime module, excluding tests.",
        validation_commands=_PYTEST,
        grading_mode="validation_exit_code",
        orchestration_path="compat",
        tags=("search_stack", "retrieval_quality", "implementation", "architecture", "selector_hard"),
    ),
    TaskSpec(
        task_id="sq_hard_2hop_arch",
        layer="mini_repo",
        repo_id="mr01_arch",
        repo_path=_MR01,
        instruction="How does flow from the CLI entry point connect through the engine to the dispatch handler?",
        validation_commands=_PYTEST,
        grading_mode="validation_exit_code",
        orchestration_path="compat",
        tags=("search_stack", "retrieval_quality", "architecture", "selector_hard"),
    ),
)


def architecture_task_ids() -> frozenset[str]:
    """Architecture-oriented task ids for isolation from simple symbol lookup (A/B architecture slice)."""
    return frozenset(t.task_id for t in _RETRIEVAL_QUALITY_SPECS if "architecture" in (t.tags or ()))


def selector_hard_task_ids() -> frozenset[str]:
    """Harder architecture/explain task ids for stricter selector alpha eval (local-only)."""
    return frozenset(t.task_id for t in _SELECTOR_HARD_SPECS if "selector_hard" in (t.tags or ()))


def load_search_stack_specs(evaluation_kind: EvaluationKind | None = None) -> list[TaskSpec]:
    ek: EvaluationKind = evaluation_kind if evaluation_kind is not None else "execution_regression"
    combined = _SEARCH_STACK_BASE + _RETRIEVAL_QUALITY_SPECS + _SELECTOR_HARD_SPECS
    return [replace(t, evaluation_kind=ek) for t in combined]


def retrieval_quality_task_ids() -> frozenset[str]:
    """Task ids tagged for offline retrieval-quality metrics (A/B, check_retrieval_quality)."""
    return frozenset(t.task_id for t in _RETRIEVAL_QUALITY_SPECS + _SELECTOR_HARD_SPECS)
