"""CLI entrypoint for autostudio command. Routes subcommands to controller or scripts."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from agent.cli.env_bootstrap import (
    bootstrap_cli_env,
    configure_cli_logging,
    logging_cli_parent_parser,
    register_logging_cli_arguments,
)

# Ensure project root is on path
ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _run_runtime(instruction: str, mode: str = "act"):
    from agent_v2.runtime.bootstrap import create_runtime

    runtime = create_runtime()
    return runtime.run(instruction, mode=mode)


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
    """Run explain instruction via v2 runtime."""

    symbol = args.symbol or " ".join(args.remainder or [])
    if not symbol:
        print("Usage: autostudio explain <symbol>", file=sys.stderr)
        return 1
    instruction = f"Explain how {symbol} works"
    state = _run_runtime(instruction, mode="act")
    _print_runtime_result(state)
    return 0


def cmd_edit(args: argparse.Namespace) -> int:
    """Run edit instruction via v2 runtime."""

    instr = getattr(args, "instruction", None)
    instruction = " ".join(instr) if isinstance(instr, list) else (instr or " ".join(args.remainder or []))
    if not instruction:
        print("Usage: autostudio edit <instruction>", file=sys.stderr)
        return 1
    state = _run_runtime(instruction, mode="act")
    _print_runtime_result(state)
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
    """Single-shot run. Pass instruction as remainder."""

    instr = getattr(args, "instruction", None) or args.remainder or []
    instruction = " ".join(instr).strip() if isinstance(instr, list) else str(instr or "").strip()
    if not instruction:
        print("Usage: autostudio run <instruction>", file=sys.stderr)
        return 1
    state = _run_runtime(instruction, mode=getattr(args, "mode", "act"))
    _print_runtime_result(state)
    return 0


def cmd_issue(args: argparse.Namespace) -> int:
    """Parse issue and run full workflow (issue -> task -> agent -> PR -> CI -> review)."""
    from agent.workflow.workflow_controller import run_workflow

    issue_text = " ".join(getattr(args, "issue_text", []) or args.remainder or [])
    if not issue_text:
        print("Usage: autostudio issue <issue_text>", file=sys.stderr)
        return 1
    root = str(args.project_root or _project_root())
    result = run_workflow(issue_text, root)
    _print_workflow_result(result)
    return 0 if result.get("goal_success") else 1


def cmd_fix(args: argparse.Namespace) -> int:
    """Run multi-agent solve only (no PR/CI/review)."""
    from agent.roles.supervisor_agent import run_multi_agent
    from agent.roles.workspace import AgentWorkspace
    from agent.workflow.pr_generator import generate_pr
    from agent.workflow.workflow_controller import _save_last_workflow

    instruction = " ".join(getattr(args, "instruction", []) or args.remainder or [])
    if not instruction:
        print("Usage: autostudio fix <instruction>", file=sys.stderr)
        return 1
    root = str(args.project_root or _project_root())
    result = run_multi_agent(instruction, project_root=root)
    ws = AgentWorkspace.from_goal(instruction, root, "")
    ws.goal = instruction
    ws.plan = result.get("plan", {})
    ws.patches = result.get("patches", [])
    ws.test_results = result.get("test_results")
    pr_data = generate_pr(ws, ws.patches, ws.test_results)
    _save_last_workflow(
        {
            "task_id": "",
            "trace_id": "",
            "task": {"description": instruction},
            "goal_success": result.get("goal_success", False),
            "pr": pr_data,
            "ci": {"passed": False, "failures": [], "runtime_sec": 0},
            "review": {"valid": False, "issues": [], "summary": ""},
            "patches": result.get("patches", []),
            "agents_used": result.get("agents_used", []),
            "test_results": result.get("test_results"),
        },
        root,
    )
    _print_workflow_result(result)
    return 0 if result.get("goal_success") else 1


def cmd_pr(args: argparse.Namespace) -> int:
    """Generate PR from last workflow run."""
    from agent.workflow.workflow_controller import load_last_workflow

    root = str(args.project_root or _project_root())
    last = load_last_workflow(root)
    if not last or not last.get("pr"):
        print("No previous workflow found. Run 'autostudio issue <text>' first.", file=sys.stderr)
        return 1
    pr = last["pr"]
    print(f"\n--- PR: {pr.get('title', '?')} ---")
    print(pr.get("description", ""))
    print(f"\nFiles modified: {pr.get('files_modified', [])}")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    """Review last patch."""
    from agent.workflow.code_review_agent import review_patch
    from agent.workflow.workflow_controller import load_last_workflow

    root = str(args.project_root or _project_root())
    last = load_last_workflow(root)
    if not last or not last.get("patches"):
        print("No previous workflow with patches found. Run 'autostudio issue <text>' or 'autostudio fix <instruction>' first.", file=sys.stderr)
        return 1
    result = review_patch(last["patches"], last.get("test_results"))
    print(f"\n--- Review ---")
    print(f"Valid: {result.get('valid', False)}")
    print(f"Summary: {result.get('summary', '')}")
    if result.get("issues"):
        print("Issues:", result["issues"])
    return 0 if result.get("valid") else 1


def cmd_ci(args: argparse.Namespace) -> int:
    """Run CI on project root."""
    from agent.workflow.ci_runner import run_ci

    root = str(args.project_root or _project_root())
    result = run_ci(root)
    print(f"\n--- CI ---")
    print(f"Passed: {result.get('passed', False)}")
    print(f"Runtime: {result.get('runtime_sec', 0)}s")
    if result.get("failures"):
        print("Failures:", result["failures"])
    return 0 if result.get("passed") else 1


def _print_result(result: dict) -> None:
    """Print controller result summary."""
    print(f"\n--- Task {result.get('task_id', '?')} ---")
    print(f"Completed steps: {result.get('completed_steps', 0)}")
    if result.get("files_modified"):
        print(f"Files modified: {result['files_modified']}")
    if result.get("errors"):
        print(f"Errors: {result['errors']}")


def _print_runtime_result(state) -> None:
    from agent_v2.cli_adapter import format_output

    result = format_output(state)
    print("\n--- Runtime Result ---")
    history = result.get("result") or []
    print(f"History entries: {len(history)}")
    if result.get("plan"):
        print(f"Plan: {result['plan']}")
    metadata = result.get("metadata") or {}
    if metadata:
        print(f"Metadata: {metadata}")


def _print_workflow_result(result: dict) -> None:
    """Print workflow result summary."""
    print(f"\n--- Workflow {result.get('task_id', '?')} ---")
    print(f"Goal success: {result.get('goal_success', False)}")
    if result.get("pr", {}).get("title"):
        print(f"PR title: {result['pr']['title']}")
    if result.get("ci"):
        ci = result["ci"]
        print(f"CI passed: {ci.get('passed', False)}")
    if result.get("review", {}).get("summary"):
        print(f"Review: {result['review']['summary']}")
    if result.get("agents_used"):
        print(f"Agents used: {result['agents_used']}")
    if result.get("error"):
        print(f"Error: {result['error']}")


def main() -> int:
    parser = argparse.ArgumentParser(prog="autostudio", description="AutoStudio CLI")
    parser.add_argument(
        "--project-root",
        type=Path,
        default=None,
        help="Sets SERENA_PROJECT_DIR and loads <root>/.env (shell env overrides .env)",
    )
    register_logging_cli_arguments(parser)
    _log_parent = logging_cli_parent_parser()

    subparsers = parser.add_subparsers(dest="command", help="Subcommands", required=False)

    # explain <symbol>
    p_explain = subparsers.add_parser("explain", parents=[_log_parent], help="Explain a symbol")
    p_explain.add_argument("symbol", nargs="?", help="Symbol to explain")
    p_explain.set_defaults(cmd=cmd_explain)

    # edit <instruction>
    p_edit = subparsers.add_parser("edit", parents=[_log_parent], help="Edit code per instruction")
    p_edit.add_argument("instruction", nargs="*", help="Edit instruction")
    p_edit.set_defaults(cmd=cmd_edit)

    # trace <task_id>
    p_trace = subparsers.add_parser("trace", parents=[_log_parent], help="View trace by task_id or trace_id")
    p_trace.add_argument("trace_id", nargs="?", help="Task ID or trace ID")
    p_trace.set_defaults(cmd=cmd_trace)

    # debug last-run
    p_debug = subparsers.add_parser("debug", parents=[_log_parent], help="Debug last run (interactive trace viewer)")
    p_debug.add_argument("target", nargs="?", default="last-run", help="Target (last-run)")
    p_debug.set_defaults(cmd=cmd_debug)

    # chat
    p_chat = subparsers.add_parser("chat", parents=[_log_parent], help="Interactive session mode")
    p_chat.add_argument("--live", action="store_true", help="Show live step visualization")
    p_chat.set_defaults(cmd=cmd_chat)

    # run <instruction> (single-shot)
    p_run = subparsers.add_parser("run", parents=[_log_parent], help="Single-shot run")
    p_run.add_argument(
        "--mode",
        choices=["act", "plan", "plan_legacy", "deep_plan", "plan_execute"],
        default="act",
        help="Runtime mode: plan=safe iterative execution; plan_legacy=explore+plan only",
    )
    p_run.add_argument("instruction", nargs="*", help="Instruction to run")
    p_run.set_defaults(cmd=cmd_run)

    # Phase 12 workflow commands
    p_issue = subparsers.add_parser("issue", help="Parse issue and run full workflow")
    p_issue.add_argument("issue_text", nargs="*", help="Issue text or ID")
    p_issue.set_defaults(cmd=cmd_issue)

    p_fix = subparsers.add_parser("fix", parents=[_log_parent], help="Run multi-agent solve only")
    p_fix.add_argument("instruction", nargs="*", help="Instruction to fix")
    p_fix.set_defaults(cmd=cmd_fix)

    p_pr = subparsers.add_parser("pr", parents=[_log_parent], help="Generate PR from last workflow")
    p_pr.set_defaults(cmd=cmd_pr)

    p_review = subparsers.add_parser("review", parents=[_log_parent], help="Review last patch")
    p_review.set_defaults(cmd=cmd_review)

    p_ci = subparsers.add_parser("ci", parents=[_log_parent], help="Run CI on project root")
    p_ci.set_defaults(cmd=cmd_ci)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    bootstrap_cli_env(getattr(args, "project_root", None))
    configure_cli_logging(args)

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
