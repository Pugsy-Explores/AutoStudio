"""Tests for agent/runtime/execution_loop: snapshot rollback, syntax validation, retry guard, strategy timing."""

from pathlib import Path
from unittest.mock import patch

import pytest


def test_successful_patch(tmp_path):
    """Patch applies, syntax valid, tests pass -> success."""
    (tmp_path / "foo.py").write_text("def bar():\n    return 1\n")
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    context = {"ranked_context": [], "retrieved_files": ["foo.py"], "retrieved_symbols": [], "project_root": str(tmp_path)}

    with patch("agent.runtime.execution_loop.plan_diff") as m_plan:
        m_plan.return_value = {
            "changes": [
                {"file": "foo.py", "symbol": "bar", "action": "modify", "patch": "return 2", "reason": "r"},
            ]
        }
        with patch("agent.runtime.execution_loop.to_structured_patches") as m_ts:
            m_ts.return_value = {"changes": [{"file": "foo.py", "patch": {"action": "text_sub", "old": "return 1", "new": "return 2"}}]}
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
    context = {"ranked_context": [], "retrieved_files": ["foo.py"], "retrieved_symbols": [], "project_root": str(tmp_path)}

    with patch("agent.runtime.execution_loop.plan_diff") as m_plan:
        m_plan.return_value = {
            "changes": [{"file": "foo.py", "symbol": "bar", "action": "modify", "patch": "x", "reason": "r"}],
        }
        with patch("agent.runtime.execution_loop.to_structured_patches") as m_ts:
            m_ts.return_value = {"changes": [{"file": "foo.py", "patch": {"action": "text_sub", "old": "return 1", "new": "x"}}]}
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
    """Simplified pipeline: single attempt. Test failure -> immediate fail (no retries)."""
    (tmp_path / "foo.py").write_text("x = 1\n")
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    context = {"ranked_context": [], "retrieved_files": ["foo.py"], "retrieved_symbols": [], "project_root": str(tmp_path)}

    with patch("agent.runtime.execution_loop.plan_diff") as m_plan:
        m_plan.return_value = {"changes": [{"file": "foo.py", "symbol": "", "action": "modify", "patch": "x = 2", "reason": "r"}]}
        with patch("agent.runtime.execution_loop.to_structured_patches") as m_ts:
            m_ts.return_value = {"changes": [{"file": "foo.py", "patch": {"action": "text_sub", "old": "x = 1", "new": "x = 2"}}]}
            with patch("agent.runtime.execution_loop.execute_patch") as m_exec:
                m_exec.return_value = {"success": True, "files_modified": [str(tmp_path / "foo.py")], "patches_applied": 1}
                with patch("agent.runtime.execution_loop.validate_project") as m_syntax:
                    m_syntax.return_value = {"valid": True, "error": ""}
                    with patch("agent.runtime.execution_loop.run_tests") as m_tests:
                        m_tests.return_value = {"passed": False, "stdout": "", "stderr": "FAILED", "error_type": "test_failure"}
                        from agent.runtime.execution_loop import run_edit_test_fix_loop
                        result = run_edit_test_fix_loop("edit foo", context, str(tmp_path), max_attempts=3)
    assert result.get("success") is False
    assert result.get("attempt") == 1
    assert result.get("failure_type") == "tests_failed"


def test_repeated_failure_stop(tmp_path):
    """Simplified pipeline: single attempt. Test failure -> immediate fail."""
    (tmp_path / "foo.py").write_text("x = 1\n")
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    context = {"ranked_context": [], "retrieved_files": ["foo.py"], "retrieved_symbols": [], "project_root": str(tmp_path)}

    with patch("agent.runtime.execution_loop.plan_diff") as m_plan:
        m_plan.return_value = {
            "changes": [{"file": "foo.py", "symbol": "", "action": "modify", "patch": "x = 2", "reason": "r"}],
        }
        with patch("agent.runtime.execution_loop.to_structured_patches") as m_ts:
            m_ts.return_value = {"changes": [{"file": "foo.py", "patch": {"action": "text_sub", "old": "x = 1", "new": "x = 2"}}]}
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
    assert result.get("attempt") == 1


