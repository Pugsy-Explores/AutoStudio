"""Tests for editing/patch_executor."""

import shutil
from pathlib import Path

import pytest

from editing.patch_executor import execute_patch

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "repo"


def test_execute_patch_apply_valid_patch(tmp_path):
    """execute_patch applies valid patch and writes file."""
    # Use simple function without docstring to avoid indent edge cases
    foo_dst = tmp_path / "foo.py"
    foo_dst.write_text("def bar():\n    return 1\n\ndef baz():\n    bar()\n    return 2\n")

    patch_plan = {
        "changes": [
            {
                "file": str(foo_dst),
                "patch": {
                    "symbol": "bar",
                    "action": "insert",
                    "target_node": "function_body_start",
                    "code": "x = 1  # inserted",
                },
            },
        ],
    }
    result = execute_patch(patch_plan, project_root=str(tmp_path))
    assert result["success"] is True
    assert len(result["files_modified"]) == 1
    assert result["patches_applied"] == 1
    content = foo_dst.read_text()
    assert "x = 1  # inserted" in content
    assert "def bar" in content


def test_execute_patch_rollback_on_invalid(tmp_path):
    """execute_patch rolls back when patch produces invalid code."""
    foo_src = FIXTURES_DIR / "foo.py"
    foo_dst = tmp_path / "foo.py"
    shutil.copy(foo_src, foo_dst)
    original = foo_dst.read_text()

    patch_plan = {
        "changes": [
            {
                "file": str(foo_dst),
                "patch": {
                    "symbol": "bar",
                    "action": "replace",
                    "target_node": "function_body",
                    "code": "return (  # unclosed paren",
                },
            },
        ],
    }
    result = execute_patch(patch_plan, project_root=str(tmp_path))
    assert result["success"] is False
    assert result.get("error") == "patch_failed"
    assert "file" in result
    assert foo_dst.read_text() == original


def test_execute_patch_empty_changes_succeeds():
    """execute_patch with empty changes returns success."""
    result = execute_patch({"changes": []})
    assert result["success"] is True
    assert result["files_modified"] == []
    assert result["patches_applied"] == 0


def test_execute_patch_rename_variable(tmp_path):
    """execute_patch applies variable rename."""
    f = tmp_path / "foo.py"
    f.write_text("def bar():\n    x = 1\n    return x\n")
    patch_plan = {
        "changes": [
            {
                "file": str(f),
                "patch": {
                    "symbol": "bar",
                    "action": "rename",
                    "old_name": "x",
                    "new_name": "value",
                },
            },
        ],
    }
    result = execute_patch(patch_plan, project_root=str(tmp_path))
    assert result["success"] is True
    content = f.read_text()
    assert "value = 1" in content
    assert "return value" in content
    assert "x = 1" not in content


def test_execute_patch_safeguard_max_files(tmp_path):
    """execute_patch aborts when exceeding max files."""
    foo = tmp_path / "foo.py"
    foo.write_text("def x(): pass\n")
    changes = [
        {"file": str(tmp_path / f"f{i}.py"), "patch": {"symbol": "x", "action": "insert", "target_node": "function_body_start", "code": "pass"}}
        for i in range(6)
    ]
    for i, c in enumerate(changes):
        (tmp_path / f"f{i}.py").write_text("def x(): pass\n")
    patch_plan = {"changes": changes}
    result = execute_patch(patch_plan, project_root=str(tmp_path))
    assert result["success"] is False
    assert result.get("error") == "safeguard_exceeded"
