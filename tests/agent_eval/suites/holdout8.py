"""Stage 19 — holdout benchmark suite (8 tasks, not tuned in Stages 13–18).

Tasks use new fixture repos, different wording, and varied validation patterns
(validate_*.py, verify_*.py, run_*.py) to avoid benchmark overfitting.
"""

from __future__ import annotations

from tests.agent_eval.task_specs import TaskSpec

# Paths relative to tests/agent_eval/fixtures/

HOLDOUT8_TASKS: tuple[TaskSpec, ...] = (
    # --- repair ---
    TaskSpec(
        task_id="holdout_repair_math",
        layer="mini_repo",
        repo_id="mh01_math",
        repo_path="holdout_mini_repos/mh01_math",
        instruction="Fix safe_div in src/math_utils/ops.py so that 10 divided by 2 equals 5.0. Ensure tests pass.",
        setup_commands=(),
        validation_commands=("PYTHONPATH=src python3 -m pytest tests/test_math_ops.py -q",),
        expected_artifacts=(),
        timeout_seconds=120,
        tags=("repair", "tests", "holdout"),
        grading_mode="validation_exit_code",
        orchestration_path="compat",
    ),
    TaskSpec(
        task_id="holdout_repair_validator",
        layer="mini_repo",
        repo_id="mh06_validator",
        repo_path="holdout_mini_repos/mh06_validator",
        instruction="Fix is_valid in src/valid/check.py so it returns True for non-empty strings. Run scripts/run_verify.py to verify.",
        setup_commands=(),
        validation_commands=("python3 scripts/run_verify.py",),
        expected_artifacts=(),
        timeout_seconds=120,
        tags=("repair", "holdout"),
        grading_mode="validation_exit_code",
        orchestration_path="compat",
    ),
    # --- small feature ---
    TaskSpec(
        task_id="holdout_feature_config",
        layer="mini_repo",
        repo_id="mh02_config",
        repo_path="holdout_mini_repos/mh02_config",
        instruction="Add a function enable_debug() -> bool in src/config/settings.py that returns False by default.",
        setup_commands=(),
        validation_commands=("PYTHONPATH=src python3 -m pytest tests/test_config.py -q",),
        expected_artifacts=(),
        timeout_seconds=120,
        tags=("feature", "holdout"),
        grading_mode="validation_exit_code",
        orchestration_path="compat",
    ),
    TaskSpec(
        task_id="holdout_feature_logger",
        layer="mini_repo",
        repo_id="mh07_logger",
        repo_path="holdout_mini_repos/mh07_logger",
        instruction="Add log_level() -> str in src/logging_utils/core.py returning a non-empty string (e.g. 'INFO').",
        setup_commands=(),
        validation_commands=("PYTHONPATH=src python3 -m pytest tests/test_logger.py -q",),
        expected_artifacts=(),
        timeout_seconds=120,
        tags=("feature", "holdout"),
        grading_mode="validation_exit_code",
        orchestration_path="compat",
    ),
    # --- docs-consistency ---
    TaskSpec(
        task_id="holdout_docs_changelog",
        layer="mini_repo",
        repo_id="mh03_changelog",
        repo_path="holdout_mini_repos/mh03_changelog",
        instruction="Align CHANGELOG.md and lib/version.py so the version in the changelog header matches RELEASE_VERSION. Run scripts/validate_changelog_version.py to verify.",
        setup_commands=(),
        validation_commands=("python3 scripts/validate_changelog_version.py",),
        expected_artifacts=(),
        timeout_seconds=120,
        tags=("docs", "consistency", "holdout"),
        grading_mode="validation_exit_code",
        orchestration_path="hierarchical",
    ),
    TaskSpec(
        task_id="holdout_docs_api",
        layer="mini_repo",
        repo_id="mh08_api",
        repo_path="holdout_mini_repos/mh08_api",
        instruction="Make API.md and spec/api_spec.py agree on the API base URL netloc. Run scripts/verify_api_docs.py to verify.",
        setup_commands=(),
        validation_commands=("python3 scripts/verify_api_docs.py",),
        expected_artifacts=(),
        timeout_seconds=120,
        tags=("docs", "consistency", "holdout"),
        grading_mode="validation_exit_code",
        orchestration_path="hierarchical",
    ),
    # --- explain-artifact ---
    TaskSpec(
        task_id="holdout_explain_trace",
        layer="mini_repo",
        repo_id="mh04_trace",
        repo_path="holdout_mini_repos/mh04_trace",
        instruction=(
            "Read FLOW_NOTE.md and src/client/, src/handler/, src/response/. "
            "Write HO/trace_output.md describing the flow from client.send through handler.process to response.build. "
            "Use arrows or 'calls' to show ordering."
        ),
        setup_commands=("mkdir -p HO",),
        validation_commands=(),
        expected_artifacts=("HO/trace_output.md",),
        timeout_seconds=120,
        tags=("explain", "trace", "holdout"),
        grading_mode="explain_artifact",
        orchestration_path="hierarchical",
        explain_required_substrings=("client", "handler", "response", "->"),
    ),
    # --- multi-file edit ---
    TaskSpec(
        task_id="holdout_multifile_prefix",
        layer="mini_repo",
        repo_id="mh05_multifile",
        repo_path="holdout_mini_repos/mh05_multifile",
        instruction="Rename SHARED_PREFIX from 'old' to 'new' in pkg_a/constants.py and any dependent code so tests pass.",
        setup_commands=(),
        validation_commands=("PYTHONPATH=. python3 -m pytest tests/test_prefix.py -q",),
        expected_artifacts=(),
        timeout_seconds=120,
        tags=("refactor", "multi_file", "holdout"),
        grading_mode="validation_exit_code",
        orchestration_path="compat",
    ),
)


def load_holdout8_specs() -> list[TaskSpec]:
    return list(HOLDOUT8_TASKS)
