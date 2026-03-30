"""End-to-end tests for the full agent pipeline.

Default: use real LLM; if unreachable, warn and fall back to mock.
Use --mock to force mock mode (skip LLM probe).

  pytest tests/test_agent_e2e.py -v          # default: try LLM, fallback to mock
  pytest tests/test_agent_e2e.py -v --mock   # always use mock

Scenarios:
1. Explain code: plan -> search -> retrieval -> explain
2. Code edit: plan -> search -> diff planner -> patch -> index update
3. Multi-file change: conflict resolver, sequential patch groups

Assertions: no exceptions, patches applied (mock), index updated, task memory saved.
"""

import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from agent.memory.task_memory import load_task
from tests.utils.runtime_adapter import run_controller
from repo_index.indexer import index_repo

logger = logging.getLogger(__name__)


# Minimal StepExecutor-like module for fixtures
_EXECUTOR_PY = '''"""StepExecutor: execute planner steps via dispatcher."""

import logging

logger = logging.getLogger(__name__)


class StepExecutor:
    """Execute planner steps sequentially."""

    def execute_step(self, step, state):
        """Run a single step; return result."""
        return {"success": True, "output": step}
'''

# Two executor classes for multi-file scenario
_EXECUTOR_A_PY = '''"""Executor A."""

class ExecutorA:
    def run(self):
        return 1
'''

_EXECUTOR_B_PY = '''"""Executor B."""

class ExecutorB:
    def run(self):
        return 2
'''


def _setup_indexed_repo(tmp_path: Path, executor_content: str, subdir: str = "agent/execution") -> str:
    """Create repo with executor, index it, return project_root."""
    exec_dir = tmp_path / subdir
    exec_dir.mkdir(parents=True, exist_ok=True)
    (exec_dir / "__init__.py").write_text("")
    (exec_dir / "executor.py").write_text(executor_content)

    index_dir = tmp_path / ".symbol_graph"
    index_dir.mkdir(parents=True, exist_ok=True)
    index_repo(str(tmp_path), output_dir=str(index_dir))
    return str(tmp_path)


def _plan_explain_step_executor():
    """Plan for: Explain how StepExecutor works."""
    return {
        "steps": [
            {"id": 1, "action": "SEARCH", "description": "Find StepExecutor implementation", "reason": "locate code"},
            {"id": 2, "action": "EXPLAIN", "description": "Explain how StepExecutor works", "reason": "answer user"},
        ],
    }


def _plan_edit_add_logging():
    """Plan for: Add logging to StepExecutor.execute_step."""
    return {
        "steps": [
            {"id": 1, "action": "SEARCH", "description": "Find StepExecutor.execute_step", "reason": "locate target"},
            {"id": 2, "action": "EDIT", "description": "Add logging to StepExecutor.execute_step", "reason": "implement"},
        ],
    }


def _plan_multi_file_edit():
    """Plan for: Add logging to every executor class."""
    return {
        "steps": [
            {"id": 1, "action": "SEARCH", "description": "Find all executor classes", "reason": "locate targets"},
            {"id": 2, "action": "EDIT", "description": "Add logging to every executor class", "reason": "implement"},
        ],
    }


@pytest.fixture(autouse=True)
def _e2e_env(monkeypatch):
    """Disable test repair and ensure diff planner enabled for E2E."""
    monkeypatch.setenv("TEST_REPAIR_ENABLED", "0")
    monkeypatch.setenv("ENABLE_DIFF_PLANNER", "1")
    monkeypatch.setenv("ENABLE_VECTOR_SEARCH", "0")
    monkeypatch.setenv("INDEX_EMBEDDINGS", "0")


@pytest.fixture
def indexed_executor_repo(tmp_path):
    """Repo with StepExecutor, indexed for graph retrieval."""
    return _setup_indexed_repo(tmp_path, _EXECUTOR_PY)


@pytest.fixture
def indexed_multi_executor_repo(tmp_path):
    """Repo with ExecutorA and ExecutorB in separate files."""
    exec_dir = tmp_path / "executors"
    exec_dir.mkdir(parents=True, exist_ok=True)
    (exec_dir / "__init__.py").write_text("")
    (exec_dir / "executor_a.py").write_text(_EXECUTOR_A_PY)
    (exec_dir / "executor_b.py").write_text(_EXECUTOR_B_PY)

    index_dir = tmp_path / ".symbol_graph"
    index_dir.mkdir(parents=True, exist_ok=True)
    index_repo(str(tmp_path), output_dir=str(index_dir))
    return str(tmp_path)


