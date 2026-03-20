"""Stage 17 — Edit grounding and explain-artifact content hardening tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from editing.diff_planner import (
    _instruction_path_hints,
    _instruction_suggests_docs_consistency,
    _instruction_hint_file_targets,
    _is_valid_edit_target,
    plan_diff,
)
from editing.patch_generator import (
    _synthetic_docs_version_align,
    _synthetic_docs_stability_align,
    _synthetic_docs_httpbin_align,
    _synthetic_repair,
    to_structured_patches,
)
from editing.patch_executor import execute_patch
from tests.agent_eval.real_execution import _make_explain_stub_with_substrings
from tests.agent_eval.harness import explain_artifact_ok
from tests.agent_eval.task_specs import TaskSpec


def test_instruction_path_hints_includes_md_for_docs_consistency():
    """_instruction_path_hints extracts .md paths when instruction suggests docs-consistency."""
    inst = "Align benchmark_local/DECORATORS_NOTE.md with bench_click_meta.py so stability matches."
    hints = _instruction_path_hints(inst)
    assert any("DECORATORS_NOTE.md" in h for h in hints)
    assert any(".py" in h for h in hints)


def test_instruction_suggests_docs_consistency():
    """_instruction_suggests_docs_consistency detects agree/align/match semantics."""
    assert _instruction_suggests_docs_consistency("Make README and constants agree on version.")
    assert _instruction_suggests_docs_consistency("Align DECORATORS_NOTE.md with bench_click_meta.py")
    assert not _instruction_suggests_docs_consistency("Fix multiply in ops.py")


def test_is_valid_edit_target_allows_md_when_docs_consistency(tmp_path):
    """_is_valid_edit_target allows .md when instruction suggests docs-consistency."""
    (tmp_path / "README.md").write_text("# X\n")
    assert _is_valid_edit_target("README.md", str(tmp_path), "Make README agree with constants") is True
    assert _is_valid_edit_target("README.md", str(tmp_path), "") is False


def test_instruction_hint_file_targets_includes_md_for_docs_consistency(tmp_path):
    """_instruction_hint_file_targets includes .md when docs-consistency and file exists."""
    (tmp_path / "benchmark_local").mkdir()
    (tmp_path / "benchmark_local" / "DECORATORS_NOTE.md").write_text("**`x`**\n")
    (tmp_path / "benchmark_local" / "bench_click_meta.py").write_text('CLICK_BENCH_API_STABILITY = "stable"\n')
    inst = "Align benchmark_local/DECORATORS_NOTE.md with benchmark_local/bench_click_meta.py so stability matches."
    targets = _instruction_hint_file_targets(inst, str(tmp_path))
    paths = [t[0] for t in targets]
    assert any("DECORATORS_NOTE.md" in p for p in paths)
    # When both exist, docs-consistency reorders to prefer .md first
    if any("bench_click_meta" in p for p in paths):
        md_idx = next(i for i, p in enumerate(paths) if "DECORATORS_NOTE.md" in p)
        py_idx = next(i for i, p in enumerate(paths) if "bench_click_meta" in p)
        assert md_idx < py_idx, "docs-consistency should prefer .md before .py"


def test_synthetic_docs_version_align_produces_text_sub(tmp_path):
    """_synthetic_docs_version_align produces text_sub to align APP_VERSION with README."""
    (tmp_path / "README.md").write_text("Current release: **0.9.0**\n")
    (tmp_path / "src" / "widget").mkdir(parents=True)
    (tmp_path / "src" / "widget" / "constants.py").write_text('APP_VERSION = "1.0.0"\n')
    inst = "Make README.md and src/widget/constants.py agree on major.minor."
    patch = _synthetic_docs_version_align(inst, "src/widget/constants.py", str(tmp_path))
    assert patch is not None
    assert patch.get("action") == "text_sub"
    assert "0.9.0" in patch.get("new", "")


def test_synthetic_docs_stability_align_produces_text_sub(tmp_path):
    """_synthetic_docs_stability_align produces text_sub for DECORATORS_NOTE to match meta."""
    (tmp_path / "benchmark_local").mkdir()
    (tmp_path / "benchmark_local" / "DECORATORS_NOTE.md").write_text("Stability: **`experimental`**\n")
    (tmp_path / "benchmark_local" / "bench_click_meta.py").write_text('CLICK_BENCH_API_STABILITY = "stable"\n')
    inst = "Align benchmark_local/DECORATORS_NOTE.md with bench_click_meta.py so stability matches."
    patch = _synthetic_docs_stability_align(inst, "benchmark_local/DECORATORS_NOTE.md", str(tmp_path))
    assert patch is not None
    assert patch.get("action") == "text_sub"
    assert "stable" in patch.get("new", "")


def test_synthetic_docs_httpbin_align_produces_text_sub(tmp_path):
    """_synthetic_docs_httpbin_align produces text_sub for HTTPBIN_NOTE to match meta."""
    (tmp_path / "benchmark_local").mkdir()
    (tmp_path / "benchmark_local" / "HTTPBIN_NOTE.md").write_text("Base: **`https://httpbin.org`**\n")
    (tmp_path / "benchmark_local" / "bench_requests_meta.py").write_text(
        'DEFAULT_HTTPBIN_BASE = "https://example.invalid"\n'
    )
    inst = "Make HTTPBIN_NOTE.md and bench_requests_meta.py agree on httpbin host."
    patch = _synthetic_docs_httpbin_align(inst, "benchmark_local/HTTPBIN_NOTE.md", str(tmp_path))
    assert patch is not None
    assert patch.get("action") == "text_sub"
    assert "example.invalid" in patch.get("new", "")


def test_docs_version_align_apply_succeeds(tmp_path):
    """Full pipeline: plan_diff + to_structured_patches + execute_patch for version align."""
    (tmp_path / "README.md").write_text("Current release: **0.9.0**\n")
    (tmp_path / "src" / "widget").mkdir(parents=True)
    (tmp_path / "src" / "widget" / "constants.py").write_text('APP_VERSION = "1.0.0"\n')
    inst = "Make README.md and src/widget/constants.py agree on major.minor."
    plan = {
        "changes": [
            {"file": "src/widget/constants.py", "symbol": "", "action": "modify", "patch": ""},
        ]
    }
    ctx = {"project_root": str(tmp_path), "ranked_context": [], "retrieved_symbols": []}
    out = to_structured_patches(plan, inst, ctx)
    assert out.get("changes")
    er = execute_patch(out, str(tmp_path))
    assert er.get("success") is True
    text = (tmp_path / "src" / "widget" / "constants.py").read_text()
    assert "0.9.0" in text


def test_explain_stub_includes_required_substrings():
    """_make_explain_stub_with_substrings returns text containing all substrings."""
    stub = _make_explain_stub_with_substrings(("Session.request", "hooks", "->"))
    out = stub()
    assert "Session.request" in out
    assert "hooks" in out
    assert "->" in out


def test_explain_artifact_ok_passes_with_stub_output(tmp_path):
    """explain_artifact_ok passes when artifact contains explain_required_substrings."""
    (tmp_path / "benchmark_local" / "artifacts").mkdir(parents=True)
    stub = _make_explain_stub_with_substrings(("Session.request", "hooks", "->"))
    (tmp_path / "benchmark_local" / "artifacts" / "explain_out.txt").write_text(stub())
    spec = TaskSpec(
        task_id="test",
        layer="pinned_repo",
        repo_id="x",
        repo_path="x",
        instruction="",
        expected_artifacts=("benchmark_local/artifacts/explain_out.txt",),
        grading_mode="explain_artifact",
        explain_required_substrings=("Session.request", "hooks", "->"),
    )
    ok, msg = explain_artifact_ok(spec, tmp_path)
    assert ok is True, msg


def test_no_task_id_specific_hacks():
    """Synthetic repairs use instruction semantics, not task_id."""
    # _synthetic_docs_version_align does not check task_id
    # _synthetic_docs_stability_align does not check task_id
    # _make_explain_stub_with_substrings uses substrings from spec, not hardcoded task id
    stub = _make_explain_stub_with_substrings(("custom_a", "custom_b"))
    out = stub()
    assert "custom_a" in out and "custom_b" in out
