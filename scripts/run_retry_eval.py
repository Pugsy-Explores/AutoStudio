#!/usr/bin/env python3
"""
Phase 15 Retry Eval: run autonomous tasks with retries, output trajectory metrics.

Outputs:
  success_rate: fraction of tasks that succeeded on any attempt
  retry_success_rate: fraction of tasks that needed >1 attempt but eventually succeeded
  attempts_per_task: average attempt count per task

Usage:
  python scripts/run_retry_eval.py              # Full eval
  python scripts/run_retry_eval.py --mock        # Mock: no agent, placeholder for CI
  python scripts/run_retry_eval.py --limit 3     # Run first 3 tasks only
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# Ensure AutoStudio root is on path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

AUTONOMOUS_TASKS_JSON = ROOT / "tests" / "autonomous_tasks.json"
REPORTS_DIR = ROOT / "reports"
RETRY_EVAL_REPORT_JSON = REPORTS_DIR / "retry_eval_report.json"


def _load_tasks(limit: int | None) -> list[dict]:
    """Load autonomous tasks from JSON."""
    if not AUTONOMOUS_TASKS_JSON.exists():
        raise FileNotFoundError(f"Autonomous tasks not found: {AUTONOMOUS_TASKS_JSON}")
    with open(AUTONOMOUS_TASKS_JSON, encoding="utf-8") as f:
        tasks = json.load(f)
    if limit is not None:
        tasks = tasks[:limit]
    return tasks


def run_mock(tasks: list[dict]) -> dict:
    """Mock run: no agent calls, placeholder metrics for CI."""
    n = len(tasks)
    return {
        "success_rate": 0.0,
        "retry_success_rate": 0.0,
        "attempts_per_task": 1.0,
        "tasks_run": n,
        "mock": True,
    }


def run_full(tasks: list[dict]) -> dict:
    """Full eval: run_autonomous for each task with max_retries=3."""
    from agent.autonomous.agent_loop import run_autonomous

    success_count = 0
    attempts_list = []
    retry_success_count = 0  # succeeded and needed >1 attempt
    retry_attempt_count = 0   # total tasks that had >1 attempt

    for i, task in enumerate(tasks):
        goal = task.get("goal", "")
        task_id = task.get("id", f"retry_{i}")
        success_criteria = task.get("success_criteria")
        print(f"[{i+1}/{len(tasks)}] {task_id}: {goal[:50]}...", flush=True)

        try:
            result = run_autonomous(
                goal,
                project_root=str(ROOT),
                max_retries=3,
                success_criteria=success_criteria,
            )
        except Exception as e:
            logger.exception("run_autonomous failed: %s", e)
            result = {"evaluation": {"status": "FAILURE"}, "attempts": 1}

        eval_status = (result.get("evaluation") or {}).get("status", "FAILURE")
        attempts = result.get("attempts", 1)
        attempts_list.append(attempts)

        if eval_status == "SUCCESS":
            success_count += 1
            if attempts > 1:
                retry_success_count += 1
        if attempts > 1:
            retry_attempt_count += 1

    n = len(tasks)
    return {
        "success_rate": success_count / n if n else 0.0,
        "retry_success_rate": retry_success_count / retry_attempt_count if retry_attempt_count else 0.0,
        "attempts_per_task": sum(attempts_list) / n if n else 1.0,
        "tasks_run": n,
        "mock": False,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Phase 15 retry eval: run autonomous tasks, output success_rate, retry_success_rate, attempts_per_task"
    )
    parser.add_argument("--mock", action="store_true", help="No agent calls, placeholder for CI")
    parser.add_argument("--limit", type=int, help="Limit number of tasks to run")
    args = parser.parse_args()

    tasks = _load_tasks(args.limit)
    if not tasks:
        print("No tasks to run.", file=sys.stderr)
        sys.exit(1)

    if args.mock:
        metrics = run_mock(tasks)
    else:
        metrics = run_full(tasks)

    report = {
        "success_rate": metrics["success_rate"],
        "retry_success_rate": metrics["retry_success_rate"],
        "attempts_per_task": metrics["attempts_per_task"],
        "tasks_run": metrics["tasks_run"],
        "mock": metrics.get("mock", False),
    }

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(RETRY_EVAL_REPORT_JSON, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print("\n=== Retry Eval Report ===")
    print(f"Written to {RETRY_EVAL_REPORT_JSON}")
    print(f"  success_rate: {report['success_rate']:.3f}")
    print(f"  retry_success_rate: {report['retry_success_rate']:.3f}")
    print(f"  attempts_per_task: {report['attempts_per_task']:.3f}")


if __name__ == "__main__":
    main()
