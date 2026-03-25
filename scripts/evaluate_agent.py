#!/usr/bin/env python3
"""
Evaluate agent on tasks from tests/agent_eval.json.

Metrics:
  - task_success_rate: % of tasks where final result is valid (validator passes)
  - retrieval_recall: For SEARCH/EXPLAIN tasks, whether expected file/symbol appears in ranked_context
  - planner_accuracy: Whether planner produced correct action sequence (SEARCH before EXPLAIN when needed)
  - latency: Time per task (seconds)

Usage:
  python scripts/evaluate_agent.py [--plan-only] [--tasks id1,id2] [--metrics m1,m2]
  python scripts/evaluate_agent.py --plan-only  # Light eval: get_plan only, no model calls
  python scripts/evaluate_agent.py --tasks explain_step_executor,where_retry
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

EVAL_JSON = ROOT / "tests" / "agent_eval.json"
METRICS = ("task_success_rate", "retrieval_recall", "planner_accuracy", "latency")


def _load_tasks(tasks_filter: list[str] | None) -> list[dict]:
    """Load eval tasks from JSON, optionally filter by id."""
    if not EVAL_JSON.exists():
        raise FileNotFoundError(f"Eval dataset not found: {EVAL_JSON}")
    with open(EVAL_JSON, encoding="utf-8") as f:
        tasks = json.load(f)
    if tasks_filter:
        tasks = [t for t in tasks if t.get("id") in tasks_filter]
    return tasks


def _planner_accuracy(task: dict, plan: dict) -> bool:
    """True if plan has correct action sequence. SEARCH before EXPLAIN when expects_context."""
    steps = plan.get("steps") or []
    actions = [s.get("action", "").upper() for s in steps]
    expected = (task.get("expected_action") or "").upper()
    expects_context = task.get("expects_context", False)

    if expects_context and expected == "EXPLAIN":
        if "SEARCH" in actions and "EXPLAIN" in actions:
            search_idx = actions.index("SEARCH")
            explain_idx = actions.index("EXPLAIN")
            return search_idx < explain_idx
        return False
    if expected:
        return expected in actions
    return True


def _retrieval_recall(task: dict, state) -> bool:
    """True if expected file/symbol appears in ranked_context."""
    ranked = (state.context or {}).get("ranked_context") or []
    pattern = task.get("expected_file_pattern", "")
    if not pattern:
        return True
    pattern_lower = pattern.lower()
    for c in ranked:
        f = (c.get("file") or "").lower()
        s = (c.get("symbol") or "").lower()
        if pattern_lower in f or pattern_lower in s:
            return True
    return False


def _task_success(task: dict, state) -> bool:
    """True if last completed step passed validation."""
    if not state.step_results:
        return False
    step = state.completed_steps[-1] if state.completed_steps else {}
    result = state.step_results[-1]
    from agent.orchestrator.validator import _validate_step_rules
    valid, _ = _validate_step_rules(step, result, state)
    return valid


def run_plan_only(tasks: list[dict], metrics_filter: list[str] | None) -> dict:
    """Light eval: get_plan only. Computes planner_accuracy."""
    from agent.orchestrator.plan_resolver import get_plan

    results = []
    planner_correct = 0
    for task in tasks:
        instruction = task.get("instruction", "")
        t0 = time.perf_counter()
        plan = get_plan(instruction)
        latency = time.perf_counter() - t0
        acc = _planner_accuracy(task, plan)
        if acc:
            planner_correct += 1
        results.append({
            "id": task.get("id"),
            "instruction": instruction[:60],
            "planner_accuracy": acc,
            "latency": latency,
        })
    n = len(tasks)
    summary = {
        "planner_accuracy": planner_correct / n if n else 0,
        "latency_avg": sum(r["latency"] for r in results) / n if n else 0,
        "tasks_run": n,
    }
    return {"results": results, "summary": summary}


def run_full(tasks: list[dict], metrics_filter: list[str] | None) -> dict:
    """Full eval: run_agent. Computes all metrics."""
    from tests.utils.runtime_adapter import run_agent
    from agent.orchestrator.plan_resolver import get_plan

    results = []
    success_count = 0
    recall_count = 0
    planner_correct = 0
    latencies = []

    for task in tasks:
        instruction = task.get("instruction", "")
        t0 = time.perf_counter()
        state = run_agent(instruction)
        latency = time.perf_counter() - t0
        latencies.append(latency)

        plan = state.current_plan
        acc = _planner_accuracy(task, plan)
        if acc:
            planner_correct += 1

        success = _task_success(task, state)
        if success:
            success_count += 1

        recall = _retrieval_recall(task, state)
        if recall:
            recall_count += 1

        results.append({
            "id": task.get("id"),
            "instruction": instruction[:60],
            "task_success": success,
            "retrieval_recall": recall,
            "planner_accuracy": acc,
            "latency": latency,
        })

    n = len(tasks)
    summary = {
        "task_success_rate": success_count / n if n else 0,
        "retrieval_recall": recall_count / n if n else 0,
        "planner_accuracy": planner_correct / n if n else 0,
        "latency_avg": sum(latencies) / n if n else 0,
        "tasks_run": n,
    }
    return {"results": results, "summary": summary}


def main():
    parser = argparse.ArgumentParser(description="Evaluate agent on agent_eval.json")
    parser.add_argument("--plan-only", action="store_true", help="Light eval: get_plan only, no model calls")
    parser.add_argument("--tasks", type=str, help="Comma-separated task ids to run")
    parser.add_argument("--metrics", type=str, help="Comma-separated metrics to report")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    tasks_filter = [x.strip() for x in args.tasks.split(",")] if args.tasks else None
    metrics_filter = [x.strip() for x in args.metrics.split(",")] if args.metrics else None

    tasks = _load_tasks(tasks_filter)
    if not tasks:
        print("No tasks to run.", file=sys.stderr)
        sys.exit(1)

    if args.plan_only:
        out = run_plan_only(tasks, metrics_filter)
    else:
        out = run_full(tasks, metrics_filter)

    if args.json:
        print(json.dumps(out, indent=2))
    else:
        summary = out.get("summary", {})
        print("=== Evaluation Summary ===")
        for k, v in summary.items():
            if metrics_filter and k not in metrics_filter:
                continue
            if isinstance(v, float):
                print(f"  {k}: {v:.3f}")
            else:
                print(f"  {k}: {v}")
        print("\n=== Per-task ===")
        for r in out.get("results", []):
            print(f"  {r.get('id')}: planner_ok={r.get('planner_accuracy', '?')} latency={r.get('latency', 0):.2f}s")


if __name__ == "__main__":
    main()
