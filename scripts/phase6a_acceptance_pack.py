"""
Phase 6A — single-lane per task acceptance pack.

Produces a compact JSON report proving the frozen contract:
1) docs task stays docs-only end to end (no code retrieval path)
2) code task stays code-only end to end
3) mixed-lane plan is rejected before execution (validate_plan)
4) runtime lane violation is fatal (dispatch)
5) replan cannot switch dominant lane (replanner)
6) traces contain dominant lane + step lane fields (step_executed payload)

Default mode is deterministic/mocked to avoid external model dependencies.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from agent.memory.state import AgentState
from agent.observability.trace_logger import add_event_listener, finish_trace, log_event, remove_event_listener, start_trace
from agent.orchestrator.execution_loop import execution_loop
from agent.orchestrator.replanner import replan
from planner.planner_utils import normalize_actions, validate_plan


def run(project_root: str) -> dict[str, Any]:
    root = Path(project_root).resolve()

    # Minimal docs fixtures under a temp-like folder inside project_root.
    # Keep bounded and deterministic.
    fixtures_dir = root / ".phase6a_acceptance_fixtures"
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    (fixtures_dir / "README.md").write_text("# Project\n\nDocs README.\n", encoding="utf-8")
    (fixtures_dir / "docs").mkdir(exist_ok=True)
    (fixtures_dir / "docs" / "architecture.md").write_text("# Architecture\n\nDocs architecture.\n", encoding="utf-8")

    events: dict[str, list[dict[str, Any]]] = {}

    def listener(trace_id: str, event_type: str, payload: dict | None):
        events.setdefault(trace_id, []).append({"type": event_type, "payload": payload or {}})

    add_event_listener(listener)
    try:
        report: dict[str, Any] = {"phase": "6A", "project_root": str(root), "checks": {}}

        # (3) Planner-time: mixed-lane plan rejected by validate_plan
        mixed_plan = normalize_actions(
            {
                "steps": [
                    {"id": 1, "action": "SEARCH_CANDIDATES", "artifact_mode": "docs", "description": "Find docs", "reason": "r"},
                    {"id": 2, "action": "SEARCH", "description": "search code", "reason": "r"},
                ]
            }
        )
        report["checks"]["planner_mixed_lane_rejected"] = validate_plan(mixed_plan) is False

        # (4) Runtime: lane violation is fatal
        state_violation = AgentState(
            instruction="x",
            current_plan={"plan_id": "p", "steps": []},
            context={"project_root": str(root), "trace_id": None, "dominant_artifact_mode": "docs", "lane_violations": []},
        )
        from agent.execution.step_dispatcher import dispatch

        out = dispatch({"id": 1, "action": "SEARCH", "description": "find", "reason": "r", "artifact_mode": "docs"}, state_violation)
        report["checks"]["runtime_lane_violation_fatal"] = (
            out.get("classification") == "FATAL_FAILURE" and "lane_violation" in (out.get("error") or "")
        )

        # (1) Docs task stays docs-only end-to-end + (6) trace fields present
        docs_plan = {
            "plan_id": "plan_docs_6a",
            "steps": [
                {"id": 1, "action": "SEARCH_CANDIDATES", "artifact_mode": "docs", "description": "Find docs", "query": "readme docs", "reason": "r"},
                {"id": 2, "action": "BUILD_CONTEXT", "artifact_mode": "docs", "description": "Build docs context", "reason": "r"},
                {"id": 3, "action": "EXPLAIN", "artifact_mode": "docs", "description": "Explain docs", "reason": "r"},
            ],
        }
        trace_docs = start_trace(f"phase6a_docs_{uuid.uuid4().hex[:8]}", str(fixtures_dir), query="docs")
        state_docs = AgentState(
            instruction="docs",
            current_plan=docs_plan,
            context={
                "project_root": str(fixtures_dir),
                "trace_id": trace_docs,
                "tool_node": "START",
                "ranked_context": [],
                "dominant_artifact_mode": "docs",
                "lane_violations": [],
            },
        )
        # Ensure EXPLAIN is deterministic.
        from unittest.mock import patch

        with patch(
            "agent.execution.step_dispatcher.call_reasoning_model",
            return_value="This is a sufficiently long explanation output for acceptance validation.",
        ):
            execution_loop(state_docs, "docs", trace_id=trace_docs, log_event_fn=log_event)
        finish_trace(trace_docs)
        step_events_docs = [e for e in (events.get(trace_docs) or []) if e["type"] == "step_executed"]
        report["checks"]["docs_task_lane_consistent"] = (
            state_docs.context.get("dominant_artifact_mode") == "docs"
            and not state_docs.context.get("lane_violations")
            and all(ev["payload"].get("dominant_artifact_mode") == "docs" for ev in step_events_docs)
            and all(
                ev["payload"].get("step_artifact_mode") == "docs"
                for ev in step_events_docs
                if ev["payload"].get("action") in ("SEARCH_CANDIDATES", "BUILD_CONTEXT", "EXPLAIN")
            )
        )
        report["checks"]["trace_fields_present_docs"] = bool(step_events_docs) and all(
            "dominant_artifact_mode" in ev["payload"] and "step_artifact_mode" in ev["payload"] for ev in step_events_docs
        )

        # (2) Code task stays code-only end-to-end + trace fields present
        code_plan = {"plan_id": "plan_code_6a", "steps": [{"id": 1, "action": "EXPLAIN", "description": "Explain X", "reason": "r"}]}
        trace_code = start_trace(f"phase6a_code_{uuid.uuid4().hex[:8]}", str(root), query="code")
        state_code = AgentState(
            instruction="code",
            current_plan=code_plan,
            context={
                "project_root": str(root),
                "trace_id": trace_code,
                "tool_node": "START",
                "ranked_context": [{"file": "a.py", "symbol": "X", "snippet": "class X: pass"}],  # avoid context injection
                "dominant_artifact_mode": "code",
                "lane_violations": [],
            },
        )
        with patch("agent.execution.step_dispatcher.ensure_context_before_explain", return_value=(True, None)):
            with patch("agent.execution.step_dispatcher.call_reasoning_model", return_value="This is a sufficiently long explanation output for acceptance validation."):
                execution_loop(state_code, "code", trace_id=trace_code, log_event_fn=log_event)
        finish_trace(trace_code)
        step_events_code = [e for e in (events.get(trace_code) or []) if e["type"] == "step_executed"]
        report["checks"]["code_task_lane_consistent"] = (
            state_code.context.get("dominant_artifact_mode") == "code"
            and not state_code.context.get("lane_violations")
            and all(ev["payload"].get("dominant_artifact_mode") == "code" for ev in step_events_code)
            and all(ev["payload"].get("step_artifact_mode") is None for ev in step_events_code)  # EXPLAIN step omitted artifact_mode
        )
        report["checks"]["trace_fields_present_code"] = bool(step_events_code) and all(
            "dominant_artifact_mode" in ev["payload"] and "step_artifact_mode" in ev["payload"] for ev in step_events_code
        )

        # (5) Replan cannot switch dominant lane: dominant docs must not accept SEARCH/EDIT replans.
        state_replan = AgentState(
            instruction="docs",
            current_plan=docs_plan,
            context={"dominant_artifact_mode": "docs", "lane_violations": []},
        )
        bad_replanned = {"steps": [{"id": 1, "action": "SEARCH", "description": "x", "reason": "r"}]}
        with patch("agent.orchestrator.replanner.call_reasoning_model", return_value=json.dumps(bad_replanned)):
            with patch("agent.orchestrator.replanner.get_model_for_task") as _:
                new_plan = replan(state_replan, failed_step={"id": 1, "action": "SEARCH_CANDIDATES", "artifact_mode": "docs"}, error="boom")
        actions = [s.get("action") for s in (new_plan.get("steps") or []) if isinstance(s, dict)]
        report["checks"]["replan_cannot_switch_lane"] = actions == ["SEARCH_CANDIDATES", "BUILD_CONTEXT", "EXPLAIN"] and all(
            s.get("artifact_mode") == "docs" for s in (new_plan.get("steps") or []) if isinstance(s, dict)
        )

        report["pass"] = all(bool(v) for v in report["checks"].values())
        return report
    finally:
        remove_event_listener(listener)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-root", default=os.getcwd())
    ap.add_argument("--out", default="phase6a_acceptance_report.json")
    args = ap.parse_args()
    report = run(args.project_root)
    out_path = Path(args.out).resolve()
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(str(out_path))
    return 0 if report.get("pass") else 2


if __name__ == "__main__":
    raise SystemExit(main())

