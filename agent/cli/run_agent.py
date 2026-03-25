"""CLI entry point: python -m agent.cli.run_agent \"instruction\" """

import argparse
import logging
import sys

from config.config_validator import validate_config
from config.logging_config import configure_logging
from config.startup import ensure_services_ready
from agent.models.model_config import REASONING_MODEL_NAME, REASONING_V2_MODEL_NAME, SMALL_MODEL_NAME
from agent_v2.cli_adapter import format_output
from agent_v2.runtime.bootstrap import create_runtime

validate_config()
# Ensure process logs (agent steps) appear when run from CLI; errors highlighted in red
configure_logging()
# Suppress only DEBUG from serena/solidlsp so process logs stay visible
for _name in ("serena", "solidlsp"):
    logging.getLogger(_name).setLevel(logging.INFO)


def main() -> None:
    parser = argparse.ArgumentParser(description="AutoStudio single-shot run")
    parser.add_argument("--live", "--verbose", action="store_true", help="Show live step visualization")
    parser.add_argument("--mode", choices=["act", "plan", "deep_plan"], default="act", help="Runtime mode")
    parser.add_argument("instruction", nargs="*", help="Instruction to run")
    args = parser.parse_args()
    instruction = " ".join(args.instruction) if args.instruction else ""
    if not instruction:
        instruction = input("Instruction: ").strip()
    if not instruction:
        print("No instruction provided.", file=sys.stderr)
        sys.exit(1)

    ensure_services_ready()

    event_fns, stage_fns = [], []
    if args.live:
        from agent.cli.live_viz import install_live_listeners, uninstall_live_listeners

        event_fns, stage_fns = install_live_listeners()
        print("--- Live ---")

    try:
        runtime = create_runtime()
        state = runtime.run(instruction, mode=args.mode)
    finally:
        if args.live and event_fns:
            from agent.cli.live_viz import uninstall_live_listeners

            uninstall_live_listeners(event_fns, stage_fns)
    print("\n--- Results ---")
    print(format_output(state))


if __name__ == "__main__":
    main()
