"""Tests for agent runtime upgrade: execution loop, trajectory store, metrics, safety, run_tests."""

import json
import os
from pathlib import Path

import pytest

from config import agent_runtime
from editing.test_runner_utils import (
    extract_failed_test,
    is_flaky,
    parse_pytest_failure,
    run_tests_raw,
)


def test_agent_runtime_config():
    """Config agent_runtime exposes expected keys."""
    assert hasattr(agent_runtime, "MAX_EDIT_ATTEMPTS")
    assert hasattr(agent_runtime, "MAX_PATCH_LINES")
    assert hasattr(agent_runtime, "MAX_PATCH_FILES")
    assert hasattr(agent_runtime, "TRAJECTORY_STORE_ENABLED")
    assert agent_runtime.MAX_PATCH_FILES == 5 or agent_runtime.MAX_PATCH_FILES >= 1


def test_test_runner_utils_run_tests_raw(tmp_path):
    """run_tests_raw with no tests returns failure and error_type."""
    (tmp_path / "foo.py").write_text("x = 1\n")
    passed, stdout, stderr, error_type = run_tests_raw(str(tmp_path), "python -m pytest -x -q", timeout=5)
    assert passed is False or error_type is not None
    assert isinstance(stdout, str)
    assert isinstance(stderr, str)


def test_test_runner_utils_extract_failed_test():
    """extract_failed_test parses pytest FAILED line."""
    out = "FAILED tests/test_foo.py::test_bar - AssertionError"
    assert extract_failed_test(out) == "tests/test_foo.py::test_bar"
    assert extract_failed_test("no match") is None


def test_test_runner_utils_parse_pytest_failure():
    """parse_pytest_failure returns file, error_type, stacktrace."""
    stdout = "FAILED tests/a.py::test_x - AssertionError"
    stderr = ""
    file_path, err_type, stacktrace = parse_pytest_failure(stdout, stderr)
    assert err_type == "test_failure"
    assert "test" in (file_path or "") or "a.py" in (file_path or "")


def test_run_tests_detection_pyproject(tmp_path):
    """run_tests with pyproject.toml detects pytest."""
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\ntestpaths = []\n")
    (tmp_path / "foo.py").write_text("x = 1\n")
    from agent.tools.run_tests import run_tests
    result = run_tests(str(tmp_path), timeout=10)
    assert "passed" in result
    assert "stdout" in result
    assert "stderr" in result


def test_execution_loop_no_changes(tmp_path):
    """run_edit_test_fix_loop with context that yields no changes returns no_changes."""
    from agent.runtime.execution_loop import run_edit_test_fix_loop
    context = {"ranked_context": [], "retrieved_files": [], "retrieved_symbols": [], "project_root": str(tmp_path)}
    result = run_edit_test_fix_loop("do nothing impossible", context, str(tmp_path), max_attempts=1)
    assert result.get("success") is False
    assert result.get("error") == "no_changes"


def test_execution_loop_patch_size_exceeded(tmp_path):
    """run_edit_test_fix_loop rejects patch with too many files when plan_diff returns many."""
    from agent.runtime.execution_loop import run_edit_test_fix_loop
    from unittest.mock import patch
    context = {
        "ranked_context": [{"file": f"f{i}.py", "symbol": "x", "snippet": "pass"} for i in range(10)],
        "retrieved_files": [f"f{i}.py" for i in range(10)],
        "retrieved_symbols": [],
        "project_root": str(tmp_path),
    }
    with patch("agent.runtime.execution_loop.plan_diff") as m:
        m.return_value = {"changes": [{"file": f"f{i}.py", "symbol": "x", "action": "modify", "patch": "x", "reason": "r"} for i in range(10)]}
        result = run_edit_test_fix_loop("edit all", context, str(tmp_path), max_attempts=1)
    assert result.get("success") is False
    assert result.get("error") == "patch_rejected"


