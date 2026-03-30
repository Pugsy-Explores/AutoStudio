"""CLI entry point: python -m agent.cli.run_agent \"instruction\" """

import argparse
import logging
import sys
from pathlib import Path

from agent.cli.env_bootstrap import (
    bootstrap_cli_env,
    configure_cli_logging,
    register_logging_cli_arguments,
)
from config.config_validator import validate_config
from config.startup import ensure_services_ready
from agent_v2.cli_adapter import format_output
from agent_v2.runtime.bootstrap import create_runtime

def main() -> None:
    parser = argparse.ArgumentParser(description="AutoStudio single-shot run")
    parser.add_argument(
        "--project-root",
        type=Path,
        default=None,
        help="Project root (sets SERENA_PROJECT_DIR; load <root>/.env without overriding shell env)",
    )
    register_logging_cli_arguments(parser)
    parser.add_argument(
        "--live",
        "--verbose",
        action="store_true",
        help="Live step visualization (--verbose here is not log level; use --debug / --log-level)",
    )
    parser.add_argument(
        "--mode",
        choices=["act", "plan", "plan_legacy", "deep_plan", "plan_execute"],
        default="act",
        help="Runtime mode: plan=safe iterative execution; plan_legacy=explore+plan only; deep_plan=plan with deep=True",
    )
    parser.add_argument("instruction", nargs="*", help="Instruction to run")
    args = parser.parse_args()
    bootstrap_cli_env(args.project_root)
    configure_cli_logging(args)
    validate_config()
    # Suppress DEBUG noise from serena/solidlsp unless root is DEBUG and user wants everything
    for _name in ("serena", "solidlsp"):
        logging.getLogger(_name).setLevel(logging.INFO)
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
