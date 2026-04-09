"""Phase 13 — LLM TraceStep interleaved with tool steps; graph + JSON serialization."""

from __future__ import annotations

import json
import unittest

from agent_v2.observability.graph_builder import build_graph
from agent_v2.runtime.trace_context import clear_active_trace_emitter, set_active_trace_emitter
from agent_v2.runtime.trace_emitter import TraceEmitter
from agent_v2.schemas.execution import (
    ErrorType,
    ExecutionError,
    ExecutionMetadata,
    ExecutionOutput,
    ExecutionResult,
)
from agent_v2.schemas.plan import (
    PlanDocument,
    PlanMetadata,
    PlanRisk,
    PlanSource,
    PlanStep,
)
from agent_v2.schemas.trace import Trace, TraceMetadata, TraceStep
from agent.models import model_client


def _plan_step() -> PlanStep:
    return PlanStep(
        step_id="tool-a",
        index=1,
        type="explore",
        goal="g",
        action="search",
        inputs={"query": "q"},
    )


def _plan() -> PlanDocument:
    return PlanDocument(
        plan_id="p13",
        instruction="i",
        understanding="u",
        sources=[PlanSource(type="other", ref="r", summary="s")],
        steps=[_plan_step()],
        risks=[PlanRisk(risk="r", impact="low", mitigation="m")],
        completion_criteria=["c"],
        metadata=PlanMetadata(created_at="2026-01-01T00:00:00Z", version=1),
    )


class TestLlmTraceEmitter(unittest.TestCase):
    def tearDown(self) -> None:
        clear_active_trace_emitter()

    def test_record_llm_then_tool_order_preserved(self) -> None:
        em = TraceEmitter()
        em.record_llm(
            task_name="PLANNER_DECISION_ACT",
            prompt="hello " * 100,
            output_text='{"ok": true}',
            latency_ms=12,
            system_prompt="sys",
            model="test-model",
        )
        plan = _plan()
        step = plan.steps[0]
        res = ExecutionResult(
            step_id=step.step_id,
            success=True,
            status="success",
            output=ExecutionOutput(summary="ok", data={}),
            error=None,
            metadata=ExecutionMetadata(tool_name="t", duration_ms=5, timestamp=""),
        )
        em.record_step(step, res, step.index)
        tr = em.build_trace("instr", plan.plan_id)
        self.assertEqual(len(tr.steps), 2)
        self.assertEqual(tr.steps[0].kind, "llm")
        self.assertEqual(tr.steps[0].action, "PLANNER_DECISION_ACT")
        self.assertEqual(tr.steps[1].kind, "tool")
        self.assertEqual(tr.steps[1].action, "search")
        self.assertLessEqual(len(tr.steps[0].input.get("prompt", "")), 9000)

    def test_trace_status_ignores_llm_for_success(self) -> None:
        em = TraceEmitter()
        em.record_llm(
            task_name="X",
            prompt="p",
            output_text="o",
            latency_ms=1,
        )
        plan = _plan()
        step = plan.steps[0]
        res = ExecutionResult(
            step_id=step.step_id,
            success=False,
            status="failure",
            output=ExecutionOutput(summary="bad", data={}),
            error=ExecutionError(type=ErrorType.tool_error, message="e"),
            metadata=ExecutionMetadata(tool_name="t", duration_ms=1, timestamp=""),
        )
        em.record_step(step, res, step.index)
        tr = em.build_trace("i", plan.plan_id)
        self.assertEqual(tr.status, "failure")

    def test_model_client_emits_when_context_set(self) -> None:
        em = TraceEmitter()
        set_active_trace_emitter(em)
        self.addCleanup(clear_active_trace_emitter)
        orig = model_client._call_chat

        def fake_chat(*_a, **_k):
            return "model-out"

        model_client._call_chat = fake_chat  # type: ignore[assignment]
        self.addCleanup(lambda: setattr(model_client, "_call_chat", orig))
        try:
            out = model_client.call_reasoning_model("user-prompt", task_name="T_UNIT", system_prompt=None)
        finally:
            model_client._call_chat = orig  # type: ignore[assignment]

        self.assertEqual(out, "model-out")
        tr = em.build_trace("instr", "pid")
        self.assertEqual(len(tr.steps), 1)
        self.assertEqual(tr.steps[0].kind, "llm")
        self.assertEqual(tr.steps[0].action, "T_UNIT")

    def test_trace_json_roundtrip(self) -> None:
        steps = [
            TraceStep(
                step_id="llm-1",
                plan_step_index=0,
                action="PLANNER_TOOL_ARGS_ACT",
                target="m",
                success=True,
                error=None,
                duration_ms=3,
                kind="llm",
                input={"prompt": "p"},
                output={"text": "t"},
                metadata={"task_name": "PLANNER_TOOL_ARGS_ACT", "model": "m", "latency_ms": 3},
            ),
            TraceStep(
                step_id="s1",
                plan_step_index=1,
                action="search",
                target="q",
                success=True,
                error=None,
                duration_ms=10,
                kind="tool",
            ),
        ]
        tr = Trace(
            trace_id="tid",
            instruction="instr",
            plan_id="pid",
            steps=steps,
            status="success",
            metadata=TraceMetadata(total_steps=2, total_duration_ms=13),
        )
        raw = tr.model_dump(mode="json")
        json.dumps(raw)
        tr2 = Trace.model_validate(raw)
        self.assertEqual(tr2.steps[0].kind, "llm")
        self.assertEqual(tr2.steps[1].kind, "tool")


class TestLlmGraphBuilder(unittest.TestCase):
    def test_mixed_llm_tool_graph(self) -> None:
        trace = Trace(
            trace_id="t",
            instruction="i",
            plan_id="p",
            steps=[
                TraceStep(
                    step_id="l1",
                    plan_step_index=0,
                    action="EXPLORATION",
                    target="",
                    success=True,
                    error=None,
                    duration_ms=20,
                    kind="llm",
                    input={"prompt": "x"},
                    output={"text": "y"},
                    metadata={"task_name": "EXPLORATION", "latency_ms": 20},
                ),
                TraceStep(
                    step_id="s1",
                    plan_step_index=1,
                    action="search",
                    target="q",
                    success=True,
                    error=None,
                    duration_ms=5,
                    kind="tool",
                ),
            ],
            status="success",
            metadata=TraceMetadata(total_steps=2, total_duration_ms=25),
        )
        g = build_graph(trace)
        self.assertEqual(len(g.nodes), 2)
        self.assertEqual(g.nodes[0].type, "llm")
        self.assertEqual(g.nodes[1].type, "step")
        self.assertEqual(len(g.edges), 1)
        self.assertEqual(g.edges[0].source, "l1")
        self.assertEqual(g.edges[0].target, "s1")
        self.assertEqual(g.model_dump(mode="json")["nodes"][0]["type"], "llm")


if __name__ == "__main__":
    unittest.main()
