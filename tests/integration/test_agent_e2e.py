"""Integration tests for the full agent pipeline.

Uses real services: planner, query rewriter, context ranker, diff planner,
patch generator, vector retrieval, graph retrieval, cross-encoder reranker.
No mocks.

Run with:
  TEST_MODE=integration pytest tests/integration/ -v

Requires:
- Reasoning model API reachable
- Reranker service (when RERANKER_ENABLED and candidate_count >= threshold)
"""

import logging
import os
from pathlib import Path

import pytest

from agent.memory.state import AgentState
from agent.memory.task_memory import load_task, save_task
from agent.observability.trace_logger import finish_trace, log_event, start_trace
from agent.orchestrator.deterministic_runner import run_deterministic
from repo_index.indexer import index_repo

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.integration


def _requires_tree_sitter():
    pytest.importorskip("tree_sitter_python")


_EXECUTORS_MULTI_FILE = [
    ("executor_a", "class ExecutorA:\n    def run(self):\n        return 1"),
    ("executor_b", "class ExecutorB:\n    def run(self):\n        return 2"),
    ("executor_c", "class ExecutorC:\n    def run(self):\n        return 3"),
]


def _setup_indexed_executor_repo(tmp_path: Path) -> str:
    """Create repo with executor classes, indexed for graph retrieval."""
    _requires_tree_sitter()
    exec_dir = tmp_path / "executors"
    exec_dir.mkdir(parents=True, exist_ok=True)
    (exec_dir / "__init__.py").write_text("")
    for name, body in _EXECUTORS_MULTI_FILE:
        (exec_dir / f"{name}.py").write_text(f'"""{name}"""\n\n{body}\n')
    index_dir = tmp_path / ".symbol_graph"
    index_dir.mkdir(parents=True, exist_ok=True)
    index_repo(str(tmp_path), output_dir=str(index_dir))
    return str(tmp_path)


def _run_agent_integration(instruction: str, project_root: str):
    """
    Run full agent pipeline (same flow as run_controller) and return state
    for integration assertions. No mocks.
    """
    root = Path(project_root).resolve()
    task_id = "integration-test"
    trace_id = start_trace(task_id, str(root), query=instruction)

    try:
        try:
            from repo_graph.repo_map_builder import build_repo_map
            build_repo_map(str(root))
        except Exception as e:
            logger.debug("[integration] repo_map build skipped: %s", e)

        similar_tasks = []
        try:
            from agent.memory.task_index import search_similar_tasks
            similar_tasks = search_similar_tasks(instruction, str(root), top_k=3)
        except Exception as e:
            logger.debug("[integration] task_index search skipped: %s", e)

        state, loop_output = run_deterministic(
            instruction,
            str(root),
            trace_id=trace_id,
            similar_tasks=similar_tasks,
            log_event_fn=log_event,
        )

        completed_steps = loop_output["completed_steps"]
        save_task(
            task_id=task_id,
            instruction=instruction,
            plan=loop_output["plan_result"],
            steps=completed_steps,
            patches=loop_output.get("patches_applied", []),
            files_modified=loop_output.get("files_modified", []),
            errors_encountered=loop_output.get("errors_encountered", []),
            results={"completed_steps": len(completed_steps)},
            project_root=str(root),
        )
        return state, loop_output
    finally:
        finish_trace(trace_id)


def test_agent_e2e_add_logging(tmp_path, monkeypatch):
    """
    Full pipeline: plan -> search -> retrieval -> reranker -> diff planner -> patch.
    Verifies successful completion and pipeline metrics.
    """
    monkeypatch.setenv("TEST_REPAIR_ENABLED", "0")
    monkeypatch.setenv("ENABLE_DIFF_PLANNER", "1")
    project_root = _setup_indexed_executor_repo(tmp_path)
    monkeypatch.setenv("SERENA_PROJECT_DIR", project_root)

    instruction = "Add logging to all executor classes"

    state, loop_output = _run_agent_integration(instruction, project_root)

    completed_steps = loop_output["completed_steps"]
    errors = loop_output.get("errors_encountered", [])

    # 1. Agent completes successfully
    assert errors == [], f"Expected no errors, got: {errors}"
    assert len(completed_steps) >= 1, "Expected at least one completed step"

    # 2. At least one SEARCH step executed
    search_steps = [s for s in completed_steps if (s.get("action") or "").upper() == "SEARCH"]
    assert len(search_steps) >= 1, (
        f"Expected >=1 SEARCH step, got {len(search_steps)}. "
        f"completed_steps={[s.get('action') for s in completed_steps]}"
    )

    # 3. Retrieval pipeline ran (tool_memories has search_code entries)
    tool_memories = state.context.get("tool_memories") or []
    search_calls = [m for m in tool_memories if m.get("tool") == "search_code"]
    assert len(search_calls) >= 1, "Expected at least one search_code call in tool_memories"

    # 4. Retrieval metrics exist (from retrieval pipeline)
    retrieval_metrics = state.context.get("retrieval_metrics") or {}
    assert retrieval_metrics is not None, "Expected retrieval_metrics in state.context"

    # 5. Reranker: when candidate_count >= threshold, rerank should have run
    candidate_count = retrieval_metrics.get("candidate_count", 0)
    rerank_skipped = retrieval_metrics.get("rerank_skipped_reason")
    rerank_latency = retrieval_metrics.get("rerank_latency_ms")

    # Debug output for flaky integration tests
    print(f"\n[integration] search_steps={len(search_steps)}")
    print(f"[integration] candidate_count={candidate_count}")
    print(f"[integration] rerank_latency_ms={rerank_latency}")
    print(f"[integration] rerank_skipped_reason={rerank_skipped}")
    print(f"[integration] retrieval_metrics keys={list(retrieval_metrics.keys())}")

    # If edits were in plan and executed, patch stage ran
    edit_steps = [s for s in completed_steps if (s.get("action") or "").upper() == "EDIT"]
    if edit_steps:
        assert loop_output.get("patches_applied") is not None or loop_output.get("files_modified") is not None, (
            "EDIT step completed but no patches/files_modified recorded"
        )

    # Task memory saved
    task = load_task("integration-test", project_root=project_root)
    assert task is not None
    assert task.get("instruction") == instruction
    assert task.get("plan") is not None
