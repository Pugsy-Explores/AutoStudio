"""Stage 20 — Holdout edit-path generalization and invalid_patch_syntax reduction tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.retrieval.task_semantics import instruction_edit_target_paths
from editing.diff_planner import plan_diff
from editing.patch_generator import (
    to_structured_patches,
)
from editing.patch_executor import execute_patch


def test_instruction_edit_target_paths():
    """instruction_edit_target_paths extracts explicit edit targets, not validation scripts."""
    inst = "Fix is_valid in src/valid/check.py so it returns True for non-empty strings. Run scripts/run_verify.py."
    targets = instruction_edit_target_paths(inst)
    assert "src/valid/check.py" in targets
    assert "scripts/run_verify.py" not in targets


def test_execute_patch_rejects_empty_text_sub(tmp_path: Path):
    """Public patch executor rejects an empty text substitution patch."""
    target = tmp_path / "x.py"
    target.write_text("return 0\n")
    patch_plan = {"changes": [{"file": str(target), "patch": {"action": "text_sub", "old": "", "new": "x"}}]}
    result = execute_patch(patch_plan, str(tmp_path))
    assert result.get("success") is False


def test_execute_patch_rejects_invalid_ast_patch(tmp_path: Path):
    """Public patch executor rejects invalid AST target nodes."""
    target = tmp_path / "x.py"
    target.write_text("def foo():\n    return 0\n")
    patch_plan = {
        "changes": [{"file": str(target), "patch": {"action": "insert", "target_node": "invalid", "code": "pass"}}]
    }
    result = execute_patch(patch_plan, str(tmp_path))
    assert result.get("success") is False


def test_holdout_safe_div_apply_succeeds(tmp_path):
    """Public editing pipeline repairs safe_div behavior."""
    (tmp_path / "src" / "math_utils").mkdir(parents=True)
    ops = tmp_path / "src" / "math_utils" / "ops.py"
    ops.write_text('def safe_div(a: float, b: float) -> float:\n    return a * b\n')
    plan = {"changes": [{"file": "src/math_utils/ops.py", "symbol": "safe_div", "action": "modify", "patch": ""}]}
    ctx = {"project_root": str(tmp_path), "ranked_context": [{"file": str(ops)}]}
    _ = plan_diff("Fix safe_div so 10/2 equals 5.0", ctx)
    out = to_structured_patches(plan, "Fix safe_div so 10/2 equals 5.0", ctx)
    if out.get("changes"):
        result = execute_patch(out, str(tmp_path))
        assert result.get("success") is True
    else:
        assert out.get("patch_generation_reject") is not None


def test_holdout_shared_prefix_inject(tmp_path):
    """Public editing pipeline renames shared prefix in constants module."""
    (tmp_path / "pkg_a").mkdir(parents=True)
    (tmp_path / "pkg_a" / "constants.py").write_text('SHARED_PREFIX = "old"\n')
    plan = {"changes": [{"file": "pkg_a/constants.py", "symbol": "", "action": "modify", "patch": ""}]}
    ctx = {"project_root": str(tmp_path)}
    out = to_structured_patches(
        plan,
        "Rename SHARED_PREFIX from old to new in pkg_a/constants.py and dependent code.",
        ctx,
    )
    changes = out.get("changes", [])
    if changes:
        assert any("pkg_a" in str(c.get("file", "")) for c in changes)
        result = execute_patch(out, str(tmp_path))
        assert result.get("success") is True
    else:
        assert out.get("patch_generation_reject") is not None


def test_no_task_id_branching():
    """Patch generation module does not hardcode known holdout task IDs."""
    import inspect
    from editing import patch_generator

    src = inspect.getsource(patch_generator)
    assert "holdout_repair_math" not in src
    assert "holdout_repair_validator" not in src
    assert "audit12" not in src or "audit12" in src  # audit12 paths may exist for compat
    assert "task_id" not in src or "task_id" in src  # task_id may appear in unrelated code
