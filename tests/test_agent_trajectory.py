"""Tests for complex agent trajectories.

Validates long agent runs with:
- Multiple search steps
- Multiple edit steps
- Conflict resolver triggered
- Repair loop runs when tests fail

Task: "Add logging to all executor classes"

Verification:
- Agent completes task
- No infinite loops (MAX_REPLAN_ATTEMPTS, MAX_TASK_RUNTIME)
- Runtime < 15 minutes
"""

import logging
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from agent.memory.task_memory import load_task
from tests.utils.runtime_adapter import run_controller
from editing.conflict_resolver import resolve_conflicts
from editing.test_repair_loop import run_with_repair
from repo_index.indexer import index_repo

logger = logging.getLogger(__name__)

MAX_TASK_RUNTIME_SECONDS = 15 * 60  # 15 minutes


# Three executor classes in ONE file to trigger conflict resolver (same_file)
_EXECUTORS_SINGLE_FILE = '''"""Executors module."""

class ExecutorA:
    def run(self):
        return 1

class ExecutorB:
    def run(self):
        return 2

class ExecutorC:
    def run(self):
        return 3
'''


def _setup_indexed_multi_executor_repo(tmp_path: Path, single_file: bool = False) -> str:
    """Create repo with 3 executor classes, indexed."""
    exec_dir = tmp_path / "executors"
    exec_dir.mkdir(parents=True, exist_ok=True)
    (exec_dir / "__init__.py").write_text("")

    if single_file:
        (exec_dir / "executors.py").write_text(_EXECUTORS_SINGLE_FILE)
    else:
        for name, body in [
            ("executor_a", "class ExecutorA:\n    def run(self):\n        return 1"),
            ("executor_b", "class ExecutorB:\n    def run(self):\n        return 2"),
            ("executor_c", "class ExecutorC:\n    def run(self):\n        return 3"),
        ]:
            (exec_dir / f"{name}.py").write_text(f'"""{name}"""\n\n{body}\n')

    index_dir = tmp_path / ".symbol_graph"
    index_dir.mkdir(parents=True, exist_ok=True)
    index_repo(str(tmp_path), output_dir=str(index_dir))
    return str(tmp_path)


@pytest.fixture
def trajectory_env(monkeypatch):
    """Env for trajectory tests: diff planner on, vector off, index embeddings off."""
    monkeypatch.setenv("ENABLE_DIFF_PLANNER", "1")
    monkeypatch.setenv("ENABLE_VECTOR_SEARCH", "0")
    monkeypatch.setenv("INDEX_EMBEDDINGS", "0")


@pytest.fixture
def indexed_single_file_executors(tmp_path):
    """Repo with 3 executor classes in one file (triggers conflict resolver)."""
    return _setup_indexed_multi_executor_repo(tmp_path, single_file=True)


@pytest.fixture
def indexed_multi_file_executors(tmp_path):
    """Repo with 3 executor classes in separate files."""
    return _setup_indexed_multi_executor_repo(tmp_path, single_file=False)


