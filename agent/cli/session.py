"""Interactive session mode: REPL loop with agent_v2 runtime per turn."""

import sys
from pathlib import Path

# Ensure project root on path
ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.cli.command_parser import parse_command, to_instruction_with_hint
from agent.memory.session_memory import SessionState
from agent_v2.cli_adapter import format_output
from agent_v2.runtime.bootstrap import create_runtime


def run_session(project_root: str, live: bool = False) -> int:
    """
    Run interactive chat session. Reads user input, parses slash-commands,
    runs the v2 runtime per turn, updates session memory.
    """
    session = SessionState()
    runtime = create_runtime()
    print("AutoStudio chat session. Commands: /explain, /fix, /refactor, /add-logging, /find")
    print("Or type a plain instruction. Ctrl+D or 'exit' to quit.\n")

    while True:
        try:
            line = input("> ").strip()
        except EOFError:
            print("\nBye.")
            break
        if not line:
            continue
        if line.lower() in ("exit", "quit", "q"):
            print("Bye.")
            break

        parsed = parse_command(line)
        instruction = to_instruction_with_hint(parsed)
        if not instruction:
            print("No instruction. Try: /explain StepExecutor")
            continue

        event_fns, stage_fns = [], []
        if live:
            from agent.cli.live_viz import install_live_listeners, uninstall_live_listeners

            event_fns, stage_fns = install_live_listeners()
            print(f"[Live] {instruction[:80]}{'...' if len(instruction) > 80 else ''}")

        try:
            state = runtime.run(instruction, mode="act")
            result = format_output(state)
        finally:
            if live and event_fns:
                from agent.cli.live_viz import uninstall_live_listeners

                uninstall_live_listeners(event_fns, stage_fns)

        # Update session memory
        history = result.get("result") or []
        summary = f"steps={len(history)}"
        session.add_turn(
            instruction=instruction,
            summary=summary,
            task_id="runtime-v2",
            files_modified=[],
            symbols_retrieved=[],
        )

        # Print result summary
        print(f"  Task runtime-v2... completed_steps={len(history)}")
        if result.get("plan"):
            print(f"  Plan: {result['plan']}")
        print()

    return 0
