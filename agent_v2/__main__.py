import sys

from agent_v2.cli_adapter import parse_mode, print_formatted_output
from agent_v2.runtime.bootstrap import create_runtime
from agent_v2.runtime.trace_formatter import build_trace
from agent_v2.runtime.trace_printer import print_trace
from config.startup import ensure_services_ready


def main():
    mode, argv = parse_mode(sys.argv[1:])
    instruction = " ".join(argv).strip()
    if not instruction:
        print("No instruction provided.", file=sys.stderr)
        return 1

    # Fail fast with actionable diagnostics when model endpoints are unavailable.
    ensure_services_ready()

    runtime = create_runtime()
    result = runtime.run(instruction, mode=mode)
    if isinstance(result, dict) and result.get("trace") is not None:
        print_trace(result["trace"])
    else:
        state = result.get("state", result) if isinstance(result, dict) else result
        print_trace(build_trace(state))
    print_formatted_output(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
