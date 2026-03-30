"""Stage 27 — external-repo evaluation suite (6 real tasks on pinned open-source repos).

Tasks test generalization beyond handcrafted benchmark fixtures:
- 2 repair tasks
- 2 docs/code consistency tasks
- 1 explain-artifact task
- 1 small feature task

Uses pinned_repos (requests, typer, click) with tasks distinct from audit12.
All validation is deterministic, local, and runnable offline.
"""

from __future__ import annotations

from tests.agent_eval.task_specs import TaskSpec

# Paths relative to tests/agent_eval/fixtures/

EXTERNAL6_TASKS: tuple[TaskSpec, ...] = (
    # --- repair (2) ---
    TaskSpec(
        task_id="ext_repair_typer_halve",
        layer="pinned_repo",
        repo_id="typer_snapshot",
        repo_path="pinned_repos/typer_snapshot",
        instruction="Fix halve() in benchmark_local/bench_math.py so that halve(4) equals 2. Run tests to verify.",
        setup_commands=(),
        validation_commands=("PYTHONPATH=. python3 -m pytest benchmark_local/test_bench_math.py -k test_halve -q",),
        expected_artifacts=(),
        timeout_seconds=120,
        tags=("repair", "tests", "external"),
        grading_mode="validation_exit_code",
        orchestration_path="compat",
    ),
    TaskSpec(
        task_id="ext_repair_click_add",
        layer="pinned_repo",
        repo_id="click_snapshot",
        repo_path="pinned_repos/click_snapshot",
        instruction="Fix add_ints() in benchmark_local/arithmetic.py so that add_ints(2, 3) equals 5. Ensure tests pass.",
        setup_commands=(),
        validation_commands=("PYTHONPATH=.:src python3 -m pytest benchmark_local/test_arithmetic.py -q",),
        expected_artifacts=(),
        timeout_seconds=120,
        tags=("repair", "tests", "external"),
        grading_mode="validation_exit_code",
        orchestration_path="compat",
    ),
    # --- docs/code consistency (2) ---
    TaskSpec(
        task_id="ext_docs_requests_version",
        layer="pinned_repo",
        repo_id="requests_snapshot",
        repo_path="pinned_repos/requests_snapshot",
        instruction="Align benchmark_local/VERSION_NOTE.md and benchmark_local/version_meta.py so the version in the note matches RELEASE_VERSION. Run python3 benchmark_local/check_version_sync.py to verify.",
        setup_commands=(),
        validation_commands=("python3 benchmark_local/check_version_sync.py",),
        expected_artifacts=(),
        timeout_seconds=120,
        tags=("docs", "consistency", "external"),
        grading_mode="validation_exit_code",
        orchestration_path="hierarchical",
    ),
    TaskSpec(
        task_id="ext_docs_typer_readme",
        layer="pinned_repo",
        repo_id="typer_snapshot",
        repo_path="pinned_repos/typer_snapshot",
        instruction="Align benchmark_local/README_BENCH.md and benchmark_local/typer_ver.py so the version in the markdown matches TYPER_BENCH_VER. Run python3 benchmark_local/check_readme_bench.py to verify.",
        setup_commands=(),
        validation_commands=("python3 benchmark_local/check_readme_bench.py",),
        expected_artifacts=(),
        timeout_seconds=120,
        tags=("docs", "consistency", "external"),
        grading_mode="validation_exit_code",
        orchestration_path="hierarchical",
    ),
    # --- explain-artifact (1) ---
    TaskSpec(
        task_id="ext_explain_click_decorators",
        layer="pinned_repo",
        repo_id="click_snapshot",
        repo_path="pinned_repos/click_snapshot",
        instruction=(
            "Read src/click/decorators.py and benchmark_local/DECORATORS_NOTE.md. "
            "Write benchmark_local/artifacts/decorator_flow.md describing how @click.command decorates a function. "
            "Use arrows or 'calls' to show the flow."
        ),
        setup_commands=("mkdir -p benchmark_local/artifacts",),
        validation_commands=(),
        expected_artifacts=("benchmark_local/artifacts/decorator_flow.md",),
        timeout_seconds=120,
        tags=("explain", "decorators", "external"),
        grading_mode="explain_artifact",
        orchestration_path="hierarchical",
        explain_required_substrings=("command", "decorator", "->"),
    ),
    # --- small feature (1) ---
    TaskSpec(
        task_id="ext_feature_requests_timeout",
        layer="pinned_repo",
        repo_id="requests_snapshot",
        repo_path="pinned_repos/requests_snapshot",
        instruction="Fix get_timeout() in benchmark_local/bench_requests_meta.py so it returns 30. Run benchmark_local/test_request_meta.py to verify.",
        setup_commands=(),
        validation_commands=("PYTHONPATH=. python3 -m pytest benchmark_local/test_request_meta.py -q",),
        expected_artifacts=(),
        timeout_seconds=120,
        tags=("feature", "external"),
        grading_mode="validation_exit_code",
        orchestration_path="compat",
    ),
)


def load_external6_specs() -> list[TaskSpec]:
    return list(EXTERNAL6_TASKS)
