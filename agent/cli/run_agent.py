"""CLI entry point: python -m agent.cli.run_agent \"instruction\" """

import logging
import sys

from agent.models.model_config import REASONING_MODEL_NAME, SMALL_MODEL_NAME
from agent.orchestrator.agent_loop import run_agent

# Ensure process logs (agent steps) appear when run from CLI
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
)
# Suppress only DEBUG from serena/solidlsp so process logs stay visible
for _name in ("serena", "solidlsp"):
    logging.getLogger(_name).setLevel(logging.INFO)


def main() -> None:
    print("Active models:")
    print(f"  SMALL → {SMALL_MODEL_NAME}")
    print(f"  REASONING → {REASONING_MODEL_NAME}")
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


if __name__ == "__main__":
    main()
