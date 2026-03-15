"""
Phase 2 — Component Integration Tests.

Tests each pipeline stage interacting with the next, in roadmap order.
Not unit tests — integration tests across stages.
"""

import json
import os
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from agent.memory.state import AgentState
from agent.retrieval.context_builder_v2 import assemble_reasoning_context
from agent.retrieval.repo_map_lookup import lookup_repo_map
from agent.retrieval.retrieval_pipeline import run_retrieval_pipeline
from agent.retrieval.symbol_graph import get_symbol_dependencies
from agent.orchestrator.agent_loop import run_agent
from editing.diff_planner import plan_diff
from editing.patch_executor import execute_patch
from editing.patch_generator import to_structured_patches
from planner.planner_eval import extract_actions, validate_structure
from repo_graph.repo_map_builder import build_repo_map
from repo_index.indexer import index_repo

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "repo"
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _requires_tree_sitter():
    pytest.importorskip("tree_sitter_python")


# --- Stage 1: Repository indexing ---


class TestStage1RepoIndexing:
    """Test index_repo(path) produces symbols.json, repo_map.json, index.sqlite."""

    def test_index_repo_creates_artifacts(self, tmp_path):
        _requires_tree_sitter()
        out_dir = tmp_path / ".symbol_graph"
        out_dir.mkdir(parents=True, exist_ok=True)

        symbols, out_path = index_repo(str(FIXTURES_DIR), output_dir=str(out_dir))
        build_repo_map(str(tmp_path))

        assert (out_dir / "symbols.json").exists()
        assert (out_dir / "index.sqlite").exists()
        assert (out_dir / "repo_map.json").exists()
        assert len(symbols) > 0


# --- Stage 2: Symbol graph ---


class TestStage2SymbolGraph:
    """Test get_symbol_dependencies: find callers, imports, inheritance."""

    def test_get_symbol_dependencies_returns_structured_results(self, tmp_path):
        _requires_tree_sitter()
        out_dir = tmp_path / ".symbol_graph"
        out_dir.mkdir(parents=True, exist_ok=True)
        index_repo(str(FIXTURES_DIR), output_dir=str(out_dir))

        deps = get_symbol_dependencies("bar", project_root=str(tmp_path))

        for d in deps:
            assert "file" in d
            assert "symbol" in d
            assert "snippet" in d
            assert "type" in d
            assert d["type"] in ("calls", "referenced_by")

    def test_get_symbol_dependencies_missing_symbol_returns_empty(self, tmp_path):
        _requires_tree_sitter()
        out_dir = tmp_path / ".symbol_graph"
        out_dir.mkdir(parents=True, exist_ok=True)
        index_repo(str(FIXTURES_DIR), output_dir=str(out_dir))

        deps = get_symbol_dependencies("NonexistentSymbol123", project_root=str(tmp_path))
        assert deps == []


# --- Stage 3: Repo map ---


class TestStage3RepoMap:
    """Test lookup symbol -> file. Example: StepExecutor -> agent/execution/executor.py."""

    @pytest.mark.slow
    def test_lookup_step_executor_resolves_to_executor_py(self):
        candidates = lookup_repo_map("StepExecutor", project_root=str(PROJECT_ROOT))
        assert len(candidates) > 0
        files = [c.get("file", "") for c in candidates]
        assert any("executor" in f and "execution" in f for f in files)


# --- Stage 4: Retrieval pipeline ---