def test_execution_trajectory_store_append(tmp_path, monkeypatch):
    """append_trajectory writes v2 JSONL when enabled."""
    monkeypatch.setattr("config.agent_runtime.TRAJECTORY_STORE_ENABLED", True)
    monkeypatch.setattr("config.agent_runtime.TRAJECTORY_STORE_DIR", "traj")
    import importlib
    from agent.memory import execution_trajectory_store
    importlib.reload(execution_trajectory_store)
    append_trajectory = execution_trajectory_store.append_trajectory
    append_trajectory(
        goal="fix bug",
        plan=["a.py"],
        retrieved_files=[],
        patch="diff",
        test_output="FAILED",
        failure_type="test_failure",
        retry_strategy="retry_edit",
        success=False,
        project_root=str(tmp_path),
    )
    path = tmp_path / "traj" / "trajectories.jsonl"
    assert path.exists()
    line = path.read_text().strip()
    record = json.loads(line)
    assert record.get("schema_version") == "v2"
    assert record.get("goal", "").startswith("fix bug")
    assert record.get("success") is False


def test_strategy_explorer_returns_alternatives():
    """explore_strategies returns list of strategy dicts."""
    from agent.strategy.strategy_explorer import explore_strategies
    from agent.meta.retry_planner import RetryHints
    hints = RetryHints(strategy="rewrite_retrieval_query", rewrite_query="q", plan_override=None, retrieve_files=[])
    out = explore_strategies("goal", hints, [], max_strategies=3)
    assert isinstance(out, list)
    if out:
        assert "strategy_name" in out[0]
        assert "plan_steps" in out[0]
        assert "score" in out[0]


def test_metrics_record_and_get(tmp_path):
    """record_metric and get_metrics work; reset_metrics clears."""
    from agent.observability.metrics import record_metric, get_metrics, reset_metrics
    reset_metrics()
    record_metric("test_metric", 1.0, append_jsonl=False)
    m = get_metrics()
    assert m.get("test_metric") == 1.0
    record_metric("test_metric", 2.0, append_jsonl=False)
    m = get_metrics()
    assert m.get("test_metric") == 3.0
    reset_metrics()
    assert get_metrics() == {}


def test_patch_executor_forbidden_path(tmp_path):
    """execute_patch rejects .env file."""
    from editing.patch_executor import execute_patch
    env_file = tmp_path / ".env"
    env_file.write_text("X=1\n")
    patch_plan = {
        "changes": [{"file": str(env_file), "patch": {"symbol": "", "action": "insert", "target_node": "function_body_start", "code": "Y=2"}}],
    }
    result = execute_patch(patch_plan, project_root=str(tmp_path))
    assert result["success"] is False
    assert result.get("error") == "forbidden_path"


def test_patch_executor_path_outside_repo(tmp_path):
    """execute_patch rejects path outside project root."""
    from editing.patch_executor import execute_patch
    # Path that resolves to a sibling of tmp_path (outside project root)
    outside_file = str((tmp_path / ".." / "other_outside" / "foo.py").resolve())
    patch_plan = {
        "changes": [{"file": outside_file, "patch": {"symbol": "x", "action": "insert", "target_node": "function_body_start", "code": "pass"}}],
    }
    result = execute_patch(patch_plan, project_root=str(tmp_path))
    assert result["success"] is False
    assert result.get("error") == "path_outside_repo"


def test_analyze_failures_script_empty(tmp_path):
    """analyze_failures script runs and reports no data when dir empty."""
    import subprocess
    import sys
    (tmp_path / "data" / "trajectories").mkdir(parents=True)
    r = subprocess.run(
        [sys.executable, "-m", "scripts.analyze_failures", "--project-root", str(tmp_path), "--trajectory-dir", "data/trajectories"],
        cwd=str(Path(__file__).resolve().parent.parent),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert r.returncode == 0
    assert "No trajectory data" in r.stdout or "No trajectory" in r.stdout or "No failure" in r.stdout


def test_retrieval_test_downweight_config():
    """Retrieval config has test downweight and service dir options."""
    from config import retrieval_config
    assert hasattr(retrieval_config, "RETRIEVAL_TEST_DOWNWEIGHT")
    assert hasattr(retrieval_config, "RETRIEVAL_USE_SERVICE_DIRS")
    assert 0 <= retrieval_config.RETRIEVAL_TEST_DOWNWEIGHT <= 1
