"""Stage 28 — External-transfer grounding hardening regression tests.

Generic behavior tests. No task-id or suite-specific logic.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.retrieval.target_resolution import (
    is_validation_script_path,
    resolve_module_descriptor_to_files,
    rank_edit_targets,
)
from editing.grounded_patch_generator import (
    generate_grounded_candidates,
    select_best_candidate,
    _try_fix_return_value,
    _try_return_binary_op_repair,
    _find_md_version_any_format,
)


# ---------------------------------------------------------------------------
# Validator/test-path deprioritization
# ---------------------------------------------------------------------------


def test_validator_path_deprioritization():
    """test_*.py and *_test.py are validation paths."""
    assert is_validation_script_path("benchmark_local/test_bench_math.py") is True
    assert is_validation_script_path("tests/test_foo.py") is True
    assert is_validation_script_path("foo_test.py") is True
    assert is_validation_script_path("scripts/check_version_sync.py") is True
    assert is_validation_script_path("scripts/assert_guard.py") is True


def test_source_path_not_validation():
    """bench_math.py, arithmetic.py, version_meta.py are not validation."""
    assert is_validation_script_path("benchmark_local/bench_math.py") is False
    assert is_validation_script_path("benchmark_local/arithmetic.py") is False
    assert is_validation_script_path("benchmark_local/version_meta.py") is False


# ---------------------------------------------------------------------------
# Module-descriptor resolution
# ---------------------------------------------------------------------------


def test_module_descriptor_explicit_path_hint(tmp_path):
    """Explicit path literals (lib/version.py, README.md) resolve via path hint extraction."""
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "version.py").write_text('RELEASE_VERSION = "1.0.0"')
    out = resolve_module_descriptor_to_files(
        "Align README.md and lib/version.py so version matches RELEASE_VERSION",
        str(tmp_path),
    )
    paths = [p for p, _ in out]
    assert any("version.py" in p or "lib" in p for p in paths)


# ---------------------------------------------------------------------------
# Existing-function return-value repair
# ---------------------------------------------------------------------------


def test_fix_return_value_get_timeout():
    """Fix get_timeout() so it returns 30."""
    src = '''def get_timeout() -> int:
    """Return default timeout."""
    return 0  # intentional: should return 30
'''
    c = _try_fix_return_value("Fix get_timeout() in bench_requests_meta.py so it returns 30.", src)
    assert c is not None
    assert c.strategy == "fix_return_value"
    assert c.patch["new"] == "    return 30  # intentional: should return 30"


def test_fix_return_value_via_generate():
    """generate_grounded_candidates produces fix_return_value for get_timeout."""
    src = '''def get_timeout() -> int:
    return 0
'''
    inst = "Fix get_timeout() in benchmark_local/bench_requests_meta.py so it returns 30."
    candidates = generate_grounded_candidates(inst, "bench_requests_meta.py", src, "/tmp")
    best = select_best_candidate(candidates, inst)
    assert best is not None
    assert best.strategy == "fix_return_value"
    assert "return 30" in str(best.patch.get("new", ""))


# ---------------------------------------------------------------------------
# add_ints-style operator repair
# ---------------------------------------------------------------------------


def test_return_binary_op_repair_add_ints():
    """add_ints(2,3) equals 5 -> return a + b not a * b."""
    src = "def add_ints(a: int, b: int) -> int:\n    return a * b\n"
    c = _try_return_binary_op_repair(
        "Fix add_ints() in arithmetic.py so that add_ints(2, 3) equals 5.",
        src,
    )
    assert c is not None
    assert c.strategy == "return_binary_op_repair"
    assert "a + b" in c.patch["new"]


def test_add_ints_via_generate():
    """generate_grounded_candidates produces return_binary_op_repair for add_ints."""
    src = "def add_ints(a: int, b: int) -> int:\n    return a * b\n"
    inst = "Fix add_ints() in benchmark_local/arithmetic.py so that add_ints(2, 3) equals 5."
    candidates = generate_grounded_candidates(inst, "arithmetic.py", src, "/tmp")
    best = select_best_candidate(candidates, inst)
    assert best is not None
    assert best.strategy == "return_binary_op_repair"
    assert "a + b" in str(best.patch.get("new", ""))


# ---------------------------------------------------------------------------
# Version extraction from bold/plain/labeled markdown
# ---------------------------------------------------------------------------


def test_find_md_version_bold_format(tmp_path):
    """**X.Y.Z** format is extracted from README.md (generic)."""
    (tmp_path / "README.md").write_text(
        "# Version\n\nCurrent release: **2.0.0**\n"
    )
    ver = _find_md_version_any_format(
        str(tmp_path),
        "lib/version.py",
        "Align README.md and lib/version.py",
    )
    assert ver == "2.0.0"


def test_find_md_version_changelog(tmp_path):
    """CHANGELOG.md **0.5.0** is extracted (generic)."""
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\nThe CLI version is **0.5.0**.\n"
    )
    ver = _find_md_version_any_format(
        str(tmp_path),
        "lib/version.py",
        "Align CHANGELOG.md and lib/version.py",
    )
    assert ver == "0.5.0"


# ---------------------------------------------------------------------------
# Docs/code alignment with version_constant_align
# ---------------------------------------------------------------------------


def test_version_constant_align_bold_format(tmp_path):
    """version_constant_align edits .py to match **X.Y.Z** in README.md (generic)."""
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "version.py").write_text('RELEASE_VERSION = "1.0.0"')
    (tmp_path / "README.md").write_text(
        "# Version\n\nCurrent: **2.0.0**\n"
    )
    from editing.grounded_patch_generator import _try_version_constant_align

    content = (tmp_path / "lib" / "version.py").read_text()
    c = _try_version_constant_align(
        "Align README.md and lib/version.py so version matches RELEASE_VERSION",
        "lib/version.py",
        content,
        str(tmp_path),
    )
    assert c is not None
    assert "2.0.0" in c.patch["new"]
    assert "1.0.0" in c.patch["old"]


# ---------------------------------------------------------------------------
# Ranking: source over validator
# ---------------------------------------------------------------------------


def test_rank_prefers_source_over_validator(tmp_path):
    """When both bench_math.py and test_bench_math.py exist, prefer bench_math."""
    (tmp_path / "benchmark_local").mkdir()
    (tmp_path / "benchmark_local" / "bench_math.py").write_text("def halve(n): return n")
    (tmp_path / "benchmark_local" / "test_bench_math.py").write_text("def test_halve(): pass")
    ranked = rank_edit_targets(
        "Fix halve() in benchmark_local/bench_math.py so halve(4) equals 2",
        str(tmp_path),
        None,
        ["benchmark_local/bench_math.py"],
        ["benchmark_local/bench_math.py", "benchmark_local/test_bench_math.py"],
        {},
    )
    assert ranked
    top_path, top_penalty, _ = ranked[0]
    assert "bench_math.py" in top_path and "test_" not in top_path
    assert top_penalty < 80


# ---------------------------------------------------------------------------
# No task-id branching
# ---------------------------------------------------------------------------


def test_no_ext_task_id_in_grounded_generator():
    """grounded_patch_generator must not branch on ext_* task_ids."""
    import inspect
    from editing import grounded_patch_generator as gpg

    src = inspect.getsource(gpg)
    ext_ids = ["ext_repair_typer_halve", "ext_repair_click_add", "ext_docs_requests_version"]
    for tid in ext_ids:
        assert tid not in src, f"grounded_patch_generator must not branch on {tid}"
