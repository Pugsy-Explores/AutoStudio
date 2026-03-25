#!/usr/bin/env python3
"""
Phase 5 Capability Eval: run dev_tasks.json through agent, write reports/eval_report.json.

Metrics:
  - task_success_rate
  - retrieval_recall
  - planner_accuracy
  - edit_success_rate
  - avg_latency
  - avg_files_modified
  - avg_steps_per_task
  - avg_patch_size

Usage:
  python scripts/run_capability_eval.py              # Full eval (runs agent)
  python scripts/run_capability_eval.py --mock       # Mock: no agent, placeholder metrics for CI
  python scripts/run_capability_eval.py --limit 5    # Run first 5 tasks only
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

DEV_TASKS_JSON = ROOT / "tests" / "dev_tasks.json"
REPORTS_DIR = ROOT / "reports"
EVAL_REPORT_JSON = REPORTS_DIR / "eval_report.json"


def _load_tasks(limit: int | None) -> list[dict]:
    """Load dev tasks from JSON."""
    if not DEV_TASKS_JSON.exists():
        raise FileNotFoundError(f"Dev tasks not found: {DEV_TASKS_JSON}")
    with open(DEV_TASKS_JSON, encoding="utf-8") as f:
        tasks = json.load(f)
    if limit is not None:
        tasks = tasks[:limit]
    return tasks


def _planner_accuracy(task: dict, plan: dict) -> bool:
    """True if plan contains all expected actions in valid order."""
    steps = plan.get("steps") or []
    actions = [s.get("action", "").upper() for s in steps]
    expected = task.get("expected_actions") or []
    if not expected:
        return True
    for exp in expected:
        if exp.upper() not in actions:
            return False
    # SEARCH before EDIT when both expected
    if "SEARCH" in expected and "EDIT" in expected:
        if "SEARCH" in actions and "EDIT" in actions:
            return actions.index("SEARCH") < actions.index("EDIT")
    return True


def _retrieval_recall(task: dict, state) -> bool:
    """True if ranked_context has relevant content (heuristic: non-empty when expect_edit)."""
    ranked = (state.context or {}).get("ranked_context") or []
    if task.get("expect_edit") and not ranked:
        return False
    return True


def _task_success(task: dict, state) -> bool:
    """True if all steps succeeded."""
    if not state.step_results:
        return False
    return all(r.success for r in state.step_results)


def _edit_success(task: dict, state) -> bool | None:
    """True if EDIT step succeeded. None if task has no expected EDIT."""
    if not task.get("expect_edit"):
        return None
    for r in state.step_results or []:
        if r.action == "EDIT":
            return r.success
    return False


def run_mock(tasks: list[dict]) -> dict:
    """Mock run: no agent calls, placeholder metrics for CI."""
    n = len(tasks)
    summary = {
        "task_success_rate": 0.0,
        "retrieval_recall": 0.0,
        "planner_accuracy": 0.0,
        "edit_success_rate": 0.0,
        "avg_latency": 0.0,
        "avg_files_modified": 0.0,
        "avg_steps_per_task": 0.0,
        "avg_patch_size": 0.0,
        "tasks_run": n,
        "mock": True,
    }
    return {"summary": summary, "results": []}


def run_full(tasks: list[dict]) -> dict:
    """Full eval: run_agent for each task, aggregate metrics."""
    from tests.utils.runtime_adapter import run_agent
    from agent.orchestrator.plan_resolver import get_plan

    results = []
    success_count = 0
    recall_count = 0
    planner_correct = 0
    edit_tasks = [t for t in tasks if t.get("expect_edit")]
    edit_success_count = 0
    latencies = []
    files_modified_list = []
    steps_per_task_list = []
    patch_sizes = []

    for i, task in enumerate(tasks):
        instruction = task.get("instruction", "")
        task_id = task.get("id", f"task_{i}")
        print(f"[{i+1}/{len(tasks)}] {task_id}: {instruction[:50]}...", flush=True)

        t0 = time.perf_counter()
        try:
            state = run_agent(instruction)
        except Exception as e:
            logger.exception("run_agent failed: %s", e)
            state = None
        latency = time.perf_counter() - t0
        latencies.append(latency)

        if state is None:
            results.append({
                "id": task_id,
                "task_success": False,
                "retrieval_recall": False,
                "planner_accuracy": False,
                "edit_success": None,
                "latency": latency,
                "steps": 0,
                "files_modified": 0,
                "patch_size": 0,
            })
            continue

        plan = state.current_plan or {}
        acc = _planner_accuracy(task, plan)
        if acc:
            planner_correct += 1

        success = _task_success(task, state)
        if success:
            success_count += 1

        recall = _retrieval_recall(task, state)
        if recall:
            recall_count += 1

        edit_ok = _edit_success(task, state)
        if edit_ok is True:
            edit_success_count += 1

        steps = len(state.step_results or [])
        steps_per_task_list.append(steps)

        fm_count = 0
        patch_count = 0
        for r in state.step_results or []:
            if r.files_modified:
                fm_count += len(r.files_modified)
            if r.patch_size is not None:
                patch_count += r.patch_size
        if fm_count > 0:
            files_modified_list.append(fm_count)
        if patch_count > 0:
            patch_sizes.append(patch_count)

        results.append({
            "id": task_id,
            "task_success": success,
            "retrieval_recall": recall,
            "planner_accuracy": acc,
            "edit_success": edit_ok,
            "latency": latency,
            "steps": steps,
            "files_modified": fm_count,
            "patch_size": patch_count,
        })

    n = len(tasks)
    n_edit = len(edit_tasks)
    summary = {
        "task_success_rate": success_count / n if n else 0,
        "retrieval_recall": recall_count / n if n else 0,
        "planner_accuracy": planner_correct / n if n else 0,
        "edit_success_rate": edit_success_count / n_edit if n_edit else 0,
        "avg_latency": sum(latencies) / n if n else 0,
        "avg_files_modified": sum(files_modified_list) / len(files_modified_list) if files_modified_list else 0,
        "avg_steps_per_task": sum(steps_per_task_list) / n if n else 0,
        "avg_patch_size": sum(patch_sizes) / len(patch_sizes) if patch_sizes else 0,
        "tasks_run": n,
        "mock": False,
    }
    return {"results": results, "summary": summary}


def main():
    parser = argparse.ArgumentParser(description="Phase 5 capability eval: dev_tasks -> eval_report.json")
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
        "results": out.get("results", []),
    }

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(EVAL_REPORT_JSON, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print(f"\n=== Capability Eval Report ===")
    print(f"Written to {EVAL_REPORT_JSON}")
    for k, v in report["metrics"].items():
        if isinstance(v, float):
            print(f"  {k}: {v:.3f}")
        else:
            print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