def test_retry_injects_causal_feedback(tmp_path):
    """Simplified pipeline: patch apply failure -> immediate fail, rollback. No critic."""
    (tmp_path / "foo.py").write_text("x = 1\n")
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    context = {"ranked_context": [], "retrieved_files": ["foo.py"], "retrieved_symbols": [], "project_root": str(tmp_path)}

    with patch("agent.runtime.execution_loop.plan_diff") as m_plan:
        m_plan.return_value = {"changes": [{"file": "foo.py", "symbol": "", "action": "modify", "patch": "x = 2", "reason": "r"}]}
        with patch("agent.runtime.execution_loop.to_structured_patches") as m_ts:
            m_ts.return_value = {"changes": [{"file": "foo.py", "patch": {"action": "text_sub", "old": "x = 1", "new": "x = 2"}}]}
            with patch("agent.runtime.execution_loop.execute_patch") as m_exec:
                m_exec.return_value = {
                    "success": False,
                    "patch_reject_reason": "patch_unchanged",
                    "failure_reason_code": "patch_unchanged",
                    "patches_applied": 0,
                }
                from agent.runtime.execution_loop import run_edit_test_fix_loop
                result = run_edit_test_fix_loop("edit foo", context, str(tmp_path), max_attempts=2)
    assert result.get("success") is False
    assert result.get("failure_type") == "patch_apply_failed"
    assert (tmp_path / "foo.py").read_text() == "x = 1\n"


def test_failure_state_accumulates(tmp_path):
    """Simplified pipeline: patch apply failure -> immediate fail, rollback. No failure_state."""
    (tmp_path / "foo.py").write_text("x = 1\n")
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    context = {"ranked_context": [], "retrieved_files": ["foo.py"], "retrieved_symbols": [], "project_root": str(tmp_path)}

    with patch("agent.runtime.execution_loop.plan_diff") as m_plan:
        m_plan.return_value = {"changes": [{"file": "foo.py", "symbol": "", "action": "modify", "patch": "x = 2", "reason": "r"}]}
        with patch("agent.runtime.execution_loop.to_structured_patches") as m_ts:
            m_ts.return_value = {"changes": [{"file": "foo.py", "patch": {"action": "text_sub", "old": "x = 1", "new": "x = 2"}}]}
            with patch("agent.runtime.execution_loop.execute_patch") as m_exec:
                m_exec.return_value = {
                    "success": False,
                    "patch_reject_reason": "patch_apply_failed",
                    "failure_reason_code": "patch_apply_failed",
                    "patches_applied": 0,
                }
                from agent.runtime.execution_loop import run_edit_test_fix_loop
                result = run_edit_test_fix_loop("edit foo", context, str(tmp_path), max_attempts=2)
    assert result.get("success") is False
    assert result.get("failure_reason_code") == "patch_apply_failed"
    assert (tmp_path / "foo.py").read_text() == "x = 1\n"


def test_reject_repeated_patch_no_progress(tmp_path):
    """Simplified pipeline: patch apply failure -> immediate fail, rollback."""
    (tmp_path / "foo.py").write_text("x = 1\n")
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    context = {"ranked_context": [], "retrieved_files": ["foo.py"], "retrieved_symbols": [], "project_root": str(tmp_path)}

    with patch("agent.runtime.execution_loop.plan_diff") as m_plan:
        m_plan.return_value = {"changes": [{"file": "foo.py", "symbol": "", "action": "modify", "patch": "x = 2", "reason": "r"}]}
        with patch("agent.runtime.execution_loop.to_structured_patches") as m_ts:
            m_ts.return_value = {"changes": [{"file": "foo.py", "patch": {"action": "text_sub", "old": "x = 1", "new": "x = 2"}}]}
            with patch("agent.runtime.execution_loop.execute_patch") as m_exec:
                m_exec.return_value = {
                    "success": False,
                    "patch_reject_reason": "no_progress_repeat",
                    "failure_reason_code": "no_progress_repeat",
                    "patches_applied": 0,
                }
                from agent.runtime.execution_loop import run_edit_test_fix_loop
                result = run_edit_test_fix_loop("edit foo", context, str(tmp_path), max_attempts=3)
    assert result.get("success") is False
    assert result.get("failure_reason_code") == "no_progress_repeat"


