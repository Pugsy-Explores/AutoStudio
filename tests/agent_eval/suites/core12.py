"""Core Stage-12 benchmark: 6 mini-repo + 6 pinned-snapshot tasks."""

from __future__ import annotations

from tests.agent_eval.task_specs import TaskSpec

# Paths are relative to tests/agent_eval/fixtures/

CORE12_TASKS: tuple[TaskSpec, ...] = (
    # --- Layer 1: mini repos (6) ---
    TaskSpec(
        task_id="core12_mini_explain_arch",
        layer="mini_repo",
        repo_id="mr01_arch",
        repo_path="mini_repos/mr01_arch",
        instruction=(
            "Using README.md and ARCHITECTURE.md together with src/flowkit/, explain how Settings "
            "from settings.load() reaches dispatch.handle via engine.run."
        ),
        setup_commands=(),
        validation_commands=("python3 -m pytest tests/ -q",),
        expected_artifacts=(),
        timeout_seconds=120,
        tags=("explain", "architecture", "layer1"),
        grading_mode="validation_exit_code",
        orchestration_path="hierarchical",
    ),
    TaskSpec(
        task_id="core12_mini_trace_flow",
        layer="mini_repo",
        repo_id="mr02_trace",
        repo_path="mini_repos/mr02_trace",
        instruction=(
            "Trace the call chain starting at chain.entry.run('ab') through middle.forward to tail.finish "
            "and state the final return value shape."
        ),
        setup_commands=(),
        validation_commands=("python3 -m pytest tests/ -q",),
        expected_artifacts=(),
        timeout_seconds=120,
        tags=("trace", "call_chain", "layer1"),
        grading_mode="validation_exit_code",
        orchestration_path="hierarchical",
    ),
    TaskSpec(
        task_id="core12_mini_repair_calc",
        layer="mini_repo",
        repo_id="mr03_calc",
        repo_path="mini_repos/mr03_calc",
        instruction="Repair src/calc/ops.py so multiply(2, 3) == 6 and tests pass.",
        setup_commands=(),
        validation_commands=("python3 -m pytest tests/test_ops.py -q",),
        expected_artifacts=(),
        timeout_seconds=120,
        tags=("repair", "tests", "layer1"),
        grading_mode="validation_exit_code",
        orchestration_path="compat",
    ),
    TaskSpec(
        task_id="core12_mini_repair_parse",
        layer="mini_repo",
        repo_id="mr04_parse",
        repo_path="mini_repos/mr04_parse",
        instruction="Fix tokenize() in src/parse/split.py to split on whitespace so test_tokenize_words passes.",
        setup_commands=(),
        validation_commands=("python3 -m pytest tests/test_split.py -q",),
        expected_artifacts=(),
        timeout_seconds=120,
        tags=("repair", "tests", "layer1"),
        grading_mode="validation_exit_code",
        orchestration_path="compat",
    ),
    TaskSpec(
        task_id="core12_mini_feature_flags",
        layer="mini_repo",
        repo_id="mr05_flags",
        repo_path="mini_repos/mr05_flags",
        instruction=(
            "Add a new function beta_enabled() -> bool in src/flags/store.py that returns False by default; "
            "keep existing is_verbose behavior."
        ),
        setup_commands=(),
        validation_commands=(
            'PYTHONPATH=src python3 -c "from flags.store import beta_enabled; assert beta_enabled() is False"',
        ),
        expected_artifacts=(),
        timeout_seconds=120,
        tags=("feature", "layer1"),
        grading_mode="validation_exit_code",
        orchestration_path="compat",
    ),
    TaskSpec(
        task_id="core12_mini_docs_version",
        layer="mini_repo",
        repo_id="mr06_version",
        repo_path="mini_repos/mr06_version",
        instruction=(
            "Make README.md and src/widget/constants.py agree on major.minor (either update README or APP_VERSION) "
            "so scripts/check_readme_version.py exits 0."
        ),
        setup_commands=(),
        validation_commands=("python3 scripts/check_readme_version.py",),
        expected_artifacts=(),
        timeout_seconds=120,
        tags=("docs", "consistency", "layer1"),
        grading_mode="validation_exit_code",
        orchestration_path="hierarchical",
    ),
    # --- Layer 2: pinned snapshots (6) ---
    TaskSpec(
        task_id="core12_pin_requests_explain_trace",
        layer="pinned_repo",
        repo_id="requests_snapshot",
        repo_path="pinned_repos/requests_snapshot",
        instruction=(
            "Read benchmark_local/TRACE_NOTE.md and src/requests/sessions.py (Session / request). "
            "Write benchmark_local/artifacts/explain_out.txt describing the redirect path: mention Session.request, "
            "hooks, and use an arrow or 'calls' to show ordering."
        ),
        setup_commands=("mkdir -p benchmark_local/artifacts",),
        validation_commands=(),
        expected_artifacts=("benchmark_local/artifacts/explain_out.txt",),
        timeout_seconds=180,
        tags=("explain", "trace", "requests", "layer2"),
        grading_mode="explain_artifact",
        orchestration_path="hierarchical",
        explain_required_substrings=("Session.request", "hooks", "->"),
    ),
    TaskSpec(
        task_id="core12_pin_click_docs_code",
        layer="pinned_repo",
        repo_id="click_snapshot",
        repo_path="pinned_repos/click_snapshot",
        instruction=(
            "Align benchmark_local/DECORATORS_NOTE.md with benchmark_local/bench_click_meta.py "
            "so the stability word in the markdown matches CLICK_BENCH_API_STABILITY."
        ),
        setup_commands=(),
        validation_commands=("python3 benchmark_local/check_docs_code.py",),
        expected_artifacts=(),
        timeout_seconds=180,
        tags=("docs", "consistency", "click", "layer2"),
        grading_mode="validation_exit_code",
        orchestration_path="hierarchical",
    ),
    TaskSpec(
        task_id="core12_pin_typer_repair",
        layer="pinned_repo",
        repo_id="typer_snapshot",
        repo_path="pinned_repos/typer_snapshot",
        instruction="Fix benchmark_local/bench_math.double so double(3) == 6.",
        setup_commands=(),
        validation_commands=("PYTHONPATH=. python3 -m pytest benchmark_local/test_bench_math.py -q",),
        expected_artifacts=(),
        timeout_seconds=180,
        tags=("repair", "tests", "typer", "layer2"),
        grading_mode="validation_exit_code",
        orchestration_path="compat",
    ),
    TaskSpec(
        task_id="core12_pin_typer_feature",
        layer="pinned_repo",
        repo_id="typer_snapshot",
        repo_path="pinned_repos/typer_snapshot",
        instruction=(
            "Implement benchmark_local/bench_cli.describe_app() to return a non-empty one-line description string."
        ),
        setup_commands=(),
        validation_commands=("PYTHONPATH=. python3 -m pytest benchmark_local/test_bench_cli.py -q",),
        expected_artifacts=(),
        timeout_seconds=180,
        tags=("feature", "typer", "layer2"),
        grading_mode="validation_exit_code",
        orchestration_path="compat",
    ),
    TaskSpec(
        task_id="core12_pin_requests_httpbin_doc",
        layer="pinned_repo",
        repo_id="requests_snapshot",
        repo_path="pinned_repos/requests_snapshot",
        instruction=(
            "Make benchmark_local/HTTPBIN_NOTE.md and benchmark_local/bench_requests_meta.py agree on the "
            "httpbin host (same netloc as documented in the bold URL)."
        ),
        setup_commands=(),
        validation_commands=("python3 benchmark_local/check_httpbin_doc.py",),
        expected_artifacts=(),
        timeout_seconds=180,
        tags=("docs", "consistency", "requests", "layer2"),
        grading_mode="validation_exit_code",
        orchestration_path="hierarchical",
    ),
    TaskSpec(
        task_id="core12_pin_click_multifile",
        layer="pinned_repo",
        repo_id="click_snapshot",
        repo_path="pinned_repos/click_snapshot",
        instruction=(
            "Rename the shared suffix from legacy to unified in benchmark_local/part_a.py and any dependent "
            "text so benchmark_local/test_multifile.py passes."
        ),
        setup_commands=(),
        validation_commands=("PYTHONPATH=src:. python3 -m pytest benchmark_local/test_multifile.py -q",),
        expected_artifacts=(),
        timeout_seconds=180,
        tags=("refactor", "multi_file", "click", "layer2"),
        grading_mode="validation_exit_code",
        orchestration_path="compat",
    ),
)


def load_core12() -> list[TaskSpec]:
    return list(CORE12_TASKS)
