#!/usr/bin/env python3
"""Emit sample Trace + ExecutionGraph JSON for Phase 13 (LLM nodes). Run from repo root: python scripts/print_phase13_sample.py"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_v2.observability.graph_builder import build_graph
from agent_v2.runtime.trace_emitter import TraceEmitter
from agent_v2.schemas.execution import (
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
    PlanStepExecution,
)


def main() -> None:
    em = TraceEmitter()
    em.reset()
    em.record_llm(
        task_name="PLANNER_DECISION_ACT",
        prompt="Plan a fix for the auth module.",
        output_text='{"steps":[{"action":"search"}]}',
        latency_ms=120,
        system_prompt="You are a planner.",
        model="reasoning-model",
    )
    plan = PlanDocument(
        plan_id="sample-p13",
        instruction="fix auth",
        understanding="u",
        sources=[PlanSource(type="other", ref="r", summary="s")],
        steps=[
            PlanStep(
                step_id="step-search",
                index=1,
                type="explore",
                goal="find code",
                action="search",
                inputs={"query": "auth"},
                execution=PlanStepExecution(),
            ),
        ],
        risks=[PlanRisk(risk="r", impact="low", mitigation="m")],
        completion_criteria=["c"],
        metadata=PlanMetadata(created_at="2026-01-01T00:00:00Z", version=1),
    )
    st = plan.steps[0]
    em.record_step(
        st,
        ExecutionResult(
            step_id=st.step_id,
            success=True,
            status="success",
            output=ExecutionOutput(summary="ok", data={}),
            error=None,
            metadata=ExecutionMetadata(tool_name="search", duration_ms=45, timestamp=""),
        ),
        st.index,
    )
    trace = em.build_trace("fix auth", plan.plan_id)
    graph = build_graph(trace, plan=plan)

    print("=== Trace (JSON) ===")
    print(json.dumps(trace.model_dump(mode="json"), indent=2))
    print("\n=== ExecutionGraph (JSON) ===")
    print(json.dumps(graph.model_dump(mode="json"), indent=2))


if __name__ == "__main__":
    main()