def test_e2e_explain_step_executor(indexed_executor_repo, monkeypatch, e2e_use_mock):
    """Scenario 1: Explain code - plan -> search -> retrieval -> explain."""
    project_root = indexed_executor_repo
    monkeypatch.setenv("SERENA_PROJECT_DIR", project_root)

    executor_path = str(Path(project_root) / "agent" / "execution" / "executor.py")
    mock_search_results = {
        "results": [
            {"file": executor_path, "symbol": "StepExecutor", "line": 14, "snippet": "class StepExecutor:"},
        ],
        "query": "StepExecutor",
    }

    instruction = "Explain how StepExecutor works"

    if e2e_use_mock:
        with (
            patch("planner.planner.call_reasoning_model") as mock_planner,
            patch("agent.retrieval.query_rewriter.call_reasoning_model") as mock_rewriter,
            patch("agent.retrieval.query_rewriter.call_small_model") as mock_small,
            patch("agent.execution.step_dispatcher.call_reasoning_model") as mock_explain,
            patch("agent.execution.step_dispatcher.call_small_model") as mock_explain_small,
            patch("agent.retrieval.context_ranker.call_reasoning_model") as mock_ranker,
            patch("agent.retrieval.graph_retriever.retrieve_symbol_context", return_value=mock_search_results),
        ):
            mock_planner.return_value = '{"steps": [{"id": 1, "action": "SEARCH", "description": "Find StepExecutor", "reason": ""}, {"id": 2, "action": "EXPLAIN", "description": "Explain how StepExecutor works", "reason": ""}]}'
            mock_rewriter.return_value = '{"query": "StepExecutor", "tool": "find_symbol", "reason": ""}'
            mock_small.return_value = '{"query": "StepExecutor", "tool": "", "reason": ""}'
            mock_explain.return_value = "StepExecutor executes planner steps by dispatching each step to the appropriate tool."
            mock_explain_small.return_value = "StepExecutor executes planner steps."
            mock_ranker.return_value = "0.9\n0.8"

            result = run_controller(instruction, project_root=project_root)
    else:
        result = run_controller(instruction, project_root=project_root)

    assert "task_id" in result
    assert result.get("errors") == []
    assert result.get("completed_steps", 0) >= 2
    assert "errors" in result
    assert result["errors"] == []

    # Task memory saved
    task = load_task(result["task_id"], project_root=project_root)
    assert task is not None
    assert task.get("instruction") == instruction
    assert task.get("plan") is not None


def test_e2e_code_edit_add_logging(indexed_executor_repo, monkeypatch, e2e_use_mock):
    """Scenario 2: Code edit - plan -> search -> diff planner -> patch -> index update."""
    project_root = indexed_executor_repo
    monkeypatch.setenv("SERENA_PROJECT_DIR", project_root)
    instruction = "Add logging to StepExecutor.execute_step"

    executor_path = Path(project_root) / "agent" / "execution" / "executor.py"
    assert executor_path.exists()

    mock_search_results = {
        "results": [
            {"file": str(executor_path), "symbol": "execute_step", "line": 17, "snippet": "def execute_step"},
        ],
        "query": "StepExecutor execute_step",
    }

    if e2e_use_mock:
        with (
            patch("planner.planner.call_reasoning_model") as mock_planner,
            patch("agent.retrieval.query_rewriter.call_reasoning_model") as mock_rewriter,
            patch("agent.retrieval.query_rewriter.call_small_model") as mock_small,
            patch("agent.retrieval.context_ranker.call_reasoning_model") as mock_ranker,
            patch("agent.retrieval.graph_retriever.retrieve_symbol_context", return_value=mock_search_results),
            patch("editing.diff_planner.plan_diff") as mock_plan_diff,
        ):
            mock_planner.return_value = '{"steps": [{"id": 1, "action": "SEARCH", "description": "Find StepExecutor.execute_step", "reason": ""}, {"id": 2, "action": "EDIT", "description": "Add logging to StepExecutor.execute_step", "reason": ""}]}'
            mock_rewriter.return_value = '{"query": "StepExecutor execute_step", "tool": "find_symbol", "reason": ""}'
            mock_small.return_value = '{"query": "StepExecutor execute_step", "tool": "", "reason": ""}'
            mock_ranker.return_value = "0.9"
            mock_plan_diff.return_value = {
                "changes": [
                    {
                        "file": str(executor_path),
                        "symbol": "execute_step",
                        "action": "modify",
                        "patch": "logger.info('step executed')",
                        "reason": "Add logging",
                    },
                ],
            }
            result = run_controller(instruction, project_root=project_root)
    else:
        result = run_controller(instruction, project_root=project_root)

    assert result.get("errors") == []
    assert "task_id" in result

    # Index updated (no exception from update_index_for_file)
    index_path = Path(project_root) / ".symbol_graph" / "index.sqlite"
    assert index_path.exists()

    # Task memory saved
    task = load_task(result["task_id"], project_root=project_root)
    assert task is not None
    assert "files_modified" in task
    assert "patches" in task

    # Mock mode: strict assertions on patch content
    if e2e_use_mock:
        content = executor_path.read_text()
        assert "logger" in content or "logging" in content
        assert "step executed" in content
        assert len(task.get("files_modified", [])) >= 1