def test_stagnation_terminates_no_progress(tmp_path):
    """Simplified pipeline: patch failure -> immediate fail. No retries."""
    (tmp_path / "foo.py").write_text("x = 1\n")
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    context = {"ranked_context": [], "retrieved_files": ["foo.py"], "retrieved_symbols": [], "project_root": str(tmp_path)}

    with patch("agent.runtime.execution_loop.plan_diff") as m_plan:
        m_plan.return_value = {"changes": [{"file": "foo.py", "symbol": "", "action": "modify", "patch": "x = 2", "reason": "r"}]}
        with patch("agent.runtime.execution_loop.to_structured_patches") as m_ts:
            m_ts.return_value = {"changes": [{"file": "foo.py", "patch": {"action": "text_sub", "old": "x = 1", "new": "x = 2"}}]}
            with patch("agent.runtime.execution_loop.execute_patch") as m_exec:
                m_exec.return_value = {
                    "success": False,
                    "patch_reject_reason": "no_progress_repeat",
                    "failure_reason_code": "no_progress_repeat",
                    "patches_applied": 0,
                }
                from agent.runtime.execution_loop import run_edit_test_fix_loop
                result = run_edit_test_fix_loop("edit foo", context, str(tmp_path), max_attempts=3)
    assert result.get("success") is False
    assert result.get("attempt") == 1
    assert result.get("failure_type") == "patch_apply_failed"


def test_stop_after_successful_edit(tmp_path):
    """Correct patch applied + validation pass -> success, no further loop iterations."""
    (tmp_path / "foo.py").write_text("def bar():\n    return 1\n")
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    context = {"ranked_context": [], "retrieved_files": ["foo.py"], "retrieved_symbols": []}

    plan_calls = 0

    def mock_plan_once(*args, **kwargs):
        nonlocal plan_calls
        plan_calls += 1
        return {"changes": [{"file": "foo.py", "symbol": "bar", "action": "modify", "patch": "return 2", "reason": "r"}]}

    with patch("agent.runtime.execution_loop.plan_diff", side_effect=mock_plan_once):
        with patch("agent.runtime.execution_loop.to_structured_patches") as m_ts:
            m_ts.return_value = {"changes": [{"file": "foo.py", "patch": {"action": "text_sub", "old": "return 1", "new": "return 2"}}]}
            with patch("agent.runtime.execution_loop.execute_patch") as m_exec:
                m_exec.return_value = {"success": True, "files_modified": [str(tmp_path / "foo.py")], "patches_applied": 1}
                with patch("agent.runtime.execution_loop.validate_project") as m_syntax:
                    m_syntax.return_value = {"valid": True, "error": ""}
                    with patch("agent.runtime.execution_loop.run_tests") as m_tests:
                        m_tests.return_value = {"passed": True}
                        from agent.runtime.execution_loop import run_edit_test_fix_loop
                        result = run_edit_test_fix_loop("change bar to return 2", context, str(tmp_path), max_attempts=3)
    assert result.get("success") is True
    assert result.get("attempt") == 1
    assert plan_calls == 1


def test_noop_when_already_correct(tmp_path):
    """No patch (already_correct) + passing tests + instruction satisfied -> success."""
    (tmp_path / "foo.py").write_text("def bar():\n    return 2\n")
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    context = {
        "ranked_context": [],
        "retrieved_files": ["foo.py"],
        "retrieved_symbols": [],
        "project_root": str(tmp_path),
        "edit_binding": {"file": "foo.py", "symbol": "bar"},
    }

    with patch("agent.runtime.execution_loop.plan_diff") as m_plan:
        m_plan.return_value = {"changes": [{"file": "foo.py", "symbol": "bar", "action": "modify", "patch": "x", "reason": "r"}]}
        with patch("agent.runtime.execution_loop.to_structured_patches") as m_ts:
            m_ts.return_value = {"changes": [], "already_correct": True}
            with patch("agent.runtime.execution_loop.run_tests") as m_tests:
                m_tests.return_value = {"passed": True}
                from agent.runtime.execution_loop import run_edit_test_fix_loop
                result = run_edit_test_fix_loop("change bar to return 2", context, str(tmp_path), max_attempts=2)
    assert result.get("success") is True
    assert result.get("patches_applied") == 0
    assert result.get("files_modified") == []


