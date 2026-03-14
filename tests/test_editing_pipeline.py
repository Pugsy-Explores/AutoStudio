"""Integration tests for the full editing pipeline.

Covers: diff_planner -> patch_generator -> ast_patcher -> patch_validator -> patch_executor.
Validates: code compiles, AST reparse succeeds, file changes applied, index updates.
Also tests step_dispatcher EDIT flow when ENABLE_DIFF_PLANNER=1.
"""

import os
import shutil
from pathlib import Path

import pytest

from editing.ast_patcher import apply_patch, generate_code, load_ast
from editing.diff_planner import plan_diff
from editing.patch_executor import execute_patch
from editing.patch_generator import to_structured_patches
from editing.patch_validator import validate_patch

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "repo"


def test_pipeline_code_compiles_after_patch(tmp_path):
    """After patch: code compiles."""
    f = tmp_path / "mod.py"
    f.write_text("def foo():\n    return 1\n")
    loaded = load_ast(str(f))
    assert loaded is not None
    tree, source_bytes = loaded
    patch = {
        "symbol": "foo",
        "action": "replace",
        "target_node": "function_body",
        "code": "return 42",
    }
    new_bytes = apply_patch(tree, source_bytes, patch)
    new_code = generate_code(tree, new_bytes)
    compile(new_code, "mod.py", "exec")


def test_pipeline_ast_reparse_succeeds(tmp_path):
    """After patch: AST reparse succeeds via validate_patch."""
    f = tmp_path / "mod.py"
    f.write_text("def bar():\n    x = 1\n    return x\n")
    loaded = load_ast(str(f))
    assert loaded is not None
    tree, source_bytes = loaded
    patch = {
        "symbol": "bar",
        "action": "insert",
        "target_node": "function_body_start",
        "code": "logger.info('bar')",
    }
    new_bytes = apply_patch(tree, source_bytes, patch)
    new_code = generate_code(tree, new_bytes)
    result = validate_patch(str(f), new_code)
    assert result["valid"] is True


def test_pipeline_file_changes_applied(tmp_path):
    """execute_patch applies file changes correctly."""
    f = tmp_path / "mod.py"
    f.write_text("def compute():\n    return 0\n")
    patch_plan = {
        "changes": [
            {
                "file": str(f),
                "patch": {
                    "symbol": "compute",
                    "action": "replace",
                    "target_node": "function_body",
                    "code": "return 100",
                },
            },
        ],
    }
    result = execute_patch(patch_plan, project_root=str(tmp_path))
    assert result["success"] is True
    assert result["patches_applied"] == 1
    assert result["files_modified"]
    content = f.read_text()
    assert "return 100" in content
    assert "return 0" not in content


def test_pipeline_index_updates_correctly(tmp_path):
    """After patch: update_index_for_file reflects changes."""
    from repo_index.indexer import index_repo, update_index_for_file

    work_dir = tmp_path / "repo"
    work_dir.mkdir()
    for p in FIXTURES_DIR.rglob("*.py"):
        rel = p.relative_to(FIXTURES_DIR)
        dst = work_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(p, dst)

    index_dir = work_dir / ".symbol_graph"
    index_repo(str(work_dir), output_dir=str(index_dir))
    foo_path = work_dir / "foo.py"
    original = foo_path.read_text()

    # Apply patch via execute_patch
    patch_plan = {
        "changes": [
            {
                "file": str(foo_path),
                "patch": {
                    "symbol": "bar",
                    "action": "insert",
                    "target_node": "function_body_start",
                    "code": "added = True  # marker",
                },
            },
        ],
    }
    result = execute_patch(patch_plan, project_root=str(work_dir))
    assert result["success"] is True

    # Update index
    count = update_index_for_file(str(foo_path), root_dir=str(work_dir))
    assert count >= 1

    # Verify file content
    content = foo_path.read_text()
    assert "added = True" in content
    assert "def bar" in content


def test_pipeline_diff_planner_to_executor(tmp_path):
    """Full flow: plan_diff -> to_structured_patches -> execute_patch."""
    f = tmp_path / "executor.py"
    f.write_text("def execute_step():\n    return True\n")
    instruction = "Add logging to execute_step"
    context = {
        "ranked_context": [{"file": str(f), "symbol": "execute_step", "snippet": "def execute_step..."}],
        "retrieved_symbols": [{"file": str(f), "symbol": "execute_step"}],
        "retrieved_files": [str(f)],
        "project_root": str(tmp_path),
    }
    plan = plan_diff(instruction, context)
    assert plan.get("changes")

    # Manually provide code-like patch for structured conversion (insert at body start)
    plan["changes"][0]["patch"] = "import logging\nlogger = logging.getLogger(__name__)\nlogger.info('step')"
    patch_plan = to_structured_patches(plan, instruction, context)

    result = execute_patch(patch_plan, project_root=str(tmp_path))
    assert result["success"] is True
    content = f.read_text()
    assert "logger" in content or "logging" in content


def test_step_dispatcher_edit_flow_with_diff_planner(tmp_path):
    """step_dispatcher EDIT uses plan_diff -> execute_patch -> update_index when ENABLE_DIFF_PLANNER=1."""
    from agent.execution.step_dispatcher import dispatch
    from agent.memory.state import AgentState

    f = tmp_path / "target.py"
    f.write_text("def target_fn():\n    return 1\n")
    # Ensure diff planner is enabled
    os.environ["ENABLE_DIFF_PLANNER"] = "1"
    try:
        state = AgentState(instruction="Add logging to target_fn", current_plan={"steps": []})
        state.context = {
            "project_root": str(tmp_path),
            "ranked_context": [{"file": str(f), "symbol": "target_fn", "snippet": "def target_fn..."}],
            "retrieved_symbols": [{"file": str(f), "symbol": "target_fn"}],
            "retrieved_files": [str(f)],
        }
        step = {"id": 1, "action": "EDIT", "description": "Add logging to target_fn"}
        result = dispatch(step, state)
        assert result.get("success") is True
        out = result.get("output", {})
        if out.get("files_modified"):
            content = f.read_text()
            assert "target_fn" in content
    finally:
        os.environ.pop("ENABLE_DIFF_PLANNER", None)