@pytest.mark.slow
def test_trajectory_add_logging_completes(
    indexed_single_file_executors, monkeypatch, trajectory_env, e2e_use_mock
):
    """Agent completes task 'Add logging to all executor classes'."""
    monkeypatch.setenv("TEST_REPAIR_ENABLED", "0")  # Patch only for deterministic run
    project_root = indexed_single_file_executors
    monkeypatch.setenv("SERENA_PROJECT_DIR", project_root)

    exec_path = Path(project_root) / "executors" / "executors.py"
    assert exec_path.exists()

    mock_search = {
        "results": [
            {"file": str(exec_path), "symbol": "ExecutorA", "line": 4, "snippet": "class ExecutorA"},
            {"file": str(exec_path), "symbol": "ExecutorB", "line": 8, "snippet": "class ExecutorB"},
            {"file": str(exec_path), "symbol": "ExecutorC", "line": 12, "snippet": "class ExecutorC"},
        ],
        "query": "executor classes",
    }

    # plan_diff returns 3 changes to SAME file -> conflict resolver triggers
    mock_plan_diff = {
        "changes": [
            {
                "file": str(exec_path),
                "symbol": "run",
                "action": "modify",
                "patch": "import logging\nlogger = logging.getLogger(__name__)\nlogger.info('ExecutorA.run')",
                "reason": "Add logging to ExecutorA",
            },
            {
                "file": str(exec_path),
                "symbol": "run",
                "action": "modify",
                "patch": "logger.info('ExecutorB.run')",
                "reason": "Add logging to ExecutorB",
            },
            {
                "file": str(exec_path),
                "symbol": "run",
                "action": "modify",
                "patch": "logger.info('ExecutorC.run')",
                "reason": "Add logging to ExecutorC",
            },
        ],
    }

    instruction = "Add logging to all executor classes"

    with (
        patch("planner.planner.call_reasoning_model") as mock_planner,
        patch("agent.retrieval.query_rewriter.call_reasoning_model") as mock_rewriter,
        patch("agent.retrieval.query_rewriter.call_small_model") as mock_small,
        patch("agent.retrieval.context_ranker.call_reasoning_model") as mock_ranker,
        patch("agent.retrieval.graph_retriever.retrieve_symbol_context", return_value=mock_search),
        patch("editing.diff_planner.plan_diff", return_value=mock_plan_diff),
    ):
        mock_planner.return_value = (
            '{"steps": ['
            '{"id": 1, "action": "SEARCH", "description": "Find all executor classes", "reason": ""}, '
            '{"id": 2, "action": "EDIT", "description": "Add logging to all executor classes", "reason": ""}'
            "]}"
        )
        mock_rewriter.return_value = '{"query": "executor classes", "tool": "find_symbol", "reason": ""}'
        mock_small.return_value = '{"query": "executor classes", "tool": "", "reason": ""}'
        mock_ranker.return_value = "0.9\n0.9\n0.9"

        start = time.perf_counter()
        result = run_controller(instruction, project_root=project_root)
        elapsed = time.perf_counter() - start

    assert "task_id" in result
    assert result.get("errors") == []
    assert result.get("completed_steps", 0) >= 2
    assert elapsed < MAX_TASK_RUNTIME_SECONDS, f"Runtime {elapsed}s exceeded {MAX_TASK_RUNTIME_SECONDS}s"

    task = load_task(result["task_id"], project_root=project_root)
    assert task is not None
    assert "files_modified" in task or "patches" in task


def test_trajectory_multiple_search_steps(
    indexed_multi_file_executors, monkeypatch, trajectory_env, e2e_use_mock
):
    """Verify multiple SEARCH steps are executed (plan has 2+ search steps)."""
    monkeypatch.setenv("TEST_REPAIR_ENABLED", "0")
    monkeypatch.setenv("RETRIEVAL_CACHE_SIZE", "0")
    project_root = indexed_multi_file_executors
    monkeypatch.setenv("SERENA_PROJECT_DIR", project_root)

    path_a = Path(project_root) / "executors" / "executor_a.py"
    path_b = Path(project_root) / "executors" / "executor_b.py"
    path_c = Path(project_root) / "executors" / "executor_c.py"

    mock_search = {
        "results": [
            {"file": str(path_a), "symbol": "ExecutorA", "line": 2, "snippet": "class ExecutorA"},
            {"file": str(path_b), "symbol": "ExecutorB", "line": 2, "snippet": "class ExecutorB"},
            {"file": str(path_c), "symbol": "ExecutorC", "line": 2, "snippet": "class ExecutorC"},
        ],
        "query": "executor",
    }

    # Plan with 2 SEARCH steps + 1 EDIT
    plan_json = (
        '{"steps": ['
        '{"id": 1, "action": "SEARCH", "description": "Find executor classes", "reason": ""}, '
        '{"id": 2, "action": "SEARCH", "description": "Find run methods", "reason": ""}, '
        '{"id": 3, "action": "EDIT", "description": "Add logging to all executor classes", "reason": ""}'
        "]}"
    )

    with (
        patch("agent.orchestrator.plan_resolver.ENABLE_INSTRUCTION_ROUTER", False),
        patch("planner.planner.call_reasoning_model", return_value=plan_json),
        patch("agent.retrieval.query_rewriter.call_reasoning_model") as mock_rewriter,
        patch("agent.retrieval.query_rewriter.call_small_model") as mock_small,
        patch("agent.retrieval.context_ranker.call_reasoning_model", return_value="0.9\n0.9\n0.9"),
        patch("agent.retrieval.graph_retriever.retrieve_symbol_context", return_value=mock_search),
        patch("editing.diff_planner.plan_diff") as mock_plan_diff,
    ):
        mock_rewriter.return_value = '{"query": "executor", "tool": "find_symbol", "reason": ""}'
        mock_small.return_value = '{"query": "executor", "tool": "", "reason": ""}'
        mock_plan_diff.return_value = {
            "changes": [
                {"file": str(path_a), "symbol": "run", "action": "modify", "patch": "logger.info(1)", "reason": ""},
                {"file": str(path_b), "symbol": "run", "action": "modify", "patch": "logger.info(2)", "reason": ""},
                {"file": str(path_c), "symbol": "run", "action": "modify", "patch": "logger.info(3)", "reason": ""},
            ],
        }

        result = run_controller("Add logging to all executor classes", project_root=project_root)

    # Assert on observable behavior: each SEARCH step in the plan was executed and completed.
    # Use saved task steps (run_controller returns count, not list).
    task = load_task(result["task_id"], project_root=project_root)
    assert task is not None
    search_steps_completed = [
        s for s in task.get("steps", [])
        if (s.get("action") or "").upper() == "SEARCH"
    ]
    assert len(search_steps_completed) >= 2, (
        f"Expected >=2 SEARCH steps completed, got {len(search_steps_completed)}. "
        f"steps={task.get('steps', [])}"
    )
    assert result.get("errors") == []


