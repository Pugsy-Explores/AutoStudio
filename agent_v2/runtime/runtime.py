"""Standalone runtime composition root for agent_v2."""
# DO NOT import from agent.* here

import uuid
from typing import Any

from agent_v2.runtime.action_generator import ActionGenerator
from agent_v2.runtime.agent_loop import AgentLoop
from agent_v2.runtime.dispatcher import Dispatcher
from agent_v2.runtime.exploration_runner import ExplorationRunner
from agent_v2.runtime.mode_manager import ModeManager
from agent_v2.runtime.observation_builder import ObservationBuilder
from agent_v2.runtime.dag_executor import DagExecutor
from agent_v2.config import get_agent_v2_episodic_log_dir
from agent_v2.schemas.policies import ExecutionPolicy
from agent_v2.runtime.validator import Validator
from agent_v2.state.agent_state import AgentState
from agent_v2.observability.langfuse_client import create_agent_trace, finalize_agent_trace
from agent_v2.observability.graph_builder import build_graph
from agent_v2.observability.observability_context import ObservabilityContext


def normalize_run_result(mgr_out: Any, state: AgentState) -> dict[str, Any]:
    """
    Phase 10 — stable CLI/API shape: status, trace, state (and downstream format_output adds result/plan).
    Phase 12 — adds graph projection layer.
    """
    if isinstance(mgr_out, dict) and "trace" in mgr_out:
        trace_obj = mgr_out.get("trace")
        graph_obj = None
        if trace_obj is not None:
            try:
                graph_obj = build_graph(trace_obj).model_dump()
            except Exception:
                pass
        return {
            "status": mgr_out.get("status", "unknown"),
            "trace": trace_obj,
            "graph": graph_obj,
            "state": mgr_out.get("state", state),
        }
    if isinstance(mgr_out, AgentState):
        trace_obj = None
        md = getattr(mgr_out, "metadata", None)
        if isinstance(md, dict):
            trace_obj = md.get("trace")
        graph_obj = None
        if trace_obj is not None:
            try:
                graph_obj = build_graph(trace_obj).model_dump()
            except Exception:
                pass
        return {"status": "plan_ready", "trace": trace_obj, "graph": graph_obj, "state": mgr_out}
    if isinstance(mgr_out, dict) and "state" in mgr_out:
        trace_obj = mgr_out.get("trace")
        graph_obj = None
        if trace_obj is not None:
            try:
                graph_obj = build_graph(trace_obj).model_dump()
            except Exception:
                pass
        return {
            "status": mgr_out.get("status", "unknown"),
            "trace": trace_obj,
            "graph": graph_obj,
            "state": mgr_out["state"],
        }
    return {"status": "unknown", "trace": None, "graph": None, "state": mgr_out}


class AgentRuntime:
    def __init__(
        self,
        planner,
        *,
        action_fn=None,
        validate_fn=None,
        dispatch_fn=None,
        exploration_fn=None,
        plan_argument_generator=None,
        replanner=None,
        execution_policy: ExecutionPolicy | None = None,
        exploration_llm_fn=None,
        model_name: str | None = None,
    ):
        dispatcher = Dispatcher(execute_fn=dispatch_fn)
        self.dispatcher = dispatcher

        self.plan_executor = None
        if plan_argument_generator is not None:
            self.plan_executor = DagExecutor(
                dispatcher,
                plan_argument_generator,
                replanner=replanner,
                policy=execution_policy,
                trace_log_dir=get_agent_v2_episodic_log_dir(),
            )

        action_generator = ActionGenerator(
            fn=action_fn,
            exploration_fn=exploration_fn,
        )

        self.loop = AgentLoop(
            dispatcher=dispatcher,
            validator=Validator(validate_fn=validate_fn),
            action_generator=action_generator,
            observation_builder=ObservationBuilder(),
        )

        self.exploration_runner = ExplorationRunner(
            action_generator=action_generator,
            dispatcher=dispatcher,
            llm_generate_fn=exploration_llm_fn,
            model_name=model_name,
        )

        self.mode_manager = ModeManager(
            self.exploration_runner,
            planner,
            self.plan_executor,
            loop=self.loop,
        )

    def run(self, instruction: str, mode: str = "act"):
        state = AgentState(instruction=instruction)
        state.metadata["runtime"] = "agent_v2"
        state.metadata["mode"] = mode
        state.context["react_mode"] = mode in ("act", "plan_execute")
        if mode in ("plan", "deep_plan"):
            state.context["plan_safe_execute"] = True
        else:
            state.context.pop("plan_safe_execute", None)
        lf_trace = create_agent_trace(instruction=instruction, mode=mode)
        state.metadata["langfuse_trace"] = lf_trace
        state.metadata["obs"] = ObservabilityContext(langfuse_trace=lf_trace, owns_root=False)
        run_status = "unknown"
        plan_id_out: str | None = None
        try:
            mgr_out = self.mode_manager.run(state, mode)
            out = normalize_run_result(mgr_out, state)
            run_status = str(out.get("status", "unknown"))
            if isinstance(mgr_out, dict) and mgr_out.get("trace") is not None:
                tr = mgr_out["trace"]
                plan_id_out = getattr(tr, "plan_id", None)
            elif getattr(state, "current_plan", None) is not None and isinstance(
                state.current_plan, dict
            ):
                plan_id_out = state.current_plan.get("plan_id")
            return out
        finally:
            if run_status == "unknown":
                # Legacy plan-only modes used plan_ready when no executor trace; iterative plan/deep_plan
                # return ACT-style status from normalize_run_result like act.
                run_status = "plan_ready" if mode == "plan_legacy" else "unknown"
            lf_fin = state.metadata.get("langfuse_trace")
            if lf_fin is None and state.metadata.get("obs") is not None:
                lf_fin = getattr(state.metadata["obs"], "langfuse_trace", None)
            finalize_agent_trace(
                lf_fin,
                status=run_status,
                plan_id=plan_id_out,
            )

    def explore(self, instruction: str):
        """
        Run the bounded exploration phase and return an ExplorationResult.

        This is the Phase 3 entry point: runs before planning, produces grounded
        context for the planner. Isolated from the main agent loop.

        Observability: there is no planner ``plan_id`` on this path; finalize uses
        an exploration correlation id (Langfuse ``trace_id`` when available) so
        root trace output is not null.
        """
        lf = create_agent_trace(instruction=instruction, mode="explore")
        obs = ObservabilityContext(langfuse_trace=lf, owns_root=False)
        tid = getattr(lf, "trace_id", None)
        explore_correlation = (
            f"explore_{tid}" if isinstance(tid, str) and tid.strip() else f"explore_{uuid.uuid4().hex[:12]}"
        )
        try:
            return self.exploration_runner.run(instruction, obs=obs)
        finally:
            finalize_agent_trace(lf, status="explore_done", plan_id=explore_correlation)
