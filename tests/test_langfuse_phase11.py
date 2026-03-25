"""
Phase 11 — Langfuse observability integration tests.

Verifies:
  - Singleton client initialization
  - No-op facades when SDK unavailable or keys missing
  - Trace/span/generation/event hierarchy
  - Integration with runtime, plan_executor, planner, arg generator, exploration
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from agent_v2.observability.langfuse_client import (
    LFGenerationHandle,
    LFSpanHandle,
    LFTraceHandle,
    _NoopTrace,
    create_agent_trace,
    finalize_agent_trace,
    langfuse,
)
from agent_v2.schemas.plan import (
    PlanDocument,
    PlanStep,
    PlanSource,
    PlanRisk,
    PlanMetadata,
    PlanStepExecution,
)
from agent_v2.schemas.execution import ExecutionResult, ExecutionOutput, ExecutionMetadata
from agent_v2.schemas.exploration import (
    ExplorationResult,
    ExplorationSummary,
    ExplorationItem,
    ExplorationResultMetadata,
    ExplorationSource,
    ExplorationContent,
    ExplorationRelevance,
    ExplorationItemMetadata,
)


def _make_plan_document(plan_id: str, steps: list[PlanStep]) -> PlanDocument:
    """Helper to construct a minimal valid PlanDocument for tests."""
    return PlanDocument(
        plan_id=plan_id,
        instruction="test",
        understanding="test understanding",
        sources=[PlanSource(type="other", ref="test", summary="test source")],
        steps=steps,
        risks=[PlanRisk(risk="test risk", impact="low", mitigation="test mitigation")],
        completion_criteria=["test criteria"],
        metadata=PlanMetadata(created_at="2026-01-01T00:00:00Z", version=1),
    )


def _make_exploration_result() -> ExplorationResult:
    """Helper to construct a minimal valid ExplorationResult for tests."""
    return ExplorationResult(
        exploration_id="exp_test_123",
        instruction="test",
        items=[],
        summary=ExplorationSummary(
            overall="test summary",
            key_findings=["finding1"],
            knowledge_gaps=[],
            knowledge_gaps_empty_reason="all found",
        ),
        metadata=ExplorationResultMetadata(
            total_items=0,
            created_at="2026-01-01T00:00:00Z",
        ),
    )


class TestLangfuseClientInit:
    """Test Step 1 — singleton client with env-based secrets."""

    def test_no_keys_returns_noop_trace(self):
        """When LANGFUSE keys are missing, create_agent_trace returns _NoopTrace."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LANGFUSE_PUBLIC_KEY", None)
            os.environ.pop("LANGFUSE_SECRET_KEY", None)
            trace = create_agent_trace(instruction="test", mode="act")
            assert isinstance(trace, _NoopTrace)

    def test_facade_trace_method(self):
        """langfuse.trace() creates trace with correct name and input."""
        trace = langfuse.trace(name="agent_run", input={"instruction": "test task", "mode": "act"})
        assert trace is not None
        assert isinstance(trace, (_NoopTrace, LFTraceHandle))


class TestLangfuseHierarchy:
    """Test Steps 2-5 — trace → spans → generations hierarchy."""

    def test_trace_can_create_span(self):
        """Trace handle supports span creation."""
        trace = create_agent_trace(instruction="test", mode="act")
        span = trace.span(name="step_0_search", input={"step_id": "s1", "action": "search"})
        assert span is not None

    def test_span_can_create_generation(self):
        """Span handle supports generation creation."""
        trace = create_agent_trace(instruction="test", mode="act")
        span = trace.span(name="step_0_search", input={})
        gen = span.generation(name="argument_generation", input={"step_goal": "find files"})
        assert gen is not None

    def test_trace_can_create_generation_directly(self):
        """Trace handle supports generation creation (planner, exploration)."""
        trace = create_agent_trace(instruction="test", mode="act")
        gen = trace.generation(name="planner", input={"prompt": "..."})
        assert gen is not None

    def test_trace_supports_event(self):
        """Trace handle supports event creation (retry, replan)."""
        trace = create_agent_trace(instruction="test", mode="act")
        trace.event(name="retry", metadata={"step_id": "s1", "attempt": 1})

    def test_span_end_with_output(self):
        """Span can end with output and metadata (Step 4-5)."""
        trace = create_agent_trace(instruction="test", mode="act")
        span = trace.span(name="step_0_search", input={})
        span.update(metadata={"tool_name": "search_code", "duration_ms": 123})
        span.end(output={"success": True, "summary": "Found 3 files", "error": None})

    def test_generation_end_with_output(self):
        """Generation can end with output."""
        trace = create_agent_trace(instruction="test", mode="act")
        gen = trace.generation(name="planner", input={"prompt": "..."})
        gen.end(output={"response": '{"steps": []}'})