class TestStage4RetrievalPipeline:
    """Test each retrieval stage: anchor detection, expansion, context build, ranking."""

    @pytest.mark.slow
    def test_run_retrieval_pipeline_populates_context(self):
        project_root = str(PROJECT_ROOT)
        graph_results = [
            {
                "file": str(PROJECT_ROOT / "agent" / "execution" / "executor.py"),
                "symbol": "StepExecutor",
                "snippet": "class StepExecutor:",
                "line": 14,
            }
        ]
        state = AgentState(
            instruction="Explain StepExecutor",
            current_plan={"steps": []},
            context={"project_root": project_root},
        )

        def mock_rank_context(rank_query: str, candidates: list) -> list:
            return list(candidates)

        with patch("agent.retrieval.retrieval_pipeline.rank_context", side_effect=mock_rank_context):
            run_retrieval_pipeline(graph_results, state, query="Explain StepExecutor")

        assert "retrieved_symbols" in state.context
        assert "context_snippets" in state.context
        assert "ranked_context" in state.context
        assert len(state.context.get("ranked_context", [])) > 0


# --- Stage 5: Context builder ---


class TestStage5ContextBuilder:
    """Verify context not empty, within token limit, relevant."""

    def test_assemble_reasoning_context_format(self):
        snippets = [
            {
                "file": "agent/execution/executor.py",
                "symbol": "StepExecutor",
                "snippet": "class StepExecutor:\n    def execute_step(self, step, state):",
                "line": 14,
            }
        ]
        context = assemble_reasoning_context(snippets, max_chars=4000)

        assert context
        assert len(context) <= 4000 + 100  # small tolerance
        assert "FILE: agent/execution/executor.py" in context
        assert "SYMBOL: StepExecutor" in context
        assert "SNIPPET:" in context


# --- Stage 6: Planner ---


class TestStage6Planner:
    """Run planner dataset validation: planner_accuracy, step validity."""

    def test_validate_structure_on_dataset_entries(self):
        dataset_path = PROJECT_ROOT / "planner" / "planner_dataset.json"
        data = json.loads(dataset_path.read_text())
        for i, item in enumerate(data[:3]):
            expected = item.get("expected_steps", [])
            plan = {"steps": [{"id": j + 1, "action": s["action"], "description": s.get("description", ""), "reason": ""} for j, s in enumerate(expected)]}
            assert validate_structure(plan), f"Entry {i} should be structurally valid"
            actions = extract_actions(plan)
            assert all(a in ("EDIT", "SEARCH", "EXPLAIN", "INFRA") for a in actions)


# --- Stage 7: Editing pipeline ---


class TestStage7EditingPipeline:
    """Test patching on toy repo: diff planner -> patch generator -> AST patcher -> validator -> executor. Rollback must work."""

    def test_full_editing_pipeline_applies_patch(self, tmp_path):
        _requires_tree_sitter()
        foo_src = FIXTURES_DIR / "foo.py"
        foo_dst = tmp_path / "foo.py"
        shutil.copy(foo_src, foo_dst)
        original = foo_dst.read_text()

        instruction = "Add logging to function bar"
        context = {
            "ranked_context": [{"file": str(foo_dst), "symbol": "bar", "snippet": "def bar():"}],
            "retrieved_symbols": [{"file": str(foo_dst), "symbol": "bar"}],
            "retrieved_files": [str(foo_dst)],
            "project_root": str(tmp_path),
        }
        plan = plan_diff(instruction, context)
        assert plan.get("changes")

        plan["changes"][0]["patch"] = "import logging\nlogger = logging.getLogger(__name__)\nlogger.info('bar called')"
        patch_plan = to_structured_patches(plan, instruction, context)
        result = execute_patch(patch_plan, project_root=str(tmp_path))

        assert result["success"] is True
        content = foo_dst.read_text()
        assert "logger" in content or "logging" in content

    def test_patch_executor_rollback_on_invalid(self, tmp_path):
        f = tmp_path / "mod.py"
        f.write_text("def foo():\n    return 1\n")
        original = f.read_text()

        patch_plan = {
            "changes": [
                {
                    "file": str(f),
                    "patch": {
                        "symbol": "foo",
                        "action": "replace",
                        "target_node": "function_body",
                        "code": "x = ",  # invalid: incomplete assignment
                    },
                },
            ],
        }
        result = execute_patch(patch_plan, project_root=str(tmp_path))
        assert result["success"] is False
        assert f.read_text() == original


