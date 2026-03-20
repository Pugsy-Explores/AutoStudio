"""Stage 21 — adversarial transfer benchmark (12 tasks).

Tasks stress transfer beyond audit12/holdout8:
- Different function names (normalize_ratios, tokenize_bytes, cfg_verbose, get_severity, validate_input)
- Different layouts (core/, io/, cfg/, impl/, mod_a/, validation/, runtime/)
- Different validation (assert_*, bin/, scripts/)
- Docs with BUILD_NUMBER, DEFAULT_ENDPOINT, CURRENT_VERSION (not APP_VERSION/API_BASE)
- Multifile with BASE_URI (not SHARED_PREFIX)
- Some instructions mention intent but not exact file path
"""

from __future__ import annotations

from tests.agent_eval.task_specs import TaskSpec

# Paths relative to tests/agent_eval/fixtures/

ADVERSARIAL12_TASKS: tuple[TaskSpec, ...] = (
    # --- repair (different names) ---
    TaskSpec(
        task_id="adv_repair_ratios",
        layer="mini_repo",
        repo_id="av01_ratios",
        repo_path="adversarial_mini_repos/av01_ratios",
        instruction="Fix normalize_ratios in core/ratios.py so that 12 divided by 4 equals 3.0. Ensure tests pass.",
        setup_commands=(),
        validation_commands=("PYTHONPATH=core python3 -m pytest tests/test_ratios.py -q",),
        expected_artifacts=(),
        timeout_seconds=120,
        tags=("repair", "tests", "adversarial"),
        grading_mode="validation_exit_code",
        orchestration_path="compat",
    ),
    TaskSpec(
        task_id="adv_repair_parse",
        layer="mini_repo",
        repo_id="av02_parse",
        repo_path="adversarial_mini_repos/av02_parse",
        instruction="Fix parse_bytes in io/bytes_parser.py to split on whitespace and return a list of tokens.",
        setup_commands=(),
        validation_commands=("PYTHONPATH=. python3 -m pytest tests/test_parser.py -q",),
        expected_artifacts=(),
        timeout_seconds=120,
        tags=("repair", "tests", "adversarial"),
        grading_mode="validation_exit_code",
        orchestration_path="compat",
    ),
    TaskSpec(
        task_id="adv_repair_guard",
        layer="mini_repo",
        repo_id="av09_guard",
        repo_path="adversarial_mini_repos/av09_guard",
        instruction="Fix the validation guard so it returns True for non-empty strings. Run bin/assert_guard.py to verify.",
        setup_commands=(),
        validation_commands=("python3 bin/assert_guard.py",),
        expected_artifacts=(),
        timeout_seconds=120,
        tags=("repair", "adversarial"),
        grading_mode="validation_exit_code",
        orchestration_path="compat",
    ),
    # --- feature (different names, insertion) ---
    TaskSpec(
        task_id="adv_feature_defaults",
        layer="mini_repo",
        repo_id="av03_defaults",
        repo_path="adversarial_mini_repos/av03_defaults",
        instruction="Add cfg_verbose() -> bool in cfg/defaults.py that returns False by default.",
        setup_commands=(),
        validation_commands=("PYTHONPATH=. python3 -m pytest tests/test_defaults.py -q",),
        expected_artifacts=(),
        timeout_seconds=120,
        tags=("feature", "adversarial"),
        grading_mode="validation_exit_code",
        orchestration_path="compat",
    ),
    TaskSpec(
        task_id="adv_feature_severity",
        layer="mini_repo",
        repo_id="av04_severity",
        repo_path="adversarial_mini_repos/av04_severity",
        instruction="Add get_severity() -> str in logging/levels.py returning a non-empty string (e.g. 'WARN').",
        setup_commands=(),
        validation_commands=("PYTHONPATH=. python3 -m pytest tests/test_levels.py -q",),
        expected_artifacts=(),
        timeout_seconds=120,
        tags=("feature", "adversarial"),
        grading_mode="validation_exit_code",
        orchestration_path="compat",
    ),
    TaskSpec(
        task_id="adv_feature_config",
        layer="mini_repo",
        repo_id="av10_config",
        repo_path="adversarial_mini_repos/av10_config",
        instruction="Add max_retries() -> int in the runtime options module returning 3.",
        setup_commands=(),
        validation_commands=("PYTHONPATH=. python3 -m pytest tests/test_options.py -q",),
        expected_artifacts=(),
        timeout_seconds=120,
        tags=("feature", "adversarial"),
        grading_mode="validation_exit_code",
        orchestration_path="compat",
    ),
    # --- docs-consistency (different source of truth) ---
    TaskSpec(
        task_id="adv_docs_release",
        layer="mini_repo",
        repo_id="av05_release",
        repo_path="adversarial_mini_repos/av05_release",
        instruction="Align RELEASE_NOTES.md and pkg/version.py so the version in the release header matches BUILD_NUMBER. Run scripts/assert_release_match.py to verify.",
        setup_commands=(),
        validation_commands=("python3 scripts/assert_release_match.py",),
        expected_artifacts=(),
        timeout_seconds=120,
        tags=("docs", "consistency", "adversarial"),
        grading_mode="validation_exit_code",
        orchestration_path="hierarchical",
    ),
    TaskSpec(
        task_id="adv_docs_spec",
        layer="mini_repo",
        repo_id="av06_spec",
        repo_path="adversarial_mini_repos/av06_spec",
        instruction="Make SPEC.md and impl/spec.py agree on the default endpoint netloc. Run scripts/assert_spec_match.py to verify.",
        setup_commands=(),
        validation_commands=("python3 scripts/assert_spec_match.py",),
        expected_artifacts=(),
        timeout_seconds=120,
        tags=("docs", "consistency", "adversarial"),
        grading_mode="validation_exit_code",
        orchestration_path="hierarchical",
    ),
    TaskSpec(
        task_id="adv_docs_version",
        layer="mini_repo",
        repo_id="av11_changelog2",
        repo_path="adversarial_mini_repos/av11_changelog2",
        instruction="Align VERSION_HISTORY.md and core/version.py so the version header matches CURRENT_VERSION. Run bin/assert_version_sync.py to verify.",
        setup_commands=(),
        validation_commands=("python3 bin/assert_version_sync.py",),
        expected_artifacts=(),
        timeout_seconds=120,
        tags=("docs", "consistency", "adversarial"),
        grading_mode="validation_exit_code",
        orchestration_path="hierarchical",
    ),
    # --- explain-artifact ---
    TaskSpec(
        task_id="adv_explain_flow",
        layer="mini_repo",
        repo_id="av07_flow",
        repo_path="adversarial_mini_repos/av07_flow",
        instruction=(
            "Read FLOW_NOTE.md and ingest/, transform/, output/. "
            "Write OUT/flow_diagram.md describing the flow from ingest.process through transform.apply to output.render. "
            "Use arrows or 'calls' to show ordering."
        ),
        setup_commands=("mkdir -p OUT",),
        validation_commands=(),
        expected_artifacts=("OUT/flow_diagram.md",),
        timeout_seconds=120,
        tags=("explain", "flow", "adversarial"),
        grading_mode="explain_artifact",
        orchestration_path="hierarchical",
        explain_required_substrings=("ingest", "transform", "output", "->"),
    ),
    TaskSpec(
        task_id="adv_explain_artifact",
        layer="mini_repo",
        repo_id="av12_artifact",
        repo_path="adversarial_mini_repos/av12_artifact",
        instruction=(
            "Read FLOW_NOTE.md and ingest/, transform/, export/. "
            "Write OUT/flow_diagram.md describing the flow from ingest.read through transform.run to export.write. "
            "Use arrows or 'calls' to show ordering."
        ),
        setup_commands=("mkdir -p OUT",),
        validation_commands=(),
        expected_artifacts=("OUT/flow_diagram.md",),
        timeout_seconds=120,
        tags=("explain", "artifact", "adversarial"),
        grading_mode="explain_artifact",
        orchestration_path="hierarchical",
        explain_required_substrings=("ingest", "transform", "export", "->"),
    ),
    # --- multi-file edit ---
    TaskSpec(
        task_id="adv_multifile_const",
        layer="mini_repo",
        repo_id="av08_const",
        repo_path="adversarial_mini_repos/av08_const",
        instruction="Rename BASE_URI from 'http' to 'https' in mod_a/params.py and any dependent code so tests pass.",
        setup_commands=(),
        validation_commands=("PYTHONPATH=. python3 -m pytest tests/test_const.py -q",),
        expected_artifacts=(),
        timeout_seconds=120,
        tags=("refactor", "multi_file", "adversarial"),
        grading_mode="validation_exit_code",
        orchestration_path="compat",
    ),
)


def load_adversarial12_specs() -> list[TaskSpec]:
    return list(ADVERSARIAL12_TASKS)
