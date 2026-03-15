"""Tests for multi-file editing pipeline.

Verifies the editing pipeline handles:
- Two-file patches (config + executor)
- Three-file patches
- ast.parse correctness on each modified file
- Rollback when one file fails (conflict/failure detection)
"""

import ast
from pathlib import Path

import pytest

from editing.patch_executor import execute_patch


def test_two_file_edit_config_and_executor(tmp_path):
    """Two-file edit: add constant to config, use it in executor."""
    config_py = tmp_path / "config.py"
    executor_py = tmp_path / "executor.py"

    config_py.write_text(
        """# Config module
def get_limit():
    return 10
"""
    )
    executor_py.write_text(
        """# Executor module
def run():
    limit = 5
    return limit
"""
    )

    patch_plan = {
        "changes": [
            {
                "file": str(config_py),
                "patch": {
                    "symbol": "get_limit",
                    "action": "replace",
                    "target_node": "function_body",
                    "code": "RETRY_LIMIT = 3\nreturn RETRY_LIMIT",
                },
            },
            {
                "file": str(executor_py),
                "patch": {
                    "symbol": "run",
                    "action": "replace",
                    "target_node": "function_body",
                    "code": "from config import get_limit\nlimit = get_limit()\nreturn limit",
                },
            },
        ],
    }

    result = execute_patch(patch_plan, project_root=str(tmp_path))
    assert result["success"] is True
    assert result["patches_applied"] == 2
    assert len(result["files_modified"]) == 2
    assert str(config_py) in result["files_modified"]
    assert str(executor_py) in result["files_modified"]

    # Verify ast.parse on each modified file
    for path in result["files_modified"]:
        content = Path(path).read_text()
        ast.parse(content)

    assert "RETRY_LIMIT" in config_py.read_text()
    assert "get_limit" in executor_py.read_text()


def test_three_file_edit(tmp_path):
    """Three-file edit: patch config, module_a, and module_b."""
    config_py = tmp_path / "config.py"
    module_a_py = tmp_path / "module_a.py"
    module_b_py = tmp_path / "module_b.py"

    config_py.write_text(
        """def get_config():
    return 1
"""
    )
    module_a_py.write_text(
        """def step_a():
    return 1
"""
    )
    module_b_py.write_text(
        """def step_b():
    return 2
"""
    )
    patch_plan = {
        "changes": [
            {
                "file": str(config_py),
                "patch": {
                    "symbol": "get_config",
                    "action": "replace",
                    "target_node": "function_body",
                    "code": "EXTRA = 1\nreturn EXTRA",
                },
            },
            {
                "file": str(module_a_py),
                "patch": {
                    "symbol": "step_a",
                    "action": "replace",
                    "target_node": "function_body",
                    "code": "x = 3\nreturn 1",
                },
            },
            {
                "file": str(module_b_py),
                "patch": {
                    "symbol": "step_b",
                    "action": "replace",
                    "target_node": "function_body",
                    "code": "y = 4\nreturn 2",
                },
            },
        ],
    }

    result = execute_patch(patch_plan, project_root=str(tmp_path))
    assert result["success"] is True
    assert result["patches_applied"] == 3
    assert len(result["files_modified"]) == 3

    for path in result["files_modified"]:
        content = Path(path).read_text()
        ast.parse(content)


def test_multifile_ast_parse_correctness(tmp_path):
    """Each modified file must parse correctly after patch."""
    a_py = tmp_path / "a.py"
    b_py = tmp_path / "b.py"
    a_py.write_text("def f():\n    return 1\n")
    b_py.write_text("def g():\n    return 2\n")

    patch_plan = {
        "changes": [
            {
                "file": str(a_py),
                "patch": {
                    "symbol": "f",
                    "action": "replace",
                    "target_node": "function_body",
                    "code": "return 10",
                },
            },
            {
                "file": str(b_py),
                "patch": {
                    "symbol": "g",
                    "action": "replace",
                    "target_node": "function_body",
                    "code": "return 20",
                },
            },
        ],
    }

    result = execute_patch(patch_plan, project_root=str(tmp_path))
    assert result["success"] is True

    for path in result["files_modified"]:
        tree = ast.parse(Path(path).read_text())
        assert tree is not None


def test_multifile_rollback_on_failure(tmp_path):
    """When one patch fails (invalid syntax), all files are rolled back."""
    a_py = tmp_path / "a.py"
    b_py = tmp_path / "b.py"
    original_a = "def f():\n    return 1\n"
    original_b = "def g():\n    return 2\n"
    a_py.write_text(original_a)
    b_py.write_text(original_b)

    # First change is valid, second introduces syntax error
    patch_plan = {
        "changes": [
            {
                "file": str(a_py),
                "patch": {
                    "symbol": "f",
                    "action": "replace",
                    "target_node": "function_body",
                    "code": "return 10",
                },
            },
            {
                "file": str(b_py),
                "patch": {
                    "symbol": "g",
                    "action": "replace",
                    "target_node": "function_body",
                    "code": "x = 1 2 3",
                },
            },
        ],
    }

    result = execute_patch(patch_plan, project_root=str(tmp_path))
    assert result["success"] is False
    assert "patch_failed" in result.get("error", "")

    # Rollback: both files must be unchanged
    assert a_py.read_text() == original_a
    assert b_py.read_text() == original_b