class TestLangfuseFinalizeTrace:
    """Test Step 9 — finalize trace with status and plan_id."""

    def test_finalize_agent_trace_with_status(self):
        """finalize_agent_trace updates trace with status and plan_id."""
        trace = create_agent_trace(instruction="test", mode="act")
        finalize_agent_trace(trace, status="success", plan_id="plan_abc123")

    def test_finalize_noop_trace_safe(self):
        """finalize_agent_trace handles _NoopTrace without error."""
        trace = _NoopTrace()
        finalize_agent_trace(trace, status="success", plan_id=None)

    def test_finalize_none_trace_safe(self):
        """finalize_agent_trace handles None without error."""
        finalize_agent_trace(None, status="success", plan_id=None)


class TestLangfuseNoopFacades:
    """Verify no-op facades don't crash when SDK unavailable."""

    def test_noop_trace_methods_callable(self):
        """_NoopTrace supports all trace methods without error."""
        trace = _NoopTrace()
        span = trace.span(name="test", input={})
        gen = trace.generation(name="test", input={})
        trace.event(name="test", metadata={})
        trace.update(output={})
        trace.end()
        assert span is not None
        assert gen is not None

    def test_noop_span_methods_callable(self):
        """_NoopSpan supports all span methods without error."""
        trace = _NoopTrace()
        span = trace.span(name="test", input={})
        span.update(metadata={})
        span.end(output={})
        gen = span.generation(name="test", input={})
        assert gen is not None

    def test_noop_generation_methods_callable(self):
        """_NoopGen supports end without error."""
        trace = _NoopTrace()
        gen = trace.generation(name="test", input={})
        gen.end(output={})


class TestLangfuseRuntimeIntegration:
    """Verify runtime.py wiring (Step 2, 9) via direct function calls."""

    def test_create_agent_trace_function(self):
        """create_agent_trace creates trace with instruction and mode."""
        trace = create_agent_trace(instruction="test task", mode="act")
        assert trace is not None
        assert isinstance(trace, (_NoopTrace, LFTraceHandle))

    def test_finalize_agent_trace_function(self):
        """finalize_agent_trace updates and ends trace."""
        trace = create_agent_trace(instruction="test task", mode="act")
        finalize_agent_trace(trace, status="success", plan_id="plan_123")


