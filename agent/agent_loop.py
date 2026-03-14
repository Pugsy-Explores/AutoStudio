"""Minimal agent loop: instruction -> plan -> execute -> print results."""

import logging
import sys

from agent.executor import StepExecutor
from agent.state import AgentState

# So model call logs (INFO) are visible when run as __main__
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def run_loop(instruction: str) -> None:
    """Run plan for instruction and print step results."""
    from planner.planner import plan

    print("--- Plan ---")
    plan_result = plan(instruction)
    if isinstance(plan_result, dict):
        steps = plan_result.get("steps", [])
        print(f"steps: {len(steps)}")
        for s in steps:
            print(f"  {s.get('id')} [{s.get('action', '')}] {s.get('description', '')[:80]}{'...' if len(s.get('description', '')) > 80 else ''}")
    else:
        print(plan_result)
    print()

    state = AgentState(
        instruction=instruction,
        current_plan=plan_result,
        completed_steps=[],
        step_results=[],
        context={},
    )
    print("--- Execute ---")
    executor = StepExecutor()
    executor.execute_plan(plan_result, state)

    print("\n--- Results ---")
    for r in state.step_results:
        print(f"Step {r.step_id} [{r.action}] success={r.success} latency={r.latency_seconds:.3f}s")
        if r.error:
            print(f"  error: {r.error}")
    # Step output is printed as each step completes in the executor loop above.


if __name__ == "__main__":
    if len(sys.argv) > 1:
        instruction = " ".join(sys.argv[1:])
    else:
        instruction = input("Instruction: ").strip()
    if not instruction:
        print("No instruction provided.")
        sys.exit(1)
    run_loop(instruction)