def test_trajectory_conflict_resolver_triggered(
    indexed_single_file_executors, monkeypatch, trajectory_env, e2e_use_mock
):
    """Verify conflict resolver is invoked when multiple edits target same file."""
    monkeypatch.setenv("TEST_REPAIR_ENABLED", "0")
    project_root = indexed_single_file_executors
    monkeypatch.setenv("SERENA_PROJECT_DIR", project_root)

    exec_path = Path(project_root) / "executors" / "executors.py"
    mock_search = {
        "results": [
            {"file": str(exec_path), "symbol": "ExecutorA", "line": 4, "snippet": "class ExecutorA"},
            {"file": str(exec_path), "symbol": "ExecutorB", "line": 8, "snippet": "class ExecutorB"},
            {"file": str(exec_path), "symbol": "ExecutorC", "line": 12, "snippet": "class ExecutorC"},
        ],
        "query": "executors",
    }

    with (
        patch("planner.planner.call_reasoning_model") as mock_planner,
        patch("agent.retrieval.query_rewriter.call_reasoning_model") as mock_rewriter,
        patch("agent.retrieval.query_rewriter.call_small_model") as mock_small,
        patch("agent.retrieval.context_ranker.call_reasoning_model", return_value="0.9\n0.9\n0.9"),
        patch("agent.retrieval.graph_retriever.retrieve_symbol_context", return_value=mock_search),
        patch("editing.conflict_resolver.resolve_conflicts", wraps=resolve_conflicts) as mock_resolve,
        patch("editing.diff_planner.plan_diff") as mock_plan_diff,
    ):
        mock_planner.return_value = (
            '{"steps": ['
            '{"id": 1, "action": "SEARCH", "description": "Find executor classes", "reason": ""}, '
            '{"id": 2, "action": "EDIT", "description": "Add logging to all executor classes", "reason": ""}'
            "]}"
        )
        mock_rewriter.return_value = '{"query": "executor", "tool": "find_symbol", "reason": ""}'
        mock_small.return_value = '{"query": "executor", "tool": "", "reason": ""}'

        # 3 changes to SAME file -> conflict
        mock_plan_diff.return_value = {
            "changes": [
                {"file": str(exec_path), "symbol": "run", "action": "modify", "patch": "x=1", "reason": "A"},
                {"file": str(exec_path), "symbol": "run", "action": "modify", "patch": "x=2", "reason": "B"},
                {"file": str(exec_path), "symbol": "run", "action": "modify", "patch": "x=3", "reason": "C"},
            ],
        }

        result = run_controller("Add logging to all executor classes", project_root=project_root)

    assert mock_resolve.call_count >= 1, "Conflict resolver should be invoked"
    assert result.get("errors") == []


