"""
Phase 6 — Developer Workflow Tests.

Multi-turn scenarios, session memory, slash-command parser, and dev task structure.
"""

import json
from pathlib import Path

import pytest

from agent.cli.command_parser import parse_command, to_instruction_with_hint
from agent.memory.session_memory import SessionState, extract_symbols_from_context

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEV_TASKS_JSON = PROJECT_ROOT / "tests" / "dev_tasks.json"


class TestSessionMemory:
    """Session memory: conversation_history, recent_files, recent_symbols."""

    def test_add_turn_updates_conversation_history(self):
        session = SessionState()
        session.add_turn("Explain StepExecutor", summary="steps=2", task_id="t1")
        assert len(session.conversation_history) == 1
        assert session.conversation_history[0]["instruction"] == "Explain StepExecutor"
        assert session.conversation_history[0]["turn"] == 1

    def test_add_turn_updates_recent_files(self):
        session = SessionState()
        session.add_turn("", files_modified=["agent/execution/executor.py"])
        assert "agent/execution/executor.py" in session.recent_files

    def test_add_turn_updates_recent_symbols(self):
        session = SessionState()
        session.add_turn("", symbols_retrieved=["StepExecutor", "run_agent"])
        assert "StepExecutor" in session.recent_symbols
        assert "run_agent" in session.recent_symbols

    def test_to_context_dict_injects_session_data(self):
        session = SessionState()
        session.add_turn("explain X", files_modified=["a.py"], symbols_retrieved=["X"])
        ctx = session.to_context_dict()
        assert "session_recent_files" in ctx
        assert "session_recent_symbols" in ctx
        assert "a.py" in ctx["session_recent_files"]
        assert "X" in ctx["session_recent_symbols"]

    def test_extract_symbols_from_context(self):
        ctx = {
            "retrieved_symbols": ["StepExecutor", "run_agent"],
            "retrieved_references": ["patch_executor"],
        }
        symbols = extract_symbols_from_context(ctx)
        assert "StepExecutor" in symbols
        assert "run_agent" in symbols
        assert "patch_executor" in symbols


class TestCommandParser:
    """Slash-command parser: /explain, /fix, /refactor, /add-logging, /find."""

    def test_parse_explain(self):
        parsed = parse_command("/explain StepExecutor")
        assert parsed.intent == "EXPLAIN"
        assert "StepExecutor" in parsed.instruction

    def test_parse_fix(self):
        parsed = parse_command("/fix the null reference")
        assert parsed.intent == "EDIT"
        assert "Fix:" in parsed.instruction

    def test_parse_refactor(self):
        parsed = parse_command("/refactor extract helper")
        assert parsed.intent == "EDIT"
        assert "Refactor:" in parsed.instruction

    def test_parse_add_logging(self):
        parsed = parse_command("/add-logging")
        assert parsed.intent == "EDIT"
        assert "logging" in parsed.instruction.lower()

    def test_parse_find(self):
        parsed = parse_command("/find execute_step")
        assert parsed.intent == "SEARCH"
        assert "execute_step" in parsed.instruction

    def test_parse_plain_instruction(self):
        parsed = parse_command("Explain how the planner works")
        assert parsed.intent is None
        assert parsed.instruction == "Explain how the planner works"

    def test_to_instruction_with_hint_prepends_intent(self):
        parsed = parse_command("/explain StepExecutor")
        instr = to_instruction_with_hint(parsed)
        assert instr.startswith("[EXPLAIN]")
        assert "StepExecutor" in instr


class TestMultiTurnScenario:
    """Multi-turn flow: session state persists across turns."""

    def test_session_tracks_multiple_turns(self):
        session = SessionState()
        session.add_turn("Explain StepExecutor", summary="steps=2", task_id="t1")
        session.add_turn("Add logging", summary="steps=1", task_id="t2", files_modified=["executor.py"])
        assert len(session.conversation_history) == 2
        assert session.recent_files == ["executor.py"]


class TestDevTasksStructure:
    """Verify dev_tasks.json structure for workflow tests."""

    def test_dev_tasks_json_loads(self):
        assert DEV_TASKS_JSON.exists()
        with open(DEV_TASKS_JSON, encoding="utf-8") as f:
            tasks = json.load(f)
        assert isinstance(tasks, list)
        assert len(tasks) >= 5

    def test_dev_tasks_have_required_fields(self):
        with open(DEV_TASKS_JSON, encoding="utf-8") as f:
            tasks = json.load(f)
        for task in tasks[:5]:
            assert "id" in task
            assert "instruction" in task
            assert "category" in task or "group" in task


@pytest.mark.slow
class TestDeveloperWorkflowIntegration:
    """Integration: run_controller with simple tasks. Skip in fast CI."""

    def test_run_controller_explain_completes(self):
        """Run a simple explain task through run_controller."""
        from tests.utils.runtime_adapter import run_controller

        result = run_controller("Explain what AgentState contains", project_root=str(PROJECT_ROOT))
        assert "task_id" in result
        assert "completed_steps" in result
        assert result["completed_steps"] >= 0
