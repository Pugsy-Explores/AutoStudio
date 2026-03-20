"""Stage 13.4: Pinned multi-file edit grounding and target resolution hardening."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from editing.diff_planner import plan_diff, _path_relative_to_root
from editing.patch_generator import to_structured_patches, _inject_click_benchmark_multifile_change
from editing.patch_executor import execute_patch


def test_path_relative_to_root_normalizes_absolute(tmp_path):
    root = tmp_path.resolve()
    (root / "benchmark_local").mkdir()
    abs_path = str(root / "benchmark_local" / "part_a.py")
    rel = _path_relative_to_root(abs_path, str(root))
    assert rel == "benchmark_local/part_a.py"


def test_path_relative_to_root_preserves_relative(tmp_path):
    root = tmp_path.resolve()
    rel = _path_relative_to_root("benchmark_local/part_a.py", str(root))
    assert rel == "benchmark_local/part_a.py"


def test_inject_works_when_project_root_from_env(tmp_path, monkeypatch):
    """Inject works when project_root comes from SERENA_PROJECT_DIR (simulating to_structured_patches fallback)."""
    monkeypatch.setenv("SERENA_PROJECT_DIR", str(tmp_path))
    (tmp_path / "benchmark_local").mkdir()
    (tmp_path / "benchmark_local" / "part_a.py").write_text('SUFFIX = "legacy"\n')
    instruction = (
        "Rename the shared suffix from legacy to unified in benchmark_local/part_a.py and any dependent "
        "text so benchmark_local/test_multifile.py passes."
    )
    result = _inject_click_benchmark_multifile_change(instruction, os.environ.get("SERENA_PROJECT_DIR", ""))
    assert result is not None
    assert result.get("file") == "benchmark_local/part_a.py"
    assert result["patch"].get("action") == "text_sub"


def test_to_structured_patches_uses_project_root_fallback(tmp_path, monkeypatch):
    """to_structured_patches uses env fallback when context.project_root is empty."""
    monkeypatch.setenv("SERENA_PROJECT_DIR", str(tmp_path))
    (tmp_path / "benchmark_local").mkdir()
    (tmp_path / "benchmark_local" / "part_a.py").write_text('SUFFIX = "legacy"\n')
    instruction = (
        "Rename legacy to unified in benchmark_local/part_a.py and multifile test."
    )
    plan = {"changes": [{"file": "benchmark_local/part_a.py", "symbol": "", "action": "modify", "patch": "x"}]}
    ctx = {"project_root": ""}
    out = to_structured_patches(plan, instruction, ctx)
    assert out.get("changes")
    er = execute_patch(out, str(tmp_path))
    assert er.get("success") is True


def test_plan_diff_outputs_relative_paths(tmp_path):
    """plan_diff returns changes with project_root-relative paths."""
    (tmp_path / "benchmark_local").mkdir()
    (tmp_path / "benchmark_local" / "part_a.py").write_text('SUFFIX = "legacy"\n')
    (tmp_path / "benchmark_local" / "test_multifile.py").write_text("assert True\n")
    instruction = (
        "Rename legacy to unified in benchmark_local/part_a.py so test_multifile passes."
    )
    context = {
        "project_root": str(tmp_path),
        "ranked_context": [],
        "retrieved_symbols": [],
        "retrieved_files": [],
    }
    result = plan_diff(instruction, context)
    changes = result.get("changes", [])
    assert changes
    for c in changes:
        fp = c.get("file", "")
        assert not Path(fp).is_absolute(), f"Expected relative path, got {fp!r}"


def test_click_multifile_apply_succeeds_with_relative_paths(fixtures_click_snapshot: Path):
    """Regression: click multifile task applies successfully (was target_not_found)."""
    root = fixtures_click_snapshot
    instruction = (
        "Rename the shared suffix from legacy to unified in benchmark_local/part_a.py and any dependent "
        "text so benchmark_local/test_multifile.py passes."
    )
    plan = {
        "changes": [
            {"file": "benchmark_local/part_b.py", "symbol": "", "action": "modify", "patch": "Review"},
            {"file": "benchmark_local/part_a.py", "symbol": "", "action": "modify", "patch": "Apply"},
        ]
    }
    ctx = {"project_root": str(root)}
    out = to_structured_patches(plan, instruction, ctx)
    er = execute_patch(out, str(root))
    assert er.get("success") is True, f"Expected success, got {er}"
    assert er.get("patch_reject_reason") is None
    text = (root / "benchmark_local" / "part_a.py").read_text(encoding="utf-8")
    assert "unified" in text


@pytest.fixture
def fixtures_click_snapshot(tmp_path: Path) -> Path:
    """Copy click_snapshot fixture for isolated tests."""
    src = Path(__file__).resolve().parent / "agent_eval" / "fixtures" / "pinned_repos" / "click_snapshot"
    assert src.is_dir(), f"click_snapshot fixture missing: {src}"
    import shutil
    dst = tmp_path / "ws"
    shutil.copytree(src, dst, symlinks=False)
    return dst
