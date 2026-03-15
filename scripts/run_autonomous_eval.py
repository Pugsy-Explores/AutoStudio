#!/usr/bin/env python3
"""
Phase 8 Autonomous Eval: run autonomous_tasks.json through run_autonomous, write reports.

Reflection metrics:
  - attempts_per_goal: mean attempts to succeed
  - retry_success_rate: % of FAILURE->SUCCESS across retries
  - critic_accuracy: % of diagnoses that led to a successful next attempt
  - trajectory_reuse: % of runs where a past trajectory was consulted

Usage:
  python scripts/run_autonomous_eval.py              # Full eval (runs agent)
  python scripts/run_autonomous_eval.py --mock       # Mock: no agent, placeholder metrics for CI
  python scripts/run_autonomous_eval.py --limit 3   # Run first 3 tasks only
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

# Ensure AutoStudio root is on path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

AUTONOMOUS_TASKS_JSON = ROOT / "tests" / "autonomous_tasks.json"
REPORTS_DIR = ROOT / "reports"
AUTONOMOUS_EVAL_REPORT_JSON = REPORTS_DIR / "autonomous_eval_report.json"


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
    summary = {
        "attempts_per_goal": 1.0,
        "retry_success_rate": 0.0,
        "critic_accuracy": 0.0,
        "trajectory_reuse": 0.0,
        "autonomous_success_rate": 0.0,
        "tasks_run": n,
        "mock": True,
    }
    return {"summary": summary, "results": []}


def run_full(tasks: list[dict]) -> dict:
    """Full eval: run_autonomous for each task, aggregate reflection metrics."""
    from agent.autonomous.agent_loop import run_autonomous

    results = []
    success_count = 0
    attempts_list = []
    retry_successes = 0  # FAILURE on attempt 1 -> SUCCESS on attempt 2+
    retry_attempts = 0
    critic_led_to_success = 0
    critic_attempts = 0
    trajectory_reuse_count = 0
    trajectory_checks = 0

    for i, task in enumerate(tasks):
        goal = task.get("goal", "")
        task_id = task.get("id", f"auto_{i}")
        success_criteria = task.get("success_criteria")
        print(f"[{i+1}/{len(tasks)}] {task_id}: {goal[:50]}...", flush=True)

        t0 = time.perf_counter()
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
        latency = time.perf_counter() - t0

        eval_status = (result.get("evaluation") or {}).get("status", "FAILURE")
        attempts = result.get("attempts", 1)
        attempts_list.append(attempts)

        if eval_status == "SUCCESS":
            success_count += 1
            if attempts > 1:
                retry_successes += 1
            if attempts > 1:
                critic_attempts += 1
                critic_led_to_success += 1
        else:
            if attempts > 1:
                critic_attempts += 1
        if attempts > 1:
            retry_attempts += 1

        # trajectory_reuse: check if retry_hints was used (simplified: any attempt > 1 means we had retry flow)
        if attempts > 1:
            trajectory_checks += 1
            # We don't yet have trajectory lookup from past runs; use placeholder
            trajectory_reuse_count += 0

        results.append({
            "id": task_id,
            "goal": goal[:80],
            "status": eval_status,
            "attempts": attempts,
            "latency": latency,
        })

    n = len(tasks)
    summary = {
        "attempts_per_goal": sum(attempts_list) / n if n else 1.0,
        "retry_success_rate": retry_successes / retry_attempts if retry_attempts else 0.0,
        "critic_accuracy": critic_led_to_success / critic_attempts if critic_attempts else 0.0,
        "trajectory_reuse": trajectory_reuse_count / trajectory_checks if trajectory_checks else 0.0,
        "autonomous_success_rate": success_count / n if n else 0.0,
        "tasks_run": n,
        "mock": False,
    }
    return {"results": results, "summary": summary}


def main():
    parser = argparse.ArgumentParser(description="Phase 8 autonomous eval: autonomous_tasks -> eval_report.json")
    parser.add_argument("--mock", action="store_true", help="No agent calls, placeholder metrics for CI")
    parser.add_argument("--limit", type=int, help="Limit number of tasks to run")
    args = parser.parse_args()

    tasks = _load_tasks(args.limit)
    if not tasks:
        print("No tasks to run.", file=sys.stderr)
        sys.exit(1)

    if args.mock:
        out = run_mock(tasks)
    else:
        out = run_full(tasks)

    report = {
        "metrics": out.get("summary", {}),
        "reflection": {
            "attempts_per_goal": out.get("summary", {}).get("attempts_per_goal", 0),
            "retry_success_rate": out.get("summary", {}).get("retry_success_rate", 0),
            "critic_accuracy": out.get("summary", {}).get("critic_accuracy", 0),
            "trajectory_reuse": out.get("summary", {}).get("trajectory_reuse", 0),
        },
        "results": out.get("results", []),
    }

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(AUTONOMOUS_EVAL_REPORT_JSON, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print(f"\n=== Autonomous Eval Report ===")
    print(f"Written to {AUTONOMOUS_EVAL_REPORT_JSON}")
    for k, v in report["reflection"].items():
        if isinstance(v, float):
            print(f"  {k}: {v:.3f}")
        else:
            print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