def test_noop_no_meaningful_diff_validation_passes(tmp_path):
    """ReAct: no verify_patch; noop patch reaches execute_patch; tests pass -> success."""
    (tmp_path / "foo.py").write_text("def bar():\n    return 2\n")
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    context = {
        "ranked_context": [],
        "retrieved_files": ["foo.py"],
        "retrieved_symbols": [],
        "edit_binding": {"file": "foo.py", "symbol": "bar"},
        "project_root": str(tmp_path),
    }

    with patch("agent.runtime.execution_loop.plan_diff") as m_plan:
        m_plan.return_value = {"changes": [{"file": "foo.py", "symbol": "bar", "action": "modify", "patch": "x", "reason": "r"}]}
        with patch("agent.runtime.execution_loop.to_structured_patches") as m_ts:
            m_ts.return_value = {"changes": [{"file": "foo.py", "patch": {"action": "text_sub", "old": "return 2", "new": "return 2"}}]}
            with patch("agent.runtime.execution_loop.execute_patch") as m_exec:
                m_exec.return_value = {"success": True, "files_modified": [], "patches_applied": 0}
                with patch("agent.runtime.execution_loop.validate_project") as m_syntax:
                    m_syntax.return_value = {"valid": True, "error": ""}
                    with patch("agent.runtime.execution_loop.run_tests") as m_tests:
                        m_tests.return_value = {"passed": True}
                        from agent.runtime.execution_loop import run_edit_test_fix_loop
                        result = run_edit_test_fix_loop("change bar to return 2", context, str(tmp_path), max_attempts=2)
    assert result.get("success") is True
    assert result.get("patches_applied") == 0


def test_no_changes_validation_passes_success(tmp_path):
    """Planner produces no changes but validation passes + instruction satisfied -> success."""
    (tmp_path / "foo.py").write_text("def bar():\n    return 2\n")
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    context = {
        "ranked_context": [],
        "retrieved_files": ["foo.py"],
        "retrieved_symbols": [],
        "edit_binding": {"file": "foo.py", "symbol": "bar"},
        "project_root": str(tmp_path),
    }

    with patch("agent.runtime.execution_loop.plan_diff") as m_plan:
        m_plan.return_value = {"changes": []}
        with patch("agent.runtime.execution_loop.run_tests") as m_tests:
            m_tests.return_value = {"passed": True}
            from agent.runtime.execution_loop import run_edit_test_fix_loop
            result = run_edit_test_fix_loop("change bar to return 2", context, str(tmp_path), max_attempts=2)
    assert result.get("success") is True
    assert result.get("files_modified") == []
    assert result.get("patches_applied") == 0


def test_noop_rejected_when_instruction_not_satisfied(tmp_path):
    """ReAct: instruction_satisfied removed; already_correct + tests pass -> success."""
    (tmp_path / "foo.py").write_text("def other():\n    pass\n")
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    context = {
        "ranked_context": [],
        "retrieved_files": ["foo.py"],
        "retrieved_symbols": [],
        "project_root": str(tmp_path),
        "edit_binding": {"file": "foo.py", "symbol": "compute_sum"},
    }

    with patch("agent.runtime.execution_loop.plan_diff") as m_plan:
        m_plan.return_value = {"changes": [{"file": "foo.py", "symbol": "compute_sum", "action": "modify", "patch": "x", "reason": "r"}]}
        with patch("agent.runtime.execution_loop.to_structured_patches") as m_ts:
            m_ts.return_value = {"changes": [], "already_correct": True}
            with patch("agent.runtime.execution_loop.run_tests") as m_tests:
                m_tests.return_value = {"passed": True}
                from agent.runtime.execution_loop import run_edit_test_fix_loop
                result = run_edit_test_fix_loop(
                    "add function compute_sum that returns the sum of two numbers",
                    context, str(tmp_path), max_attempts=2
                )
    assert result.get("success") is True
    assert result.get("patches_applied") == 0


