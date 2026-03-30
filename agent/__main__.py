"""Entry point for python -m agent routed through agent_v2 runtime."""

import sys

from config.config_validator import validate_config
from config.logging_config import configure_logging
from agent_v2.cli_adapter import print_formatted_output
from agent_v2.runtime.bootstrap import create_runtime

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
    runtime = create_runtime()
    state = runtime.run(instruction, mode="act")
    print_formatted_output(state)
