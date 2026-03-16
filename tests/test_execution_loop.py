"""Tests for agent/runtime/execution_loop: snapshot rollback, syntax validation, retry guard, strategy timing."""

from pathlib import Path
from unittest.mock import patch

import pytest


def test_successful_patch(tmp_path):
    """Patch applies, syntax valid, tests pass -> success."""
    (tmp_path / "foo.py").write_text("def bar():\n    return 1\n")
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    context = {"ranked_context": [], "retrieved_files": ["foo.py"], "retrieved_symbols": []}

    with patch("agent.runtime.execution_loop.plan_diff") as m_plan:
        m_plan.return_value = {
            "changes": [
                {"file": "foo.py", "symbol": "bar", "action": "modify", "patch": "return 2", "reason": "r"},
            ]
        }
        with patch("agent.runtime.execution_loop.execute_patch") as m_exec:
            m_exec.return_value = {"success": True, "files_modified": [str(tmp_path / "foo.py")], "patches_applied": 1}
            with patch("agent.runtime.execution_loop.validate_project") as m_syntax:
                m_syntax.return_value = {"valid": True, "error": ""}
                with patch("agent.runtime.execution_loop.run_tests") as m_tests:
                    m_tests.return_value = {"passed": True}
                    from agent.runtime.execution_loop import run_edit_test_fix_loop
                    result = run_edit_test_fix_loop("change bar to return 2", context, str(tmp_path), max_attempts=2)
    assert result.get("success") is True
    assert result.get("attempt") == 1
    assert "files_modified" in result


def test_syntax_error_skips_tests_and_rolls_back(tmp_path):
    """Syntax invalid after patch -> skip tests, rollback, return syntax_error."""
    original = "def bar():\n    return 1\n"
    (tmp_path / "foo.py").write_text(original)
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    context = {"ranked_context": [], "retrieved_files": ["foo.py"], "retrieved_symbols": []}

    with patch("agent.runtime.execution_loop.plan_diff") as m_plan:
        m_plan.return_value = {
            "changes": [{"file": "foo.py", "symbol": "bar", "action": "modify", "patch": "x", "reason": "r"}],
        }
        with patch("agent.runtime.execution_loop.execute_patch") as m_exec:
            m_exec.return_value = {"success": True, "files_modified": [str(tmp_path / "foo.py")], "patches_applied": 1}
            with patch("agent.runtime.execution_loop.validate_project") as m_syntax:
                m_syntax.return_value = {"valid": False, "error": "SyntaxError: invalid syntax"}
                from agent.runtime.execution_loop import run_edit_test_fix_loop
                result = run_edit_test_fix_loop("break syntax", context, str(tmp_path), max_attempts=1)
    assert result.get("success") is False
    assert result.get("error") == "syntax_error"
    assert result.get("failure_type") == "syntax_error"
    assert (tmp_path / "foo.py").read_text() == original


def test_retry_success(tmp_path):
    """First attempt fails (test), second succeeds."""
    (tmp_path / "foo.py").write_text("x = 1\n")
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    context = {"ranked_context": [], "retrieved_files": ["foo.py"], "retrieved_symbols": []}

    test_calls = []

    def mock_plan(*args, **kwargs):
        return {"changes": [{"file": "foo.py", "symbol": "", "action": "modify", "patch": "x = 2", "reason": "r"}]}

    def fail_then_pass(*args, **kwargs):
        test_calls.append(1)
        if len(test_calls) < 2:
            return {"passed": False, "stdout": "", "stderr": "FAILED", "error_type": "test_failure"}
        return {"passed": True}

    with patch("agent.runtime.execution_loop.plan_diff", side_effect=mock_plan):
        with patch("agent.runtime.execution_loop.execute_patch") as m_exec:
            m_exec.return_value = {"success": True, "files_modified": [str(tmp_path / "foo.py")], "patches_applied": 1}
            with patch("agent.runtime.execution_loop.validate_project") as m_syntax:
                m_syntax.return_value = {"valid": True, "error": ""}
                with patch("agent.runtime.execution_loop.run_tests", side_effect=fail_then_pass):
                    from agent.runtime.execution_loop import run_edit_test_fix_loop
                    result = run_edit_test_fix_loop("edit foo", context, str(tmp_path), max_attempts=3)
    assert result.get("success") is True
    assert result.get("attempt") == 2


def test_repeated_failure_stop(tmp_path):
    """Same error twice -> loop stops (MAX_SAME_ERROR_RETRIES)."""
    (tmp_path / "foo.py").write_text("x = 1\n")
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    context = {"ranked_context": [], "retrieved_files": ["foo.py"], "retrieved_symbols": []}

    from config.agent_runtime import MAX_SAME_ERROR_RETRIES

    with patch("agent.runtime.execution_loop.plan_diff") as m_plan:
        m_plan.return_value = {
            "changes": [{"file": "foo.py", "symbol": "", "action": "modify", "patch": "x = 2", "reason": "r"}],
        }
        with patch("agent.runtime.execution_loop.execute_patch") as m_exec:
            m_exec.return_value = {"success": True, "files_modified": [str(tmp_path / "foo.py")], "patches_applied": 1}
            with patch("agent.runtime.execution_loop.validate_project") as m_syntax:
                m_syntax.return_value = {"valid": True, "error": ""}
                with patch("agent.runtime.execution_loop.run_tests") as m_tests:
                    m_tests.return_value = {"passed": False, "stdout": "", "stderr": "fail", "error_type": "test_failure"}
                    from agent.runtime.execution_loop import run_edit_test_fix_loop
                    result = run_edit_test_fix_loop("edit foo", context, str(tmp_path), max_attempts=5)
    assert result.get("success") is False
    assert result.get("error") == "test_failure"
    assert result.get("attempt") == MAX_SAME_ERROR_RETRIES


def test_rollback_restore_verification(tmp_path):
    """After syntax validation failure, files are restored to original (snapshot rollback)."""
    original = "def bar():\n    return 1\n"
    (tmp_path / "foo.py").write_text(original)
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    context = {"ranked_context": [], "retrieved_files": ["foo.py"], "retrieved_symbols": [{"file": "foo.py", "symbol": "bar"}]}

    with patch("agent.runtime.execution_loop.plan_diff") as m_plan:
        m_plan.return_value = {
            "changes": [
                {
                    "file": "foo.py",
                    "symbol": "bar",
                    "action": "modify",
                    "patch": "    x = 1  # inserted",
                    "reason": "r",
                },
            ]
        }
        with patch("agent.runtime.execution_loop.validate_project") as m_syntax:
            m_syntax.return_value = {"valid": False, "error": "SyntaxError"}
            from agent.runtime.execution_loop import run_edit_test_fix_loop
            result = run_edit_test_fix_loop("change bar", context, str(tmp_path), max_attempts=1)
    assert result.get("success") is False
    assert result.get("error") == "syntax_error"
    # execute_patch is not mocked: it runs and modifies file; then syntax fails and we rollback
    assert (tmp_path / "foo.py").read_text() == original
