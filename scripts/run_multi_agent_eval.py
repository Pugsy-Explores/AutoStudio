#!/usr/bin/env python3
"""
Phase 9 Multi-Agent Eval: run multi_agent_tasks.json through run_multi_agent, write reports.

Metrics:
  - goal_success: % of tasks where goal_success=True
  - agent_delegations: mean agents_used per task
  - retry_depth: mean patch_attempts before success/fail
  - critic_accuracy: % of critic runs that led to success on next attempt
  - localization_accuracy: % of tasks with non-empty candidate_files
  - patch_success_rate: % of edit steps that succeeded

Output: reports/multi_agent_eval_report.json (Phase 9 metrics)
       reports/eval_report.json updated with multi_agent section when --merge

Usage:
  python scripts/run_multi_agent_eval.py              # Full eval
  python scripts/run_multi_agent_eval.py --mock      # Mock: no agent, placeholder metrics for CI
  python scripts/run_multi_agent_eval.py --limit 3   # Run first 3 tasks only
  python scripts/run_multi_agent_eval.py --merge     # Merge metrics into reports/eval_report.json
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

MULTI_AGENT_TASKS_JSON = ROOT / "tests" / "multi_agent_tasks.json"
REPORTS_DIR = ROOT / "reports"
MULTI_AGENT_EVAL_REPORT_JSON = REPORTS_DIR / "multi_agent_eval_report.json"
EVAL_REPORT_JSON = REPORTS_DIR / "eval_report.json"


def _load_tasks(limit: int | None) -> list[dict]:
    """Load multi-agent tasks from JSON."""
    if not MULTI_AGENT_TASKS_JSON.exists():
        raise FileNotFoundError(f"Multi-agent tasks not found: {MULTI_AGENT_TASKS_JSON}")
    with open(MULTI_AGENT_TASKS_JSON, encoding="utf-8") as f:
        tasks = json.load(f)
    if limit is not None:
        tasks = tasks[:limit]
    return tasks


def run_mock(tasks: list[dict]) -> dict:
    """Mock run: no agent calls, placeholder metrics for CI."""
    n = len(tasks)
    summary = {
        "goal_success_rate": 0.0,
        "agent_delegations": 6.0,
        "retry_depth": 0.0,
        "critic_accuracy": 0.0,
        "localization_accuracy": 0.0,
        "patch_success_rate": 0.0,
        "tasks_run": n,
        "mock": True,
    }
    return {"summary": summary, "results": []}


def run_full(tasks: list[dict]) -> dict:
    """Full eval: run_multi_agent for each task, aggregate Phase 9 metrics."""
    from agent.roles.supervisor_agent import run_multi_agent

    results = []
    success_count = 0
    delegations_list = []
    critic_led_to_success = 0
    critic_attempts = 0
    localization_hits = 0
    patch_successes = 0
    patch_total = 0

    for i, task in enumerate(tasks):
        goal = task.get("goal", "")
        task_id = task.get("id", f"ma_{i}")
        success_criteria = task.get("success_criteria")
        print(f"[{i+1}/{len(tasks)}] {task_id}: {goal[:50]}...", flush=True)

        t0 = time.perf_counter()
        try:
            result = run_multi_agent(
                goal,
                project_root=str(ROOT),
                success_criteria=success_criteria,
            )
        except Exception as e:
            logger.exception("run_multi_agent failed: %s", e)
            result = {"goal_success": False, "agents_used": [], "error": str(e)}
        latency = time.perf_counter() - t0

        success = result.get("goal_success", False)
        if success:
            success_count += 1
        agents_used = result.get("agents_used") or []
        delegations_list.append(len(agents_used))
        if result.get("candidate_files"):
            localization_hits += 1
        patches = result.get("patches") or []
        for p in patches:
            patch_total += 1
            if isinstance(p, dict) and (p.get("files_modified") or p.get("patches_applied", 0) > 0):
                patch_successes += 1
        if "critic" in agents_used and len(agents_used) > 4:
            critic_attempts += 1
            if success:
                critic_led_to_success += 1

        results.append({
            "id": task_id,
            "goal": goal[:80],
            "goal_success": success,
            "agents_used": agents_used,
            "agent_delegations": len(agents_used),
            "latency": latency,
            "test_status": (result.get("test_results") or {}).get("status"),
        })

    n = len(tasks)
    summary = {
        "goal_success_rate": success_count / n if n else 0.0,
        "agent_delegations": sum(delegations_list) / n if n else 0.0,
        "retry_depth": 0.0,  # Would need patch_attempts from result; placeholder
        "critic_accuracy": critic_led_to_success / critic_attempts if critic_attempts else 0.0,
        "localization_accuracy": localization_hits / n if n else 0.0,
        "patch_success_rate": patch_successes / patch_total if patch_total else 0.0,
        "tasks_run": n,
        "mock": False,
    }
    return {"results": results, "summary": summary}


def main():
    parser = argparse.ArgumentParser(
        description="Phase 9 multi-agent eval: multi_agent_tasks -> multi_agent_eval_report.json"
    )
    parser.add_argument("--mock", action="store_true", help="No agent calls, placeholder metrics for CI")
    parser.add_argument("--limit", type=int, help="Limit number of tasks to run")
    parser.add_argument("--merge", action="store_true", help="Merge metrics into reports/eval_report.json")
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
        "multi_agent": {
            "agent_delegations": out.get("summary", {}).get("agent_delegations", 0),
            "retry_depth": out.get("summary", {}).get("retry_depth", 0),
            "critic_accuracy": out.get("summary", {}).get("critic_accuracy", 0),
            "localization_accuracy": out.get("summary", {}).get("localization_accuracy", 0),
            "patch_success_rate": out.get("summary", {}).get("patch_success_rate", 0),
            "goal_success_rate": out.get("summary", {}).get("goal_success_rate", 0),
        },
        "results": out.get("results", []),
    }

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(MULTI_AGENT_EVAL_REPORT_JSON, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    if args.merge and EVAL_REPORT_JSON.exists():
        try:
            with open(EVAL_REPORT_JSON, encoding="utf-8") as f:
                existing = json.load(f)
            existing["multi_agent"] = report["multi_agent"]
            with open(EVAL_REPORT_JSON, "w", encoding="utf-8") as f:
                json.dump(existing, f, indent=2)
            print(f"Merged into {EVAL_REPORT_JSON}")
        except Exception as e:
            logger.warning("Merge failed: %s", e)

    print(f"\n=== Multi-Agent Eval Report ===")
    print(f"Written to {MULTI_AGENT_EVAL_REPORT_JSON}")
    for k, v in report["multi_agent"].items():
        if isinstance(v, float):
            print(f"  {k}: {v:.3f}")
        else:
            print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
