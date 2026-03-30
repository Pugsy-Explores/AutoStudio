"""Optional pattern-dimension retrieval checks (tiny external clones).

Run after indexing:
  python3 scripts/index_pattern_coverage_repos.py

Or set ``RUN_PATTERN_COVERAGE=1`` — the module fixture will clone and index when needed
(network required on first run).

  RUN_PATTERN_COVERAGE=1 pytest tests/retrieval/test_pattern_coverage_retrieval.py -v
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _want_pattern_coverage() -> bool:
    return os.environ.get("RUN_PATTERN_COVERAGE", "").strip() == "1"


def _load_eval_retrieval_module():
    path = _REPO_ROOT / "scripts" / "eval_retrieval_pipeline.py"
    name = "_eval_retrieval_pipeline"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load eval_retrieval_pipeline")
    mod = importlib.util.module_from_spec(spec)
    # Py3.12+ dataclasses resolve cls.__module__ in sys.modules during class body
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def pattern_coverage_cases():
    """Clones + manifest validation only (no engine — fast bucket check)."""
    if not _want_pattern_coverage():
        pytest.skip("set RUN_PATTERN_COVERAGE=1 to run pattern coverage retrieval tests")

    from tests.retrieval.pattern_coverage import (  # noqa: PLC0415
        apply_pattern_coverage_env,
        build_pattern_coverage_cases,
        index_pattern_repos,
        pattern_repos_indexed,
    )

    apply_pattern_coverage_env(_REPO_ROOT)
    try:
        cases = build_pattern_coverage_cases(_REPO_ROOT)
    except Exception as exc:
        pytest.skip(f"pattern repos unavailable: {exc}")

    if not pattern_repos_indexed(_REPO_ROOT):
        index_pattern_repos(_REPO_ROOT, verbose=False)

    return cases


@pytest.fixture(scope="module")
def pattern_engine_and_cases():
    if not _want_pattern_coverage():
        pytest.skip("set RUN_PATTERN_COVERAGE=1 to run pattern coverage retrieval tests")

    os.environ.setdefault("SKIP_STARTUP_CHECKS", "1")
    os.environ["AGENT_V2_ENABLE_EXPLORATION_ENGINE_V2"] = "1"
    # Smoke test: rerank off keeps the module test bounded; use eval script for rerank-on runs.
    os.environ.setdefault("AGENT_V2_EXPLORATION_DISCOVERY_RERANK_ENABLED", "0")

    from tests.retrieval.pattern_coverage import (  # noqa: PLC0415
        apply_pattern_coverage_env,
        build_pattern_coverage_cases,
        index_pattern_repos,
        pattern_repos_indexed,
    )

    apply_pattern_coverage_env(_REPO_ROOT)
    try:
        build_pattern_coverage_cases(_REPO_ROOT)
    except Exception as exc:
        pytest.skip(f"pattern repos unavailable: {exc}")

    if not pattern_repos_indexed(_REPO_ROOT):
        index_pattern_repos(_REPO_ROOT, verbose=False)

    original_cwd = os.getcwd()
    os.chdir(str(_REPO_ROOT))
    os.environ["SERENA_PROJECT_DIR"] = str(_REPO_ROOT)

    from agent.tools.react_tools import register_all_tools
    register_all_tools()

    ev = _load_eval_retrieval_module()
    cases = build_pattern_coverage_cases(_REPO_ROOT)
    engine = ev.build_engine(_REPO_ROOT)
    try:
        yield engine, cases, ev.run_case, ev
    finally:
        os.chdir(original_cwd)


@pytest.mark.retrieval
def test_pattern_coverage_pipeline_runs_and_reports(pattern_engine_and_cases):
    """Harness smoke: no exceptions, fixed display slots; rank/top-k misses are not CI failures."""
    engine, cases, run_case, ev = pattern_engine_and_cases
    display_n = getattr(ev, "DISPLAY_TOP_N", 10)

    for case in cases:
        result = run_case(engine, case)
        assert result.case_id == case.case_id
        assert result.total_candidates >= 0
        assert len(result.top_paths) == display_n
        assert len(result.top_scores) == display_n
        assert len(result.top_symbols) == display_n


@pytest.mark.retrieval
def test_pattern_coverage_category_tags(pattern_coverage_cases):
    """Manifest must list every bucket in ``required_pattern_buckets`` (single source of truth)."""
    from tests.retrieval.pattern_coverage import load_pattern_manifest  # noqa: PLC0415

    cases = pattern_coverage_cases
    data = load_pattern_manifest()
    required = set(data.get("required_pattern_buckets") or [])
    cats = {c.category for c in cases}
    for bucket in required:
        assert bucket in cats, f"missing pattern bucket {bucket!r} — update pattern_sources.json"

