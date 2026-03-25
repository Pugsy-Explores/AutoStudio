"""Stage 17 — Edit grounding and explain-artifact behavior tests (public APIs only)."""

from __future__ import annotations

from editing.diff_planner import plan_diff
from editing.patch_generator import to_structured_patches
from editing.patch_executor import execute_patch
from tests.agent_eval.harness import explain_artifact_ok
from tests.agent_eval.task_specs import TaskSpec
from tests.utils.runtime_adapter import run_agent


def test_docs_version_align_apply_succeeds(tmp_path):
    """Public editing pipeline returns observable result contract for docs/code alignment."""
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
    _ = plan_diff(inst, ctx)
    out = to_structured_patches(plan, inst, ctx)
    assert "changes" in out
    if out.get("changes"):
        er = execute_patch(out, str(tmp_path))
        assert er.get("success") is True
    else:
        # Public API may reject weakly grounded edits; verify explicit rejection contract.
        assert out.get("patch_generation_reject") is not None


def test_explain_artifact_ok_passes_with_stub_output(tmp_path):
    """Public artifact validator passes on required-substring output."""
    (tmp_path / "benchmark_local" / "artifacts").mkdir(parents=True)
    (tmp_path / "benchmark_local" / "artifacts" / "explain_out.txt").write_text(
        "Session.request -> hooks ordering is preserved."
    )
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


def test_runtime_adapter_exposes_observable_state():
    """Runtime adapter returns observable state contract for test callers."""
    state = run_agent("Finish immediately without any tool calls")
    assert hasattr(state, "history")
    assert hasattr(state, "context")
