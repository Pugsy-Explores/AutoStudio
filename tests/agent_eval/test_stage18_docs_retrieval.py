"""Stage 18 — docs-consistency retrieval and context plumbing (generic semantics, no task ids)."""

from __future__ import annotations

from pathlib import Path

from agent.memory.state import AgentState
from agent.retrieval.search_target_filter import filter_and_rank_search_results
from agent.retrieval.task_semantics import (
    instruction_path_hints,
    instruction_suggests_docs_consistency,
    validation_check_script_paths_in_instruction,
)
from agent.retrieval.retrieval_pipeline import _inject_instruction_path_snippets
from agent.tools.validation_scope import resolve_inner_loop_validation
from editing.diff_planner import plan_diff
from editing.patch_generator import to_structured_patches


def test_filter_keeps_md_with_py_when_docs_alignment_query(tmp_path: Path):
    readme = tmp_path / "README.md"
    readme.write_text("# v1\n", encoding="utf-8")
    py = tmp_path / "src" / "widget" / "constants.py"
    py.parent.mkdir(parents=True, exist_ok=True)
    py.write_text('APP_VERSION = "0.0.0"\n', encoding="utf-8")

    q = "Make README.md and src/widget/constants.py agree on major.minor so scripts/check_readme_version.py exits 0."
    results = [
        {"file": str(readme), "snippet": "# v1", "score": 0.9},
        {"file": str(py), "snippet": "APP_VERSION", "score": 0.5},
    ]
    out = filter_and_rank_search_results(results, q, str(tmp_path))
    files = {Path(x["file"]).name for x in out}
    assert "README.md" in files
    assert "constants.py" in files


def test_instruction_path_hints_pairs_generic_docs_task():
    inst = (
        "Make benchmark_local/HTTPBIN_NOTE.md and benchmark_local/bench_requests_meta.py agree on the "
        "httpbin host."
    )
    assert instruction_suggests_docs_consistency(inst)
    hints = instruction_path_hints(inst)
    assert any("HTTPBIN_NOTE.md" in h for h in hints)
    assert any("bench_requests_meta.py" in h for h in hints)


def test_validation_check_script_paths_in_instruction_hints():
    inst = "Run python3 benchmark_local/check_httpbin_doc.py after edits."
    assert "benchmark_local/check_httpbin_doc.py" in validation_check_script_paths_in_instruction(inst)


def test_resolve_inner_loop_validation_non_pytest_command():
    """Inner env can be a shell check script; resolver must set test_cmd (Stage 18)."""
    import os

    from agent.tools.validation_scope import ENV_INNER_VALIDATION_CMD

    prev = os.environ.get(ENV_INNER_VALIDATION_CMD)
    try:
        os.environ[ENV_INNER_VALIDATION_CMD] = "python3 scripts/check_readme_version.py"
        out = resolve_inner_loop_validation("/tmp", {})
        assert out.get("resolved_validation_command")
        assert out.get("test_cmd") == "python3 scripts/check_readme_version.py"
    finally:
        if prev is None:
            os.environ.pop(ENV_INNER_VALIDATION_CMD, None)
        else:
            os.environ[ENV_INNER_VALIDATION_CMD] = prev


def test_docs_consistency_single_synthetic_patch(tmp_path: Path):
    """Emit one text_sub patch for docs alignment; do not append AST placeholders for sibling files."""
    d = tmp_path / "src" / "widget"
    d.mkdir(parents=True)
    (d / "constants.py").write_text('APP_VERSION = "9.9.9"\n', encoding="utf-8")
    (tmp_path / "README.md").write_text("Current release: **0.0.1**\n", encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "check_readme_version.py").write_text("# check\n", encoding="utf-8")

    inst = (
        "Make README.md and src/widget/constants.py agree on major.minor "
        "so scripts/check_readme_version.py exits 0."
    )
    plan = {
        "changes": [
            {"file": "src/widget/constants.py", "symbol": "", "action": "modify", "patch": "x", "reason": "r"},
            {"file": "scripts/check_readme_version.py", "symbol": "", "action": "modify", "patch": "x", "reason": "r"},
        ]
    }
    ctx = {"project_root": str(tmp_path), "ranked_context": []}
    out = to_structured_patches(plan, inst, ctx)
    ch = out.get("changes") or []
    assert len(ch) == 1
    assert ch[0].get("file") == "src/widget/constants.py"
    assert (ch[0].get("patch") or {}).get("action") == "text_sub"


def test_inject_instruction_paths_reach_plan_diff(tmp_path: Path):
    (tmp_path / "README.md").write_text("# App 1.0.0\n", encoding="utf-8")
    d = tmp_path / "src" / "widget"
    d.mkdir(parents=True)
    (d / "constants.py").write_text('APP_VERSION = "9.9.9"\n', encoding="utf-8")

    inst = (
        "Make README.md and src/widget/constants.py agree on major.minor "
        "so scripts/check_readme_version.py exits 0."
    )
    state = AgentState(
        instruction=inst,
        current_plan={"plan_id": "p", "steps": []},
        context={
            "project_root": str(tmp_path),
            "parent_instruction": inst,
            "ranked_context": [],
        },
    )
    final, n = _inject_instruction_path_snippets([], state, str(tmp_path), inst)
    assert n >= 1
    assert any("README.md" in (c.get("file") or "") for c in final if isinstance(c, dict))

    ctx = dict(state.context)
    ctx["ranked_context"] = final
    pd = plan_diff(inst, ctx)
    assert pd.get("changes"), "plan_diff should see injected files"
