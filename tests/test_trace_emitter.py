"""Phase 9 — TraceEmitter and structured Trace serialization."""

import json
import unittest

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
from agent_v2.runtime.trace_emitter import TraceEmitter, extract_target_from_plan_step


def _plan() -> PlanDocument:
    return PlanDocument(
        plan_id="p9",
        instruction="i",
        understanding="u",
        sources=[PlanSource(type="other", ref="r", summary="s")],
        steps=[
            PlanStep(
                step_id="a",
                index=1,
                type="explore",
                goal="g",
                action="search",
                inputs={"query": "foo"},
            ),
        ],
        risks=[PlanRisk(risk="r", impact="low", mitigation="m")],
        completion_criteria=["c"],
        metadata=PlanMetadata(created_at="2026-01-01T00:00:00Z", version=1),
    )


class TestTraceEmitter(unittest.TestCase):
    def test_extract_target_from_inputs(self):
        st = _plan().steps[0]
        self.assertEqual(extract_target_from_plan_step(st), "foo")

    def test_build_trace_success_and_json_roundtrip(self):
        em = TraceEmitter()
        plan = _plan()
        step = plan.steps[0]
        res = ExecutionResult(
            step_id=step.step_id,
            success=True,
            status="success",
            output=ExecutionOutput(summary="ok", data={}),
            error=None,
            metadata=ExecutionMetadata(tool_name="t", duration_ms=42, timestamp=""),
        )
        em.record_step(step, res, step.index)
        tr = em.build_trace("instr", plan.plan_id)
        self.assertEqual(tr.status, "success")
        self.assertEqual(tr.metadata.total_steps, 1)
        self.assertEqual(tr.metadata.total_duration_ms, 42)
        raw = tr.model_dump(mode="json")
        json.dumps(raw)
        self.assertEqual(raw["steps"][0]["action"], "search")

    def test_failure_trace_has_structured_error(self):
        em = TraceEmitter()
        plan = _plan()
        step = plan.steps[0]
        res = ExecutionResult(
            step_id=step.step_id,
            success=False,
            status="failure",
            output=ExecutionOutput(summary="bad", data={}),
            error=ExecutionError(type=ErrorType.not_found, message="missing"),
            metadata=ExecutionMetadata(tool_name="t", duration_ms=1, timestamp=""),
        )
        em.record_step(step, res, step.index)
        tr = em.build_trace("i", plan.plan_id)
        self.assertEqual(tr.status, "failure")
        self.assertIsNotNone(tr.steps[0].error)
        self.assertEqual(tr.steps[0].error.type, ErrorType.not_found)

    def test_read_snippet_trace_has_minimal_provenance(self):
        em = TraceEmitter()
        plan = _plan()
        step = plan.steps[0]
        res = ExecutionResult(
            step_id=step.step_id,
            success=True,
            status="success",
            output=ExecutionOutput(
                summary="read snippet ok",
                data={
                    "file_path": "/repo/a.py",
                    "symbol": "Foo.bar",
                    "content": "def bar():\n    return 1\n",
                    "mode": "symbol_body",
                },
            ),
            error=None,
            metadata=ExecutionMetadata(tool_name="read_snippet", duration_ms=5, timestamp=""),
        )
        em.record_step(step, res, step.index)
        tr = em.build_trace("instr", plan.plan_id)
        md = tr.steps[0].metadata
        self.assertEqual(md.get("tool_name"), "read_snippet")
        self.assertEqual(md.get("read_source"), "symbol")
        self.assertEqual(md.get("file"), "/repo/a.py")
        self.assertEqual(md.get("symbol"), "Foo.bar")
        self.assertTrue(isinstance(md.get("snippet_preview"), str) and md.get("snippet_preview"))


if __name__ == "__main__":
    unittest.main()
