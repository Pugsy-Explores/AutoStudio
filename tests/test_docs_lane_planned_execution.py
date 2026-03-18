from pathlib import Path

from agent.memory.state import AgentState
from agent.orchestrator.execution_loop import ExecutionLoopMode, execution_loop


def test_planned_docs_lane_runs_end_to_end(tmp_path: Path):
    # Create minimal docs artifacts in a temp repo root.
    (tmp_path / "README.md").write_text("# Project\n\nThis is the README.\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "overview.md").write_text("# Overview\n\nDocs overview.\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_docs.md").write_text("# Test Doc\n\nShould be excluded.\n", encoding="utf-8")

    plan = {
        "plan_id": "plan_docs_001",
        "steps": [
            {
                "id": 1,
                "action": "SEARCH_CANDIDATES",
                "artifact_mode": "docs",
                "description": "Find README/docs artifacts",
                "query": "readme docs",
                "reason": "Need docs candidates",
            },
            {
                "id": 2,
                "action": "BUILD_CONTEXT",
                "artifact_mode": "docs",
                "description": "Build docs context",
                "reason": "Read top docs files",
            },
            {
                "id": 3,
                "action": "EXPLAIN",
                "artifact_mode": "docs",
                "description": "Explain what the docs contain",
                "reason": "Answer using docs context",
            },
        ],
    }

    state = AgentState(
        instruction="where are readmes and docs",
        current_plan=plan,
        context={
            "project_root": str(tmp_path),
            "retrieved_files": [],
            "retrieved_symbols": [],
            "retrieved_references": [],
            "context_snippets": [],
            "ranked_context": [],
            "context_candidates": [],
            "ranking_scores": [],
        },
    )

    result = execution_loop(state, state.instruction, mode=ExecutionLoopMode.DETERMINISTIC)
    assert result.state.context.get("artifact_mode") == "docs"
    ranked = result.state.context.get("ranked_context") or []
    assert ranked, "docs lane should populate ranked_context"
    assert all(isinstance(x, dict) and x.get("artifact_type") == "doc" for x in ranked)