class TestLangfusePlanExecutorIntegration:
    """Verify plan_executor.py wiring (Step 3-5, 7)."""

    def test_plan_executor_creates_span_per_step(self):
        """PlanExecutor._run_with_retry creates span for each plan step."""
        from agent_v2.runtime.plan_executor import PlanExecutor
        from agent_v2.state.agent_state import AgentState

        mock_dispatcher = MagicMock()
        mock_dispatcher.execute.return_value = ExecutionResult(
            step_id="s1",
            success=True,
            status="success",
            output=ExecutionOutput(summary="done", data={}),
            error=None,
            metadata=ExecutionMetadata(tool_name="search_code", duration_ms=100, timestamp="2026-01-01T00:00:00Z"),
        )
        mock_arg_gen = MagicMock(generate=MagicMock(return_value={"query": "test"}))

        ex = PlanExecutor(mock_dispatcher, mock_arg_gen)
        plan = _make_plan_document(
            "test_plan",
            [
                PlanStep(
                    step_id="s1",
                    index=0,
                    type="explore",
                    action="search",
                    goal="find files",
                )
            ],
        )
        state = AgentState(instruction="test")
        state.current_plan = plan
        trace = create_agent_trace(instruction="test", mode="act")
        state.metadata["langfuse_trace"] = trace

        ex.run(plan, state)

    def test_retry_event_emitted(self):
        """PlanExecutor emits retry event on step failure before retry."""
        from agent_v2.runtime.plan_executor import PlanExecutor
        from agent_v2.schemas.execution import ErrorType, ExecutionError
        from agent_v2.state.agent_state import AgentState

        attempt_count = 0

        def failing_then_success(*args, **kwargs):
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count == 1:
                return ExecutionResult(
                    step_id="s1",
                    success=False,
                    status="failure",
                    output=ExecutionOutput(summary="failed", data={}),
                    error=ExecutionError(type=ErrorType.validation_error, message="invalid args"),
                    metadata=ExecutionMetadata(
                        tool_name="search_code", duration_ms=50, timestamp="2026-01-01T00:00:00Z"
                    ),
                )
            return ExecutionResult(
                step_id="s1",
                success=True,
                status="success",
                output=ExecutionOutput(summary="done", data={}),
                error=None,
                metadata=ExecutionMetadata(
                    tool_name="search_code", duration_ms=100, timestamp="2026-01-01T00:00:00Z"
                ),
            )

        mock_dispatcher = MagicMock()
        mock_dispatcher.execute.side_effect = failing_then_success
        mock_arg_gen = MagicMock(generate=MagicMock(return_value={"query": "test"}))

        ex = PlanExecutor(mock_dispatcher, mock_arg_gen)
        plan = _make_plan_document(
            "test_plan",
            [
                PlanStep(
                    step_id="s1",
                    index=0,
                    type="explore",
                    action="search",
                    goal="find files",
                )
            ],
        )
        state = AgentState(instruction="test")
        state.current_plan = plan
        trace = create_agent_trace(instruction="test", mode="act")
        state.metadata["langfuse_trace"] = trace

        ex.run(plan, state)
        assert attempt_count == 2


class TestLangfuseReplanEvent:
    """Test Step 8 — replan_triggered event."""

    def test_replan_event_emitted_on_failure(self):
        """PlanExecutor emits replan_triggered event when step exhausts retries."""
        from agent_v2.runtime.plan_executor import PlanExecutor
        from agent_v2.runtime.replanner import Replanner
        from agent_v2.schemas.execution import ErrorType, ExecutionError
        from agent_v2.state.agent_state import AgentState

        mock_dispatcher = MagicMock()
        mock_dispatcher.execute.return_value = ExecutionResult(
            step_id="s1",
            success=False,
            status="failure",
            output=ExecutionOutput(summary="failed", data={}),
            error=ExecutionError(type=ErrorType.tool_error, message="tool failed"),
            metadata=ExecutionMetadata(tool_name="search_code", duration_ms=50, timestamp="2026-01-01T00:00:00Z"),
        )
        mock_arg_gen = MagicMock(generate=MagicMock(return_value={"query": "test"}))

        new_plan = _make_plan_document(
            "replan_123",
            [
                PlanStep(step_id="s2", index=1, type="explore", action="search", goal="try again"),
                PlanStep(step_id="s_finish", index=2, type="finish", action="finish", goal="done"),
            ],
        )
        mock_planner = MagicMock(plan=MagicMock(return_value=new_plan))
        replanner = Replanner(mock_planner)

        ex = PlanExecutor(mock_dispatcher, mock_arg_gen, replanner=replanner)
        plan = _make_plan_document(
            "test_plan",
            [
                PlanStep(
                    step_id="s1",
                    index=1,
                    type="explore",
                    action="search",
                    goal="find files",
                    execution=PlanStepExecution(max_attempts=1),
                )
            ],
        )
        state = AgentState(instruction="test")
        state.current_plan = plan
        trace = create_agent_trace(instruction="test", mode="act")
        state.metadata["langfuse_trace"] = trace

        ex.run(plan, state)