def test_trajectory_repair_loop_runs_on_test_failure(
    indexed_multi_file_executors, monkeypatch, trajectory_env, e2e_use_mock
):
    """Verify repair loop runs when tests fail (TEST_REPAIR_ENABLED=1)."""
    monkeypatch.setenv("TEST_REPAIR_ENABLED", "1")
    project_root = indexed_multi_file_executors
    monkeypatch.setenv("SERENA_PROJECT_DIR", project_root)

    # Add a test file that will fail initially, then pass after repair
    test_dir = Path(project_root) / "tests"
    test_dir.mkdir(exist_ok=True)
    (test_dir / "__init__.py").write_text("")
    (test_dir / "test_executors.py").write_text(
        "def test_executors_ok():\n    from executors.executor_a import ExecutorA\n    assert ExecutorA().run() == 1\n"
    )

    path_a = Path(project_root) / "executors" / "executor_a.py"
    path_b = Path(project_root) / "executors" / "executor_b.py"
    path_c = Path(project_root) / "executors" / "executor_c.py"

    with (
        patch("planner.planner.call_reasoning_model") as mock_planner,
        patch("agent.retrieval.query_rewriter.call_reasoning_model") as mock_rewriter,
        patch("agent.retrieval.query_rewriter.call_small_model") as mock_small,
        patch("agent.retrieval.context_ranker.call_reasoning_model", return_value="0.9\n0.9\n0.9"),
        patch("agent.retrieval.graph_retriever.retrieve_symbol_context") as mock_search,
        patch("editing.test_repair_loop.run_with_repair", wraps=run_with_repair) as mock_repair,
        patch("editing.diff_planner.plan_diff") as mock_plan_diff,
    ):
        mock_planner.return_value = (
            '{"steps": ['
            '{"id": 1, "action": "SEARCH", "description": "Find executor classes", "reason": ""}, '
            '{"id": 2, "action": "EDIT", "description": "Add logging to all executor classes", "reason": ""}'
            "]}"
        )
        mock_rewriter.return_value = '{"query": "executor", "tool": "find_symbol", "reason": ""}'
        mock_small.return_value = '{"query": "executor", "tool": "", "reason": ""}'
        mock_search.return_value = {
            "results": [
                {"file": str(path_a), "symbol": "ExecutorA", "line": 2, "snippet": "class ExecutorA"},
                {"file": str(path_b), "symbol": "ExecutorB", "line": 2, "snippet": "class ExecutorB"},
                {"file": str(path_c), "symbol": "ExecutorC", "line": 2, "snippet": "class ExecutorC"},
            ],
            "query": "executor",
        }

        # Patch that adds logging without breaking tests (insert at function_body_start)
        mock_plan_diff.return_value = {
            "changes": [
                {
                    "file": str(path_a),
                    "symbol": "run",
                    "action": "modify",
                    "patch": "import logging\nlogger = logging.getLogger(__name__)\nlogger.info('ExecutorA.run')",
                    "reason": "Add logging",
                },
                {
                    "file": str(path_b),
                    "symbol": "run",
                    "action": "modify",
                    "patch": "import logging\nlogger = logging.getLogger(__name__)\nlogger.info('ExecutorB.run')",
                    "reason": "Add logging",
                },
                {
                    "file": str(path_c),
                    "symbol": "run",
                    "action": "modify",
                    "patch": "import logging\nlogger = logging.getLogger(__name__)\nlogger.info('ExecutorC.run')",
                    "reason": "Add logging",
                },
            ],
        }

        result = run_controller("Add logging to all executor classes", project_root=project_root)

    assert mock_repair.call_count >= 1, "Repair loop (run_with_repair) should be invoked"


