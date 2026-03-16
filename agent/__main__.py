"""Entry point for python -m agent. Delegates to run_agent and prints results."""

import sys

from config.config_validator import validate_config
from config.logging_config import configure_logging
from agent.orchestrator.agent_loop import run_agent

validate_config()
# Errors highlighted in red when stderr is a TTY
configure_logging()

if __name__ == "__main__":
    if len(sys.argv) > 1:
        instruction = " ".join(sys.argv[1:]).strip()
    else:
        instruction = input("Instruction: ").strip()
    if not instruction:
        print("No instruction provided.", file=sys.stderr)
        sys.exit(1)
    state = run_agent(instruction)
    print("\n--- Results ---")
    for r in state.step_results:
        print(f"Step {r.step_id} [{r.action}] success={r.success} latency={r.latency_seconds:.3f}s")
        if r.error:
            print(f"  error: {r.error}")
        else:
            out = r.output
            if isinstance(out, dict):
                print(f"  output: {out}")
            else:
                s = str(out)
                print(f"  output: {s[:200]}{'...' if len(s) > 200 else ''}")