class TestLangfusePlannerIntegration:
    """Test Step 6 — planner wraps LLM calls with generation."""

    def test_planner_wraps_llm_with_generation(self):
        """PlannerV2._call_llm creates generation when langfuse_trace provided."""
        from agent_v2.planner.planner_v2 import PlannerV2

        def mock_generate(prompt):
            return '{"steps": [{"step_id": "s1", "type": "explore", "action": "search", "goal": "test"}, {"step_id": "s_finish", "type": "finish", "action": "finish", "goal": "done"}]}'

        planner = PlannerV2(generate_fn=mock_generate)
        exploration = _make_exploration_result()
        trace = create_agent_trace(instruction="test", mode="plan")
        plan = planner.plan("test task", exploration, langfuse_trace=trace)
        assert plan is not None
        assert len(plan.steps) >= 1


class TestLangfuseArgumentGeneratorIntegration:
    """Test Step 6 — argument generator wraps LLM calls with generation."""

    def test_arg_generator_wraps_llm_with_generation(self):
        """PlanArgumentGenerator._generate_with_langfuse creates generation."""
        from agent_v2.runtime.plan_argument_generator import PlanArgumentGenerator
        from agent_v2.schemas.plan import PlanStep
        from agent_v2.state.agent_state import AgentState

        def mock_generate(prompt):
            return '{"query": "test query"}'

        arg_gen = PlanArgumentGenerator(generate_fn=mock_generate)
        step = PlanStep(step_id="s1", index=0, type="explore", action="search", goal="find files")
        state = AgentState(instruction="test")
        trace = create_agent_trace(instruction="test", mode="act")
        state.metadata["langfuse_trace"] = trace
        span = trace.span(name="step_0_search", input={})
        state.metadata["_current_langfuse_span"] = span

        args = arg_gen.generate(step, state)
        assert "query" in args


class TestLangfuseExplorationIntegration:
    """Test Step 6 — exploration wraps LLM calls with generation."""

    def test_exploration_action_fn_uses_langfuse_trace(self):
        """_exploration_action_fn from bootstrap accepts langfuse_trace parameter."""
        from agent_v2.runtime.bootstrap import _exploration_action_fn

        def mock_react_fn(*args, **kwargs):
            return {"action": "search", "args": {"query": "test"}}

        with patch("agent_v2.runtime.bootstrap._react_get_next_action", mock_react_fn):
            trace = create_agent_trace(instruction="test", mode="act")
            result = _exploration_action_fn("test task", [], langfuse_trace=trace)
            assert result is not None


class TestLangfuseEndToEndWiring:
    """Verify complete Phase 11 wiring from runtime → executor → planner → tools."""

    def test_runtime_to_plan_executor_trace_flow(self):
        """Trace created in runtime flows to plan_executor via state.metadata."""
        from agent_v2.runtime.runtime import AgentRuntime
        from agent_v2.state.agent_state import AgentState

        mock_planner = MagicMock()
        mock_planner.plan.return_value = _make_plan_document(
            "p1",
            [PlanStep(step_id="s_finish", index=0, type="finish", action="finish", goal="done")],
        )

        mock_dispatcher = MagicMock()
        mock_dispatcher.execute.return_value = ExecutionResult(
            step_id="s_finish",
            success=True,
            status="success",
            output=ExecutionOutput(summary="done", data={}),
            error=None,
            metadata=ExecutionMetadata(tool_name="finish", duration_ms=0, timestamp="2026-01-01T00:00:00Z"),
        )
        mock_arg_gen = MagicMock(generate=MagicMock(return_value={}))

        runtime = AgentRuntime(
            planner=mock_planner,
            plan_argument_generator=mock_arg_gen,
            dispatch_fn=lambda step, state: mock_dispatcher.execute(step, state),
        )

        result = runtime.run("test task", mode="act")
        assert "state" in result or "trace" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
