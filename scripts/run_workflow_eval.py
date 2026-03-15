#!/usr/bin/env python3
"""
Phase 12 Workflow Eval: run workflow_tasks.json through run_workflow, write reports.

Metrics:
  - pr_success_rate: % of tasks with valid PR generated
  - ci_pass_rate: % of tasks where CI passed
  - developer_acceptance_rate: placeholder (requires feedback collection)
  - avg_retries_per_task: mean retries before success/fail
  - pr_merge_latency: mean time from issue to PR ready (latency)
  - issue_to_pr_success: % of tasks where goal_success and pr generated

Output: reports/workflow_eval_report.json

Usage:
  python scripts/run_workflow_eval.py              # Full eval
  python scripts/run_workflow_eval.py --mock       # Mock: no agent, placeholder metrics for CI
  python scripts/run_workflow_eval.py --limit 3    # Run first 3 tasks only
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

WORKFLOW_TASKS_JSON = ROOT / "tests" / "workflow_tasks.json"
REPORTS_DIR = ROOT / "reports"
WORKFLOW_EVAL_REPORT_JSON = REPORTS_DIR / "workflow_eval_report.json"


def _load_tasks(limit: int | None) -> list[dict]:
    """Load workflow tasks from JSON."""
    if not WORKFLOW_TASKS_JSON.exists():
        raise FileNotFoundError(f"Workflow tasks not found: {WORKFLOW_TASKS_JSON}")
    with open(WORKFLOW_TASKS_JSON, encoding="utf-8") as f:
        tasks = json.load(f)
    if limit is not None:
        tasks = tasks[:limit]
    return tasks


def run_mock(tasks: list[dict]) -> dict:
    """Mock run: no agent calls, placeholder metrics for CI."""
    n = len(tasks)
    summary = {
        "pr_success_rate": 0.0,
        "ci_pass_rate": 0.0,
        "developer_acceptance_rate": 0.0,
        "avg_retries_per_task": 0.0,
        "pr_merge_latency": 0.0,
        "issue_to_pr_success": 0.0,
        "tasks_run": n,
        "mock": True,
    }
    return {"summary": summary, "results": []}


def run_full(tasks: list[dict]) -> dict:
    """Full eval: run_workflow for each task, aggregate Phase 12 metrics."""
    from agent.workflow.workflow_controller import run_workflow

    results = []
    pr_success_count = 0
    ci_pass_count = 0
    issue_to_pr_success_count = 0
    latencies = []

    for i, task in enumerate(tasks):
        goal = task.get("goal", "")
        task_id = task.get("id", f"wf_{i}")
        print(f"[{i+1}/{len(tasks)}] {task_id}: {goal[:50]}...", flush=True)

        t0 = time.perf_counter()
        try:
            result = run_workflow(goal, project_root=str(ROOT))
        except Exception as e:
            logger.exception("run_workflow failed: %s", e)
            result = {
                "goal_success": False,
                "pr": {},
                "ci": {"passed": False, "failures": [str(e)], "runtime_sec": 0},
                "review": {"valid": False, "issues": [], "summary": ""},
                "error": str(e),
            }
        latency = time.perf_counter() - t0
        latencies.append(latency)

        goal_success = result.get("goal_success", False)
        pr = result.get("pr") or {}
        pr_ok = bool(pr.get("title")) and bool(pr.get("files_modified") or pr.get("description"))
        ci_passed = (result.get("ci") or {}).get("passed", False)
        review_valid = (result.get("review") or {}).get("valid", False)

        if pr_ok:
            pr_success_count += 1
        if ci_passed:
            ci_pass_count += 1
        if goal_success and pr_ok:
            issue_to_pr_success_count += 1

        results.append({
            "id": task_id,
            "goal": goal[:80],
            "goal_success": goal_success,
            "pr_success": pr_ok,
            "ci_passed": ci_passed,
            "review_valid": review_valid,
            "latency": latency,
        })

    n = len(tasks)
    summary = {
        "pr_success_rate": pr_success_count / n if n else 0.0,
        "ci_pass_rate": ci_pass_count / n if n else 0.0,
        "developer_acceptance_rate": 0.0,  # Requires feedback collection
        "avg_retries_per_task": 0.0,  # Would need retry count from workflow
        "pr_merge_latency": sum(latencies) / n if n else 0.0,
        "issue_to_pr_success": issue_to_pr_success_count / n if n else 0.0,
        "tasks_run": n,
        "mock": False,
    }
    return {"results": results, "summary": summary}


def main():
    parser = argparse.ArgumentParser(
        description="Phase 12 workflow eval: workflow_tasks -> workflow_eval_report.json"
    )
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
        "workflow": {
            "pr_success_rate": out.get("summary", {}).get("pr_success_rate", 0),
            "ci_pass_rate": out.get("summary", {}).get("ci_pass_rate", 0),
            "developer_acceptance_rate": out.get("summary", {}).get("developer_acceptance_rate", 0),
            "avg_retries_per_task": out.get("summary", {}).get("avg_retries_per_task", 0),
            "pr_merge_latency": out.get("summary", {}).get("pr_merge_latency", 0),
            "issue_to_pr_success": out.get("summary", {}).get("issue_to_pr_success", 0),
        },
        "results": out.get("results", []),
    }

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(WORKFLOW_EVAL_REPORT_JSON, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print(f"\n=== Workflow Eval Report ===")
    print(f"Written to {WORKFLOW_EVAL_REPORT_JSON}")
    for k, v in report["workflow"].items():
        if isinstance(v, float):
            print(f"  {k}: {v:.3f}")
        else:
            print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