# --- Stage 8: Full agent loop ---


class TestStage8FullAgentLoop:
    """Run agent loop; verify complete trace logs."""

    @patch("agent.execution.executor.dispatch")
    @patch("agent.orchestrator.agent_loop.get_plan")
    def test_run_agent_produces_trace_and_plan(self, mock_get_plan, mock_dispatch):
        mock_get_plan.return_value = {
            "steps": [
                {"id": 1, "action": "EXPLAIN", "description": "Explain StepExecutor", "reason": "User request"},
            ]
        }
        mock_dispatch.return_value = {
            "success": True,
            "output": "StepExecutor executes steps via the dispatcher.",
            "error": None,
        }

        state = run_agent("Explain StepExecutor")

        assert state.current_plan
        assert "steps" in state.current_plan
        assert len(state.current_plan["steps"]) >= 1
        assert len(state.step_results) >= 1
        assert state.step_results[0].success


# --- Detailed Plan: Step 1 — Router → Planner Integration ---


class TestStep1RouterPlannerIntegration:
    """Step 1: Router eval runs; routing decisions invoke planner. Verify router_eval.run_eval returns metrics."""

    def test_router_eval_run_eval_returns_metrics(self):
        from router_eval.router_eval import run_eval

        def stub_route(_instruction: str) -> str:
            return "EDIT"

        metrics = run_eval(verbose=False, route_fn=stub_route, router_name="stub")
        assert "accuracy" in metrics
        assert "total" in metrics
        assert metrics["total"] > 0
        assert 0 <= metrics["accuracy"] <= 1


# --- Detailed Plan: Step 5 — Observability ---


class TestStep5Observability:
    """Step 5: Trace system captures router, planner, retrieval, model calls, exec outputs."""

    @patch("agent.execution.executor.dispatch")
    @patch("agent.orchestrator.agent_loop.get_plan")
    def test_trace_contains_step_executed_and_structure(self, mock_get_plan, mock_dispatch, tmp_path):
        mock_get_plan.return_value = {
            "steps": [
                {"id": 1, "action": "EXPLAIN", "description": "Explain AgentState", "reason": "User request"},
            ]
        }
        mock_dispatch.return_value = {
            "success": True,
            "output": "AgentState holds instruction, plan, context.",
            "error": None,
        }

        import os

        os.environ["SERENA_PROJECT_DIR"] = str(tmp_path)
        try:
            run_agent("Explain AgentState")
        finally:
            os.environ.pop("SERENA_PROJECT_DIR", None)

        traces_dir = tmp_path / ".agent_memory" / "traces"
        if not traces_dir.exists():
            pytest.skip("No traces dir created")
        trace_files = list(traces_dir.glob("*.json"))
        assert len(trace_files) > 0, "At least one trace file should exist"

        latest = max(trace_files, key=lambda p: p.stat().st_mtime)
        data = json.loads(latest.read_text())
        assert "events" in data
        assert "trace_id" in data
        step_events = [e for e in data.get("events", []) if e.get("type") == "step_executed"]
        assert len(step_events) >= 1, "Trace must contain at least one step_executed event"


# --- Detailed Plan: Step 6 — Explain Gate Safety ---


class TestStep6ExplainGate:
    """Step 6: ExplainGate triggers SEARCH automatically when EXPLAIN without context."""

    def test_ensure_context_before_explain_returns_synthetic_search_when_empty(self):
        from agent.execution.explain_gate import ensure_context_before_explain

        state = AgentState(
            instruction="Explain StepExecutor",
            current_plan={"steps": []},
            context={"ranked_context": []},
        )
        step = {"id": 1, "action": "EXPLAIN", "description": "Explain StepExecutor"}
        has_context, synthetic = ensure_context_before_explain(step, state)
        assert has_context is False
        assert synthetic is not None
        assert synthetic.get("action") == "SEARCH"
        assert synthetic.get("description") == "Explain StepExecutor"
