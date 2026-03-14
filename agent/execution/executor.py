"""StepExecutor: interpret planner steps and call tool adapters via dispatcher."""

import json
import logging
import time

from agent.execution.step_dispatcher import dispatch
from agent.memory.state import AgentState
from agent.memory.step_result import StepResult

logger = logging.getLogger(__name__)


class StepExecutor:
    """Execute planner steps sequentially; map actions via step_dispatcher."""

    def execute_step(self, step: dict, state: AgentState) -> StepResult:
        """Run a single step; return StepResult with latency and output/error."""
        print("[workflow] executor")
        step_id = step.get("id", 0)
        action = step.get("action", "EXPLAIN")
        start = time.perf_counter()
        try:
            raw = dispatch(step, state)
            elapsed = time.perf_counter() - start
            return StepResult(
                step_id=step_id,
                action=action,
                success=raw.get("success", True),
                output=raw.get("output", ""),
                latency_seconds=elapsed,
                error=raw.get("error"),
            )
        except Exception as e:
            elapsed = time.perf_counter() - start
            return StepResult(
                step_id=step_id,
                action=action,
                success=False,
                output="",
                latency_seconds=elapsed,
                error=str(e),
            )

    def execute_plan(self, plan: dict, state: AgentState) -> list[StepResult]:
        """Run all steps in order; append results to state; return step_results."""
        steps = plan.get("steps", [])
        for step in steps:
            result = self.execute_step(step, state)
            state.record(step, result)
            _print_step_result(result)
        return state.step_results


def _print_step_result(r: StepResult, max_output_len: int = 500) -> None:
    """Print one step's summary and output to stdout."""
    print(f"Step {r.step_id} [{r.action}] success={r.success} latency={r.latency_seconds:.3f}s")
    if r.error:
        print(f"  error: {r.error}")
    out = r.output
    if out is not None and out != "":
        if isinstance(out, dict):
            out_str = json.dumps(out, default=str)[:max_output_len]
            if len(json.dumps(out, default=str)) > max_output_len:
                out_str += "..."
            print(f"  output: {out_str}")
        else:
            s = str(out)
            print(f"  output: {s[:max_output_len]}{'...' if len(s) > max_output_len else ''}")
