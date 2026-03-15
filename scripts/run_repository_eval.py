#!/usr/bin/env python3
"""
Phase 10 Repository Eval: run repository_tasks.json through run_multi_agent, write reports.

Metrics:
  - localization_accuracy: % of tasks with non-empty candidate_files
  - impact_prediction_accuracy: % of tasks where impact_result had affected_files (when edit occurred)
  - context_compression_ratio: placeholder (chars_in/chars_out when compressor used)
  - long_horizon_success_rate: % of repository tasks completed successfully

Output: reports/repository_eval_report.json

Usage:
  python scripts/run_repository_eval.py              # Full eval
  python scripts/run_repository_eval.py --mock       # Mock: no agent, placeholder metrics for CI
  python scripts/run_repository_eval.py --limit 3    # Run first 3 tasks only
  python scripts/run_repository_eval.py --merge      # Merge metrics into reports/eval_report.json
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

REPOSITORY_TASKS_JSON = ROOT / "tests" / "repository_tasks.json"
REPORTS_DIR = ROOT / "reports"
REPOSITORY_EVAL_REPORT_JSON = REPORTS_DIR / "repository_eval_report.json"
EVAL_REPORT_JSON = REPORTS_DIR / "eval_report.json"


def _load_tasks(limit: int | None) -> list[dict]:
    """Load repository tasks from JSON."""
    if not REPOSITORY_TASKS_JSON.exists():
        raise FileNotFoundError(f"Repository tasks not found: {REPOSITORY_TASKS_JSON}")
    with open(REPOSITORY_TASKS_JSON, encoding="utf-8") as f:
        tasks = json.load(f)
    if limit is not None:
        tasks = tasks[:limit]
    return tasks


def run_mock(tasks: list[dict]) -> dict:
    """Mock run: no agent calls, placeholder metrics for CI."""
    n = len(tasks)
    summary = {
        "localization_accuracy": 0.0,
        "impact_prediction_accuracy": 0.0,
        "context_compression_ratio": 0.0,
        "long_horizon_success_rate": 0.0,
        "goal_success_rate": 0.0,
        "tasks_run": n,
        "mock": True,
    }
    return {"summary": summary, "results": []}


def run_full(tasks: list[dict]) -> dict:
    """Full eval: run_multi_agent for each task, aggregate Phase 10 metrics."""
    from agent.roles.supervisor_agent import run_multi_agent

    results = []
    success_count = 0
    localization_hits = 0
    impact_predicted = 0
    impact_opportunities = 0
    compression_ratios: list[float] = []

    for i, task in enumerate(tasks):
        goal = task.get("goal", "")
        task_id = task.get("id", f"repo_{i}")
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
        if result.get("candidate_files"):
            localization_hits += 1
        patches = result.get("patches") or []
        if patches:
            impact_opportunities += 1
            impact_result = result.get("impact_result") or {}
            if impact_result.get("affected_files"):
                impact_predicted += 1
        if result.get("context_compression_ratio"):
            compression_ratios.append(result["context_compression_ratio"])

        results.append({
            "id": task_id,
            "goal": goal[:80],
            "goal_success": success,
            "agents_used": result.get("agents_used", []),
            "latency": latency,
            "test_status": (result.get("test_results") or {}).get("status"),
        })

    n = len(tasks)
    summary = {
        "localization_accuracy": localization_hits / n if n else 0.0,
        "impact_prediction_accuracy": impact_predicted / impact_opportunities if impact_opportunities else 0.0,
        "context_compression_ratio": sum(compression_ratios) / len(compression_ratios) if compression_ratios else 0.0,
        "long_horizon_success_rate": success_count / n if n else 0.0,
        "goal_success_rate": success_count / n if n else 0.0,
        "tasks_run": n,
        "mock": False,
    }
    return {"results": results, "summary": summary}


def main():
    parser = argparse.ArgumentParser(
        description="Phase 10 repository eval: repository_tasks -> repository_eval_report.json"
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

    summary = out.get("summary", {})
    report = {
        "metrics": summary,
        "repository": {
            "localization_accuracy": summary.get("localization_accuracy", 0),
            "impact_prediction_accuracy": summary.get("impact_prediction_accuracy", 0),
            "context_compression_ratio": summary.get("context_compression_ratio", 0),
            "long_horizon_success_rate": summary.get("long_horizon_success_rate", 0),
            "goal_success_rate": summary.get("goal_success_rate", 0),
        },
        "results": out.get("results", []),
    }

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(REPOSITORY_EVAL_REPORT_JSON, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    if args.merge and EVAL_REPORT_JSON.exists():
        try:
            with open(EVAL_REPORT_JSON, encoding="utf-8") as f:
                existing = json.load(f)
            existing["repository"] = report["repository"]
            with open(EVAL_REPORT_JSON, "w", encoding="utf-8") as f:
                json.dump(existing, f, indent=2)
            print(f"Merged into {EVAL_REPORT_JSON}")
        except Exception as e:
            logger.warning("Merge failed: %s", e)

    print(f"\n=== Repository Eval Report ===")
    print(f"Written to {REPOSITORY_EVAL_REPORT_JSON}")
    for k, v in report["repository"].items():
        if isinstance(v, float):
            print(f"  {k}: {v:.3f}")
        else:
            print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
