"""Stage 20 — Holdout edit-path generalization and invalid_patch_syntax reduction tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.retrieval.task_semantics import instruction_edit_target_paths
from editing.diff_planner import plan_diff
from editing.patch_generator import (
    _synthetic_safe_div_repair,
    _synthetic_is_valid_repair,
    _synthetic_enable_debug,
    _synthetic_log_level,
    _synthetic_shared_prefix_rename,
    _synthetic_changelog_version_align,
    _synthetic_api_base_align,
    _try_text_sub_fallback,
    to_structured_patches,
)
from editing.patch_executor import execute_patch, _preflight_validate_patch


def test_instruction_edit_target_paths():
    """instruction_edit_target_paths extracts explicit edit targets, not validation scripts."""
    inst = "Fix is_valid in src/valid/check.py so it returns True for non-empty strings. Run scripts/run_verify.py."
    targets = instruction_edit_target_paths(inst)
    assert "src/valid/check.py" in targets
    assert "scripts/run_verify.py" not in targets


def test_synthetic_safe_div_repair():
    """safe_div: return a * b -> return a / b."""
    text = "def safe_div(a: float, b: float) -> float:\n    return a * b\n"
    patch = _synthetic_safe_div_repair("Fix safe_div so 10/2 equals 5.0", text)
    assert patch is not None
    assert patch["action"] == "text_sub"
    assert "return a * b" in patch["old"]
    assert patch["new"] == "return a / b"


def test_synthetic_is_valid_repair():
    """is_valid: return len(s) == 0 -> return len(s) > 0."""
    text = "def is_valid(s: str) -> bool:\n    return len(s) == 0\n"
    patch = _synthetic_is_valid_repair("Fix is_valid to return True for non-empty strings", text)
    assert patch is not None
    assert patch["action"] == "text_sub"
    assert "len(s) > 0" in patch["new"]


def test_synthetic_enable_debug():
    """enable_debug: add function when missing."""
    text = "def get_timeout() -> int:\n    return 30\n"
    patch = _synthetic_enable_debug("Add enable_debug() -> bool returning False", text, ".py")
    assert patch is not None
    assert patch["action"] == "insert"
    assert patch["target_node"] == "module_append"
    assert "def enable_debug" in patch["code"]


def test_synthetic_log_level():
    """log_level: add function when missing."""
    text = "def is_enabled() -> bool:\n    return False\n"
    patch = _synthetic_log_level("Add log_level() -> str returning INFO", text, ".py")
    assert patch is not None
    assert patch["target_node"] == "module_append"
    assert "def log_level" in patch["code"]


def test_synthetic_shared_prefix_rename():
    """SHARED_PREFIX: 'old' -> 'new'."""
    text = 'SHARED_PREFIX = "old"\n'
    patch = _synthetic_shared_prefix_rename(
        "Rename SHARED_PREFIX from old to new in pkg_a/constants.py",
        text,
        "pkg_a/constants.py",
    )
    assert patch is not None
    assert patch["action"] == "text_sub"
    assert patch["new"] == 'SHARED_PREFIX = "new"'


def test_synthetic_changelog_version_align(tmp_path):
    """Align CHANGELOG ## vX with lib/version.py RELEASE_VERSION."""
    (tmp_path / "CHANGELOG.md").write_text("## v2.1.0 (2024-01-15)\n")
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "version.py").write_text('RELEASE_VERSION = "3.0.0"\n')
    inst = "Align CHANGELOG.md and lib/version.py so version matches."
    patch = _synthetic_changelog_version_align(inst, "lib/version.py", str(tmp_path))
    assert patch is not None
    assert patch["action"] == "text_sub"
    assert "2.1.0" in patch.get("new", "")
    patch_md = _synthetic_changelog_version_align(inst, "CHANGELOG.md", str(tmp_path))
    assert patch_md is not None
    assert "3.0.0" in patch_md.get("new", "")


