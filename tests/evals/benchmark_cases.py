"""
Stage 12 benchmark corpus: 12 fixed tasks over the sample_app fixture.

Path modes:
- compat: single-phase compatibility delegation (mocked execution).
- hierarchical: two-phase non-compat execution (mocked execution).

Evaluation hooks are structural for the harness (offline, deterministic mocks).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

PathMode = Literal["compat", "hierarchical"]
EvaluationHook = Literal["structural_loop_output_ok"]


@dataclass(frozen=True)
class BenchmarkCase:
    task_id: str
    category: str
    instruction: str
    """Relative path under tests/evals/fixtures/ (fixture root for this task)."""
    fixture_relative: str
    path_mode: PathMode
    evaluation_hook: EvaluationHook


# Corpus root (mini-project) — all tasks point here; instructions reference files inside it.
_SAMPLE_APP = "mini_projects/sample_app"

BENCHMARK_CASES: tuple[BenchmarkCase, ...] = (
    # 1–2 explain architecture (docs + code) — hierarchical
    BenchmarkCase(
        "s12_explain_arch_01",
        "explain_architecture",
        (
            "Find docs in ARCHITECTURE.md and explain how the widget pipeline layers interact "
            f"and how data flows from config into pipeline.run in this repo (fixture root: {_SAMPLE_APP})."
        ),
        _SAMPLE_APP,
        "hierarchical",
        "structural_loop_output_ok",
    ),
    BenchmarkCase(
        "s12_explain_arch_02",
        "explain_architecture",
        (
            "Locate README.md and describe how configuration from config.py relates to pipeline.run "
            f"in the sample fixture under {_SAMPLE_APP}."
        ),
        _SAMPLE_APP,
        "hierarchical",
        "structural_loop_output_ok",
    ),
    # 3–4 trace flow — hierarchical
    BenchmarkCase(
        "s12_trace_flow_01",
        "trace_flow",
        (
            "Find documentation for the data pipeline and trace how run() calls process() and transform() "
            f"across files in {_SAMPLE_APP}."
        ),
        _SAMPLE_APP,
        "hierarchical",
        "structural_loop_output_ok",
    ),
    BenchmarkCase(
        "s12_trace_flow_02",
        "trace_flow",
        (
            "Show README and explain how the entrypoint in sample_app/__init__.py connects to pipeline.run "
            f"in fixture {_SAMPLE_APP}."
        ),
        _SAMPLE_APP,
        "hierarchical",
        "structural_loop_output_ok",
    ),
    # 5–6 small bug fix — compat
    BenchmarkCase(
        "s12_bugfix_01",
        "bug_fix",
        (
            f"In fixture {_SAMPLE_APP}, edit src/sample_app/pipeline.py so multiply(2, 3) returns 6 "
            "(fix the off-by-one style bug in multiply)."
        ),
        _SAMPLE_APP,
        "compat",
        "structural_loop_output_ok",
    ),
    BenchmarkCase(
        "s12_bugfix_02",
        "bug_fix",
        (
            f"In fixture {_SAMPLE_APP}, fix config.load_timeout so the default is 30 seconds, not -1."
        ),
        _SAMPLE_APP,
        "compat",
        "structural_loop_output_ok",
    ),
    # 7–8 small feature — compat
    BenchmarkCase(
        "s12_feature_01",
        "feature_addition",
        (
            f"In {_SAMPLE_APP}, change benchmark_ok_marker() in src/sample_app/pipeline.py to return "
            "the string 'ok' exactly."
        ),
        _SAMPLE_APP,
        "compat",
        "structural_loop_output_ok",
    ),
    BenchmarkCase(
        "s12_feature_02",
        "feature_addition",
        (
            f"In {_SAMPLE_APP}, extend src/sample_app/config.py with a new boolean feature_flag_x "
            "defaulting to False."
        ),
        _SAMPLE_APP,
        "compat",
        "structural_loop_output_ok",
    ),
    # 9–10 tests — compat
    BenchmarkCase(
        "s12_tests_01",
        "add_or_repair_tests",
        (
            f"In {_SAMPLE_APP}, repair tests/sample_app_pipeline_tests.py so test_multiply_wrong_on_purpose passes "
            "once multiply() is correct (or adjust the test to match the intended behavior)."
        ),
        _SAMPLE_APP,
        "compat",
        "structural_loop_output_ok",
    ),
    BenchmarkCase(
        "s12_tests_02",
        "add_or_repair_tests",
        (
            f"In {_SAMPLE_APP}, add a new pytest test in tests/sample_app_pipeline_tests.py that asserts add(1, 2) == 3 "
            "(if not already covered)."
        ),
        _SAMPLE_APP,
        "compat",
        "structural_loop_output_ok",
    ),
    # 11–12 multi-file consistency — hierarchical
    BenchmarkCase(
        "s12_multifile_01",
        "multi_file_consistency",
        (
            "Find docs in docs/ and explain how to keep APP_CONSTANT_NAME consistent when renaming "
            f"across constants.py and __init__.py in {_SAMPLE_APP}."
        ),
        _SAMPLE_APP,
        "hierarchical",
        "structural_loop_output_ok",
    ),
    BenchmarkCase(
        "s12_multifile_02",
        "multi_file_consistency",
        (
            "Locate README and describe how version constants should stay aligned between "
            f"constants.APP_VERSION and __version__ in {_SAMPLE_APP}."
        ),
        _SAMPLE_APP,
        "hierarchical",
        "structural_loop_output_ok",
    ),
)


def fixtures_root() -> Path:
    """Absolute path to tests/evals/fixtures."""
    return Path(__file__).resolve().parent / "fixtures"


def load_benchmark_cases() -> list[BenchmarkCase]:
    """Return a copy of the benchmark list (validated)."""
    return list(BENCHMARK_CASES)


def validate_benchmark_case(case: BenchmarkCase) -> None:
    """Validate required fields and path resolution."""
    if not case.task_id or not str(case.task_id).strip():
        raise ValueError("task_id required")
    if not case.instruction or not str(case.instruction).strip():
        raise ValueError(f"{case.task_id}: instruction required")
    if case.path_mode not in ("compat", "hierarchical"):
        raise ValueError(f"{case.task_id}: invalid path_mode")
    if case.evaluation_hook != "structural_loop_output_ok":
        raise ValueError(f"{case.task_id}: unsupported evaluation_hook")
    root = fixtures_root() / case.fixture_relative
    if not root.is_dir():
        raise ValueError(f"{case.task_id}: fixture root missing: {root}")


def validate_all_cases(cases: list[BenchmarkCase] | None = None) -> None:
    """Validate every case and uniqueness of task_id."""
    items = cases if cases is not None else load_benchmark_cases()
    seen: set[str] = set()
    for c in items:
        if c.task_id in seen:
            raise ValueError(f"duplicate task_id: {c.task_id}")
        seen.add(c.task_id)
        validate_benchmark_case(c)
