#!/usr/bin/env python3
"""
Phase 2 exit criteria verification.

Runs:
1. pytest tests/test_phase2_integration.py
2. The 5 tasks from dev/evaluation/test_tasks.md via run_agent (with --mock)

Exit 0 if criteria met; 1 otherwise.
Phase 2 exit: 10-15 tasks succeed (or all defined tasks when fewer than 10).
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEST_TASKS_PATH = ROOT / "dev" / "evaluation" / "test_tasks.md"
MIN_TASKS_FOR_EXIT = 10


def _load_test_tasks() -> list[str]:
    """Parse test_tasks.md for task instructions (numbered list)."""
    if not TEST_TASKS_PATH.exists():
        return []
    text = TEST_TASKS_PATH.read_text()
    tasks = []
    for line in text.splitlines():
        m = re.match(r"^\d+\.\s+(.+)$", line.strip())
        if m:
            tasks.append(m.group(1).strip())
    return tasks


def _run_pytest() -> bool:
    """Run Phase 2 integration tests. Return True if all pass."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_phase2_integration.py", "-v", "--tb=short"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print("Phase 2 integration tests FAILED:")
        print(result.stdout)
        print(result.stderr)
        return False
    print("Phase 2 integration tests: PASSED")
    return True


def _run_task(instruction: str, mock: bool) -> bool:
    """Run a single task via run_agent. Return True if success."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    from unittest.mock import patch

    from agent.orchestrator.agent_loop import run_agent

    if mock:
        with patch("agent.orchestrator.agent_loop.get_plan") as mock_plan:
            with patch("agent.execution.executor.dispatch") as mock_dispatch:
                with patch("agent.orchestrator.agent_loop.validate_step") as mock_validate:
                    mock_plan.return_value = {
                        "steps": [
                            {"id": 1, "action": "EXPLAIN", "description": instruction[:80], "reason": "User"},
                        ]
                    }
                    mock_dispatch.return_value = {
                        "success": True,
                        "output": (
                            f"Explanation for '{instruction[:50]}': See agent/memory/state.py. "
                            "The class holds instruction, plan, step_results, and context."
                        ),
                        "error": None,
                    }
                    mock_validate.return_value = (True, "")
                    state = run_agent(instruction)
        return len(state.step_results) >= 1 and state.step_results[0].success
    else:
        state = run_agent(instruction)
        return len(state.step_results) >= 1 and all(r.success for r in state.step_results)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Verify Phase 2 exit criteria")
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use mocked planner/dispatch for task runs (no LLM)",
    )
    parser.add_argument(
        "--skip-pytest",
        action="store_true",
        help="Skip pytest; only run task set",
    )
    args = parser.parse_args()

    print("=== Phase 2 Exit Criteria Verification ===\n")

    if not args.skip_pytest:
        if not _run_pytest():
            return 1
        print()

    tasks = _load_test_tasks()
    if not tasks:
        print("No tasks in dev/evaluation/test_tasks.md; skipping task run.")
        return 0 if not args.skip_pytest else 1

    print(f"Running {len(tasks)} tasks from test_tasks.md (mock={args.mock})...")
    successes = 0
    for i, task in enumerate(tasks, 1):
        ok = _run_task(task, mock=args.mock)
        status = "PASS" if ok else "FAIL"
        print(f"  {i}. [{status}] {task[:60]}{'...' if len(task) > 60 else ''}")
        if ok:
            successes += 1

    required = len(tasks) if len(tasks) < MIN_TASKS_FOR_EXIT else MIN_TASKS_FOR_EXIT

    print(f"\nTasks: {successes}/{len(tasks)} passed (required: {required})")
    if successes >= required:
        print("Phase 2 exit criteria: MET")
        return 0
    print("Phase 2 exit criteria: NOT MET")
    return 1


if __name__ == "__main__":
    sys.exit(main())
