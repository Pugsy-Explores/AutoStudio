"""CLI entrypoint for autostudio command. Routes subcommands to controller or scripts."""

import argparse
import os
import subprocess
import sys
from pathlib import Path

# Ensure project root is on path
ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _project_root() -> Path:
    """Resolve project root from env or cwd."""
    root = os.environ.get("SERENA_PROJECT_DIR", os.getcwd())
    return Path(root).resolve()


def _run_replay_trace(trace_arg: str, mode: str = "print", project_root: Path | None = None) -> int:
    """Invoke scripts/replay_trace.py as subprocess."""
    root = project_root or _project_root()
    script = ROOT / "scripts" / "replay_trace.py"
    cmd = [sys.executable, str(script), trace_arg, "--mode", mode, "--project-root", str(root)]
    return subprocess.call(cmd)


def _get_latest_trace_id(project_root: Path) -> str | None:
    """Return trace_id of most recently modified trace file."""
    traces_dir = project_root / ".agent_memory" / "traces"
    if not traces_dir.exists():
        return None
    files = list(traces_dir.glob("*.json"))
    if not files:
        return None
    latest = max(files, key=lambda p: p.stat().st_mtime)
    return latest.stem


def cmd_explain(args: argparse.Namespace) -> int:
    """Run explain instruction via controller."""
    from agent.orchestrator.agent_controller import run_controller

    symbol = args.symbol or " ".join(args.remainder or [])
    if not symbol:
        print("Usage: autostudio explain <symbol>", file=sys.stderr)
        return 1
    instruction = f"Explain how {symbol} works"
    result = run_controller(instruction, project_root=str(args.project_root))
    _print_result(result)
    return 0


def cmd_edit(args: argparse.Namespace) -> int:
    """Run edit instruction via controller."""
    from agent.orchestrator.agent_controller import run_controller

    instr = getattr(args, "instruction", None)
    instruction = " ".join(instr) if isinstance(instr, list) else (instr or " ".join(args.remainder or []))
    if not instruction:
        print("Usage: autostudio edit <instruction>", file=sys.stderr)
        return 1
    result = run_controller(instruction, project_root=str(args.project_root))
    _print_result(result)
    return 0


def cmd_trace(args: argparse.Namespace) -> int:
    """View trace by task_id or trace_id. Wraps replay_trace.py."""
    trace_arg = getattr(args, "trace_id", None) or (args.remainder[0] if args.remainder else "")
    if not trace_arg:
        print("Usage: autostudio trace <task_id|trace_id>", file=sys.stderr)
        return 1
    root = args.project_root or _project_root()
    return _run_replay_trace(trace_arg, mode="print", project_root=root)


def cmd_debug(args: argparse.Namespace) -> int:
    """Debug last run: replay most recent trace in interactive mode."""
    root = args.project_root or _project_root()
    trace_id = _get_latest_trace_id(root)
    if not trace_id:
        print("No traces found. Run a task first.", file=sys.stderr)
        return 1
    return _run_replay_trace(trace_id, mode="interactive", project_root=root)


def cmd_chat(args: argparse.Namespace) -> int:
    """Start interactive session mode."""
    from agent.cli.session import run_session

    return run_session(project_root=str(args.project_root or _project_root()), live=getattr(args, "live", False))


def cmd_run(args: argparse.Namespace) -> int:
    """Single-shot run (legacy). Pass instruction as remainder."""
    from agent.orchestrator.agent_controller import run_controller

    instr = getattr(args, "instruction", None) or args.remainder or []
    instruction = " ".join(instr).strip() if isinstance(instr, list) else str(instr or "").strip()
    if not instruction:
        print("Usage: autostudio run <instruction>", file=sys.stderr)
        return 1
    result = run_controller(instruction, project_root=str(args.project_root))
    _print_result(result)
    return 0


def _print_result(result: dict) -> None:
    """Print controller result summary."""
    print(f"\n--- Task {result.get('task_id', '?')} ---")
    print(f"Completed steps: {result.get('completed_steps', 0)}")
    if result.get("files_modified"):
        print(f"Files modified: {result['files_modified']}")
    if result.get("errors"):
        print(f"Errors: {result['errors']}")


def main() -> int:
    parser = argparse.ArgumentParser(prog="autostudio", description="AutoStudio CLI")
    parser.add_argument("--project-root", type=Path, default=None, help="Project root for traces/repo")

    subparsers = parser.add_subparsers(dest="command", help="Subcommands", required=False)

    # explain <symbol>
    p_explain = subparsers.add_parser("explain", help="Explain a symbol")
    p_explain.add_argument("symbol", nargs="?", help="Symbol to explain")
    p_explain.set_defaults(cmd=cmd_explain)

    # edit <instruction>
    p_edit = subparsers.add_parser("edit", help="Edit code per instruction")
    p_edit.add_argument("instruction", nargs="*", help="Edit instruction")
    p_edit.set_defaults(cmd=cmd_edit)

    # trace <task_id>
    p_trace = subparsers.add_parser("trace", help="View trace by task_id or trace_id")
    p_trace.add_argument("trace_id", nargs="?", help="Task ID or trace ID")
    p_trace.set_defaults(cmd=cmd_trace)

    # debug last-run
    p_debug = subparsers.add_parser("debug", help="Debug last run (interactive trace viewer)")
    p_debug.add_argument("target", nargs="?", default="last-run", help="Target (last-run)")
    p_debug.set_defaults(cmd=cmd_debug)

    # chat
    p_chat = subparsers.add_parser("chat", help="Interactive session mode")
    p_chat.add_argument("--live", action="store_true", help="Show live step visualization")
    p_chat.set_defaults(cmd=cmd_chat)

    # run <instruction> (single-shot)
    p_run = subparsers.add_parser("run", help="Single-shot run (legacy)")
    p_run.add_argument("instruction", nargs="*", help="Instruction to run")
    p_run.set_defaults(cmd=cmd_run)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    # Normalize remainder for subparsers that use it
    if getattr(args, "remainder", None) is None:
        args.remainder = []

    cmd_fn = getattr(args, "cmd", None)
    if cmd_fn:
        return cmd_fn(args)

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
