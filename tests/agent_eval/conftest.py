"""Pytest configuration for agent_eval tests."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

# Pre-import numpy before mocks/threads to avoid RecursionError in rank_bm25 and reranker
import numpy  # noqa: F401


@pytest.fixture(scope="session")
def run_suite_core12_mocked(tmp_path_factory):
    """
    Run core12 suite once per session (mocked mode) and reuse for tests that only need
    (run_dir, results, summary). Saves ~130s by avoiding 3 duplicate full-suite runs.
    """
    tmp_path = tmp_path_factory.mktemp("agent_eval_core12_shared")
    fx_src = Path(__file__).resolve().parent / "fixtures"
    fx_dst = tmp_path / "tests" / "agent_eval" / "fixtures"
    fx_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(fx_src, fx_dst)

    import tests.agent_eval.runner as rmod

    output_arg = tmp_path / "artifacts" / "agent_eval_runs" / "latest"
    run_dir, results, summary = rmod.run_suite(
        "core12",
        output_arg,
        repo_root=tmp_path,
        execution_mode="mocked",
    )
    return run_dir, results, summary