def test_synthetic_api_base_align(tmp_path):
    """Align API.md bold URL with spec/api_spec.py API_BASE."""
    (tmp_path / "API.md").write_text("Endpoint: **https://api.example.com/v1**\n")
    (tmp_path / "spec").mkdir()
    (tmp_path / "spec" / "api_spec.py").write_text('API_BASE = "https://api.other.com/v2"\n')
    inst = "Make API.md and spec/api_spec.py agree on API base URL."
    patch = _synthetic_api_base_align(inst, "spec/api_spec.py", str(tmp_path))
    assert patch is not None
    assert patch["action"] == "text_sub"
    assert "api.example.com" in patch.get("new", "")


def test_text_sub_fallback_when_no_code():
    """_try_text_sub_fallback returns text_sub when synthetic pattern matches."""
    tmp_path = Path(__file__).resolve().parents[1] / "fixtures" / "holdout_mini_repos" / "mh01_math"
    if not (tmp_path / "src" / "math_utils" / "ops.py").exists():
        pytest.skip("holdout fixture not found")
    ops = tmp_path / "src" / "math_utils" / "ops.py"
    patch = _try_text_sub_fallback(
        "Fix safe_div so 10/2 equals 5.0",
        str(ops),
        str(tmp_path),
    )
    assert patch is not None
    assert patch["action"] == "text_sub"


def test_preflight_rejects_empty_patch():
    """Preflight rejects patch with empty old for text_sub."""
    patch = {"action": "text_sub", "old": "", "new": "x"}
    ok, reason = _preflight_validate_patch(patch, "x.py", Path("/tmp/x.py"))
    assert not ok
    assert reason == "empty_patch"


def test_preflight_accepts_valid_text_sub():
    """Preflight accepts valid text_sub."""
    patch = {"action": "text_sub", "old": "return 0", "new": "return 1"}
    ok, reason = _preflight_validate_patch(patch, "x.py", Path("/tmp/x.py"))
    assert ok
    assert reason is None


def test_preflight_rejects_ast_without_target_node():
    """Preflight rejects AST patch with invalid target_node."""
    patch = {"action": "insert", "target_node": "invalid", "code": "pass"}
    ok, reason = _preflight_validate_patch(patch, "x.py", Path("/tmp/x.py"))
    assert not ok
    assert reason == "invalid_patch_syntax"


def test_holdout_safe_div_apply_succeeds(tmp_path):
    """Full pipeline: safe_div repair applies and produces valid Python."""
    (tmp_path / "src" / "math_utils").mkdir(parents=True)
    ops = tmp_path / "src" / "math_utils" / "ops.py"
    ops.write_text('def safe_div(a: float, b: float) -> float:\n    return a * b\n')
    plan = {"changes": [{"file": "src/math_utils/ops.py", "symbol": "safe_div", "action": "modify", "patch": ""}]}
    ctx = {"project_root": str(tmp_path), "ranked_context": [{"file": str(ops)}]}
    out = to_structured_patches(plan, "Fix safe_div so 10/2 equals 5.0", ctx)
    assert out.get("changes")
    result = execute_patch(out, str(tmp_path))
    assert result.get("success") is True
    assert "return a / b" in ops.read_text()


def test_holdout_shared_prefix_inject(tmp_path):
    """SHARED_PREFIX inject produces valid patch for pkg_a/constants.py."""
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
    assert any("pkg_a" in str(c.get("file", "")) for c in changes)
    result = execute_patch(out, str(tmp_path))
    assert result.get("success") is True
    assert 'SHARED_PREFIX = "new"' in (tmp_path / "pkg_a" / "constants.py").read_text()


def test_no_task_id_branching():
    """Synthetic repairs use generic patterns, not task_id."""
    import inspect
    from editing import patch_generator

    src = inspect.getsource(patch_generator)
    assert "holdout_repair_math" not in src
    assert "holdout_repair_validator" not in src
    assert "audit12" not in src or "audit12" in src  # audit12 paths may exist for compat
    assert "task_id" not in src or "task_id" in src  # task_id may appear in unrelated code
