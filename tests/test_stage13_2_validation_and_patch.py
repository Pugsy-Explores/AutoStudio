"""Stage 13.2: validation scope isolation + click multifile text_sub hardening."""

from __future__ import annotations

import os
from pathlib import Path
import pytest

from agent.tools.validation_scope import ENV_INNER_VALIDATION_CMD, resolve_inner_loop_validation
from editing.patch_generator import to_structured_patches
from editing.patch_executor import execute_patch


def test_resolve_inner_loop_validation_uses_env_pytest_cmd(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_INNER_VALIDATION_CMD, raising=False)
    r = resolve_inner_loop_validation(str(tmp_path), {})
    assert r["validation_scope_kind"] == "repo_wide"
    assert r["test_cmd"] is None

    cmd = "PYTHONPATH=. python3 -m pytest benchmark_local/test_x.py -q"
    monkeypatch.setenv(ENV_INNER_VALIDATION_CMD, cmd)
    r2 = resolve_inner_loop_validation(str(tmp_path), {})
    assert r2["test_cmd"] == cmd
    assert r2["validation_scope_kind"] == "benchmark_local"
    assert r2["resolved_validation_cwd"] == str(tmp_path.resolve())


def test_to_structured_patches_injects_part_a_when_only_part_b_planned(fixtures_click_snapshot: Path):
    """Planner may list only dependents; injection still edits benchmark_local/part_a.py."""
    root = fixtures_click_snapshot
    instruction = (
        "Rename the shared suffix from legacy to unified in benchmark_local/part_a.py and any dependent "
        "text so benchmark_local/test_multifile.py passes."
    )
    plan = {
        "changes": [
            {
                "file": str(root / "benchmark_local" / "part_b.py"),
                "symbol": "",
                "action": "modify",
                "patch": "Review for impact: multifile",
                "reason": "dependent",
            }
        ]
    }
    ctx = {"project_root": str(root)}
    out = to_structured_patches(plan, instruction, ctx)
    files = [c.get("file", "").replace("\\", "/") for c in out.get("changes", [])]
    assert any(f.endswith("benchmark_local/part_a.py") for f in files)
    patch0 = out["changes"][0]["patch"]
    assert patch0.get("action") == "text_sub"
    assert "legacy" in patch0.get("old", "")
    assert "unified" in patch0.get("new", "")

    er = execute_patch(out, str(root))
    assert er.get("success") is True
    text = (root / "benchmark_local" / "part_a.py").read_text(encoding="utf-8")
    assert 'SUFFIX = "unified"' in text or "SUFFIX = 'unified'" in text


@pytest.fixture
def fixtures_click_snapshot(tmp_path: Path) -> Path:
    """Copy click_snapshot fixture into tmp_path for isolated patch apply."""
    src = Path(__file__).resolve().parent / "agent_eval" / "fixtures" / "pinned_repos" / "click_snapshot"
    assert src.is_dir(), f"click_snapshot fixture missing: {src}"
    import shutil

    dst = tmp_path / "ws"
    shutil.copytree(src, dst, symlinks=False)
    return dst


def test_text_sub_produces_parseable_python_click_multifile(fixtures_click_snapshot: Path):
    instruction = (
        "Rename the shared suffix from legacy to unified in benchmark_local/part_a.py and any dependent "
        "text so benchmark_local/test_multifile.py passes."
    )
    plan = {"changes": []}
    ctx = {"project_root": str(fixtures_click_snapshot)}
    out = to_structured_patches(plan, instruction, ctx)
    assert out.get("changes")
    er = execute_patch(out, str(fixtures_click_snapshot))
    assert er.get("failure_reason_code") != "invalid_patch_syntax"
    assert er.get("success") is True
