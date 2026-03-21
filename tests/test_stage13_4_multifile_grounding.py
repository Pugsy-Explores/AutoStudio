"""Stage 13.4: Pinned multi-file edit grounding and target resolution hardening."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from editing.diff_planner import plan_diff, _path_relative_to_root
from editing.patch_generator import to_structured_patches
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


def test_to_structured_patches_uses_project_root_fallback(tmp_path, monkeypatch):
    """to_structured_patches uses env fallback when context.project_root is empty."""
    monkeypatch.setenv("SERENA_PROJECT_DIR", str(tmp_path))
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "ops.py").write_text("def divide(a, b):\n    return a * b\n")
    instruction = "Change divide to return a/b in src/ops.py."
    plan = {"changes": [{"file": "src/ops.py", "symbol": "divide", "action": "modify", "patch": "x"}]}
    ctx = {"project_root": ""}
    out = to_structured_patches(plan, instruction, ctx)
    assert out.get("changes"), "Generic multiply-to-div repair should produce a patch"
    er = execute_patch(out, str(tmp_path))
    assert er.get("success") is True
    assert "a / b" in (tmp_path / "src" / "ops.py").read_text()


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


def test_click_multifile_no_benchmark_inject(fixtures_click_snapshot: Path):
    """Stage 30: legacy->unified multifile task no longer uses benchmark-specific inject.
    Without the inject, patch generation relies on grounded generation; with vague plan
    patch text, we get rejection. Documents honest benchmark impact."""
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
    # Benchmark inject removed in Stage 30; grounded generation may reject vague patches
    if not out.get("changes"):
        assert out.get("patch_generation_reject") == "weakly_grounded_patch"
        assert out.get("generation_rejected_reason") == "no_grounded_candidate_found"
    else:
        er = execute_patch(out, str(root))
        assert er.get("success") is True


@pytest.fixture
def fixtures_click_snapshot(tmp_path: Path) -> Path:
    """Copy click_snapshot fixture for isolated tests."""
    src = Path(__file__).resolve().parent / "agent_eval" / "fixtures" / "pinned_repos" / "click_snapshot"
    assert src.is_dir(), f"click_snapshot fixture missing: {src}"
    import shutil
    dst = tmp_path / "ws"
    shutil.copytree(src, dst, symlinks=False)
    return dst