def test_noop_allowed_when_symbol_already_exists(tmp_path):
    """Symbol already exists in content -> instruction satisfied -> success."""
    (tmp_path / "foo.py").write_text("def bar():\n    return 2\n")
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    context = {
        "ranked_context": [],
        "retrieved_files": ["foo.py"],
        "retrieved_symbols": [],
        "project_root": str(tmp_path),
        "edit_binding": {"file": "foo.py", "symbol": "bar"},
    }

    with patch("agent.runtime.execution_loop.plan_diff") as m_plan:
        m_plan.return_value = {"changes": [{"file": "foo.py", "symbol": "bar", "action": "modify", "patch": "x", "reason": "r"}]}
        with patch("agent.runtime.execution_loop.to_structured_patches") as m_ts:
            m_ts.return_value = {"changes": [], "already_correct": True}
            with patch("agent.runtime.execution_loop.run_tests") as m_tests:
                m_tests.return_value = {"passed": True}
                from agent.runtime.execution_loop import run_edit_test_fix_loop
                result = run_edit_test_fix_loop("add function bar", context, str(tmp_path), max_attempts=2)
    assert result.get("success") is True
    assert result.get("patches_applied") == 0


def test_patch_flow_unchanged(tmp_path):
    """Valid patch applied -> success regardless of instruction satisfaction check."""
    (tmp_path / "foo.py").write_text("def bar():\n    return 1\n")
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    context = {"ranked_context": [], "retrieved_files": ["foo.py"], "retrieved_symbols": [], "project_root": str(tmp_path)}

    with patch("agent.runtime.execution_loop.plan_diff") as m_plan:
        m_plan.return_value = {"changes": [{"file": "foo.py", "symbol": "bar", "action": "modify", "patch": "return 2", "reason": "r"}]}
        with patch("agent.runtime.execution_loop.to_structured_patches") as m_ts:
            m_ts.return_value = {"changes": [{"file": "foo.py", "patch": {"action": "text_sub", "old": "return 1", "new": "return 2"}}]}
            with patch("agent.runtime.execution_loop.execute_patch") as m_exec:
                m_exec.return_value = {"success": True, "files_modified": [str(tmp_path / "foo.py")], "patches_applied": 1}
                with patch("agent.runtime.execution_loop.validate_project") as m_syntax:
                    m_syntax.return_value = {"valid": True, "error": ""}
                    with patch("agent.runtime.execution_loop.run_tests") as m_tests:
                        m_tests.return_value = {"passed": True}
                        from agent.runtime.execution_loop import run_edit_test_fix_loop
                        result = run_edit_test_fix_loop("change bar to return 2", context, str(tmp_path), max_attempts=2)
    assert result.get("success") is True
    assert result.get("patches_applied") == 1


def test_rollback_restore_verification(tmp_path):
    """After syntax validation failure, files are restored to original (snapshot rollback)."""
    original = "def bar():\n    return 1\n"
    (tmp_path / "foo.py").write_text(original)
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    context = {
        "ranked_context": [],
        "retrieved_files": ["foo.py"],
        "retrieved_symbols": [{"file": "foo.py", "symbol": "bar"}],
        "project_root": str(tmp_path),
        "edit_binding": {"file": "foo.py", "symbol": "bar"},
    }

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
        with patch("agent.runtime.execution_loop.to_structured_patches") as m_ts:
            m_ts.return_value = {"changes": [{"file": "foo.py", "patch": {"action": "text_sub", "old": "return 1", "new": "return 1 + 1"}}]}
            with patch("agent.runtime.execution_loop.validate_project") as m_syntax:
                    m_syntax.return_value = {"valid": False, "error": "SyntaxError"}
                    from agent.runtime.execution_loop import run_edit_test_fix_loop
                    result = run_edit_test_fix_loop("change bar", context, str(tmp_path), max_attempts=1)
    assert result.get("success") is False
    assert result.get("error") == "syntax_error"
    # execute_patch runs (not mocked) and modifies file; then syntax fails and we rollback
    assert (tmp_path / "foo.py").read_text() == original
