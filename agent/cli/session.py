"""Interactive session mode: REPL loop with run_controller per turn."""

import sys
from pathlib import Path

# Ensure project root on path
ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.cli.command_parser import parse_command, to_instruction_with_hint
from agent.memory.session_memory import SessionState
from agent.orchestrator.agent_controller import run_controller


def run_session(project_root: str, live: bool = False) -> int:
    """
    Run interactive chat session. Reads user input, parses slash-commands,
    calls run_controller per turn, updates session memory.
    """
    session = SessionState()
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
            result = run_controller(instruction, project_root=project_root)
        finally:
            if live and event_fns:
                from agent.cli.live_viz import uninstall_live_listeners

                uninstall_live_listeners(event_fns, stage_fns)

        # Update session memory
        summary = f"steps={result.get('completed_steps', 0)}"
        if result.get("errors"):
            summary += f" errors={len(result['errors'])}"
        session.add_turn(
            instruction=instruction,
            summary=summary,
            task_id=result.get("task_id", ""),
            files_modified=result.get("files_modified"),
            symbols_retrieved=result.get("retrieved_symbols"),
        )

        # Print result summary
        print(f"  Task {result.get('task_id', '?')[:8]}... completed_steps={result.get('completed_steps', 0)}")
        if result.get("files_modified"):
            print(f"  Files modified: {result['files_modified']}")
        if result.get("errors"):
            print(f"  Errors: {result['errors']}")
        print()

    return 0