def test_e2e_multi_file_conflict_resolver(indexed_multi_executor_repo, monkeypatch, e2e_use_mock):
    """Scenario 3: Multi-file change - conflict resolver, sequential patch groups."""
    project_root = indexed_multi_executor_repo
    monkeypatch.setenv("SERENA_PROJECT_DIR", project_root)
    instruction = "Add logging to every executor class"

    path_a = Path(project_root) / "executors" / "executor_a.py"
    path_b = Path(project_root) / "executors" / "executor_b.py"
    assert path_a.exists() and path_b.exists()

    mock_search_results = {
        "results": [
            {"file": str(path_a), "symbol": "ExecutorA", "line": 4, "snippet": "class ExecutorA"},
            {"file": str(path_b), "symbol": "ExecutorB", "line": 4, "snippet": "class ExecutorB"},
        ],
        "query": "ExecutorA ExecutorB",
    }

    if e2e_use_mock:
        with (
            patch("planner.planner.call_reasoning_model") as mock_planner,
            patch("agent.retrieval.query_rewriter.call_reasoning_model") as mock_rewriter,
            patch("agent.retrieval.query_rewriter.call_small_model") as mock_small,
            patch("agent.retrieval.context_ranker.call_reasoning_model") as mock_ranker,
            patch("agent.retrieval.graph_retriever.retrieve_symbol_context", return_value=mock_search_results),
            patch("editing.diff_planner.plan_diff") as mock_plan_diff,
        ):
            mock_planner.return_value = '{"steps": [{"id": 1, "action": "SEARCH", "description": "Find all executor classes", "reason": ""}, {"id": 2, "action": "EDIT", "description": "Add logging to every executor class", "reason": ""}]}'
            mock_rewriter.return_value = '{"query": "ExecutorA ExecutorB", "tool": "find_symbol", "reason": ""}'
            mock_small.return_value = '{"query": "ExecutorA ExecutorB", "tool": "", "reason": ""}'
            mock_ranker.return_value = "0.9\n0.9"
            mock_plan_diff.return_value = {
                "changes": [
                    {
                        "file": str(path_a),
                        "symbol": "run",
                        "action": "modify",
                        "patch": "import logging\nlogger = logging.getLogger(__name__)\nlogger.info('ExecutorA.run')",
                        "reason": "Add logging to ExecutorA",
                    },
                    {
                        "file": str(path_b),
                        "symbol": "run",
                        "action": "modify",
                        "patch": "import logging\nlogger = logging.getLogger(__name__)\nlogger.info('ExecutorB.run')",
                        "reason": "Add logging to ExecutorB",
                    },
                ],
            }
            result = run_controller(instruction, project_root=project_root)
    else:
        result = run_controller(instruction, project_root=project_root)

    assert result.get("errors") == []
    assert "task_id" in result

    # Index updated for modified files
    index_path = Path(project_root) / ".symbol_graph" / "index.sqlite"
    assert index_path.exists()

    # Task memory saved
    task = load_task(result["task_id"], project_root=project_root)
    assert task is not None
    assert "files_modified" in task
    assert "patches" in task

    # Mock mode: strict assertions on patch content
    if e2e_use_mock:
        content_a = path_a.read_text()
        content_b = path_b.read_text()
        assert "logger" in content_a or "logging" in content_a
        assert "logger" in content_b or "logging" in content_b
        assert len(task.get("files_modified", [])) >= 2