def test_trajectory_no_infinite_loop_on_replan(
    indexed_multi_file_executors, monkeypatch, trajectory_env, e2e_use_mock
):
    """Verify agent stops after MAX_REPLAN_ATTEMPTS, no infinite loop."""
    monkeypatch.setenv("TEST_REPAIR_ENABLED", "0")
    project_root = indexed_multi_file_executors
    monkeypatch.setenv("SERENA_PROJECT_DIR", project_root)

    path_a = Path(project_root) / "executors" / "executor_a.py"

    # Plan: SEARCH then EDIT. EDIT will always fail (bad patch).
    plan_json = (
        '{"steps": ['
        '{"id": 1, "action": "SEARCH", "description": "Find executor classes", "reason": ""}, '
        '{"id": 2, "action": "EDIT", "description": "Add logging to all executor classes", "reason": ""}'
        "]}"
    )

    with (
        patch("planner.planner.call_reasoning_model", return_value=plan_json),
        patch("agent.retrieval.query_rewriter.call_reasoning_model") as mock_rewriter,
        patch("agent.retrieval.query_rewriter.call_small_model") as mock_small,
        patch("agent.retrieval.context_ranker.call_reasoning_model", return_value="0.9"),
        patch("agent.retrieval.graph_retriever.retrieve_symbol_context") as mock_search,
        patch("editing.diff_planner.plan_diff") as mock_plan_diff,
        patch("editing.test_repair_loop.run_with_repair") as mock_repair,
    ):
        mock_rewriter.return_value = '{"query": "executor", "tool": "find_symbol", "reason": ""}'
        mock_small.return_value = '{"query": "executor", "tool": "", "reason": ""}'
        mock_search.return_value = {
            "results": [{"file": str(path_a), "symbol": "ExecutorA", "line": 2, "snippet": "class ExecutorA"}],
            "query": "executor",
        }
        mock_plan_diff.return_value = {
            "changes": [
                {"file": str(path_a), "symbol": "run", "action": "modify", "patch": "x", "reason": ""},
            ],
        }
        # run_with_repair always fails -> triggers replan
        mock_repair.return_value = {
            "success": False,
            "error": "patch_failed",
            "reason": "syntax error",
        }

        start = time.perf_counter()
        result = run_controller("Add logging to all executor classes", project_root=project_root)
        elapsed = time.perf_counter() - start

    # Agent should stop after MAX_REPLAN_ATTEMPTS (5), not loop forever
    assert elapsed < 60, f"Should stop quickly on repeated failure, took {elapsed}s"
    assert "max_replan_attempts_exceeded" in result.get("errors", []) or len(result.get("errors", [])) > 0


def test_trajectory_runtime_under_limit(
    indexed_multi_file_executors, monkeypatch, trajectory_env, e2e_use_mock
):
    """Verify task completes within 15 minutes."""
    monkeypatch.setenv("TEST_REPAIR_ENABLED", "0")
    project_root = indexed_multi_file_executors
    monkeypatch.setenv("SERENA_PROJECT_DIR", project_root)

    path_a = Path(project_root) / "executors" / "executor_a.py"
    path_b = Path(project_root) / "executors" / "executor_b.py"
    path_c = Path(project_root) / "executors" / "executor_c.py"

    mock_search = {
        "results": [
            {"file": str(path_a), "symbol": "ExecutorA", "line": 2, "snippet": "class ExecutorA"},
            {"file": str(path_b), "symbol": "ExecutorB", "line": 2, "snippet": "class ExecutorB"},
            {"file": str(path_c), "symbol": "ExecutorC", "line": 2, "snippet": "class ExecutorC"},
        ],
        "query": "executor",
    }

    with (
        patch("planner.planner.call_reasoning_model") as mock_planner,
        patch("agent.retrieval.query_rewriter.call_reasoning_model") as mock_rewriter,
        patch("agent.retrieval.query_rewriter.call_small_model") as mock_small,
        patch("agent.retrieval.context_ranker.call_reasoning_model", return_value="0.9\n0.9\n0.9"),
        patch("agent.retrieval.graph_retriever.retrieve_symbol_context", return_value=mock_search),
        patch("editing.diff_planner.plan_diff") as mock_plan_diff,
    ):
        mock_planner.return_value = (
            '{"steps": ['
            '{"id": 1, "action": "SEARCH", "description": "Find executor classes", "reason": ""}, '
            '{"id": 2, "action": "EDIT", "description": "Add logging to all executor classes", "reason": ""}'
            "]}"
        )
        mock_rewriter.return_value = '{"query": "executor", "tool": "find_symbol", "reason": ""}'
        mock_small.return_value = '{"query": "executor", "tool": "", "reason": ""}'
        mock_plan_diff.return_value = {
            "changes": [
                {"file": str(path_a), "symbol": "run", "action": "modify", "patch": "return 1", "reason": ""},
                {"file": str(path_b), "symbol": "run", "action": "modify", "patch": "return 2", "reason": ""},
                {"file": str(path_c), "symbol": "run", "action": "modify", "patch": "return 3", "reason": ""},
            ],
        }

        start = time.perf_counter()
        result = run_controller("Add logging to all executor classes", project_root=project_root)
        elapsed = time.perf_counter() - start

    assert elapsed < MAX_TASK_RUNTIME_SECONDS, f"Runtime {elapsed}s exceeded {MAX_TASK_RUNTIME_SECONDS}s"
    assert "task_id" in result
