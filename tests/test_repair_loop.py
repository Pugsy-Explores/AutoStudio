"""Tests for editing/test_repair_loop."""

import os
from pathlib import Path

import pytest

from editing.test_repair_loop import run_with_repair


def test_run_with_repair_patch_only(tmp_path, monkeypatch):
    """run_with_repair applies patch when TEST_REPAIR_ENABLED=0."""
    monkeypatch.setenv("TEST_REPAIR_ENABLED", "0")
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
    context = {"instruction": "Add x", "project_root": str(tmp_path), "ranked_context": []}

    result = run_with_repair(patch_plan, str(tmp_path), context, max_attempts=2)

    assert result["success"] is True
    assert "files_modified" in result
    assert "x = 1  # inserted" in foo_dst.read_text()


def test_run_with_repair_patch_failure(tmp_path, monkeypatch):
    """run_with_repair returns failure when patch fails."""
    monkeypatch.setenv("TEST_REPAIR_ENABLED", "0")
    patch_plan = {
        "changes": [
            {
                "file": str(tmp_path / "nonexistent.py"),
                "patch": {"symbol": "x", "action": "insert", "target_node": "function_body_start", "code": "pass"},
            },
        ],
    }
    context = {"instruction": "Add", "project_root": str(tmp_path), "ranked_context": []}

    result = run_with_repair(patch_plan, str(tmp_path), context, max_attempts=1)

    assert result["success"] is False
    assert "error" in result
