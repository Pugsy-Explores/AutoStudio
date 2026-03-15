#!/usr/bin/env python3
"""
Phase 10.5 Localization Eval: run localization_tasks.json through localization pipeline.

Metrics:
  - file_accuracy: % correct file in top-k
  - function_accuracy: % correct symbol in top-k
  - top_k_recall: hits at k=1,3,5
  - avg_graph_nodes: mean dependency traversal count
  - avg_tool_calls: placeholder (requires full agent run)

Usage:
  python scripts/run_localization_eval.py              # Full eval
  python scripts/run_localization_eval.py --mock        # Mock: placeholder metrics for CI
  python scripts/run_localization_eval.py --limit 3     # Run first 3 tasks only
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

LOCALIZATION_TASKS_JSON = ROOT / "tests" / "localization_tasks.json"
REPORTS_DIR = ROOT / "reports"
LOCALIZATION_REPORT_JSON = REPORTS_DIR / "localization_report.json"


def _load_tasks(limit: int | None) -> list[dict]:
    """Load localization tasks from JSON."""
    if not LOCALIZATION_TASKS_JSON.exists():
        raise FileNotFoundError(f"Localization tasks not found: {LOCALIZATION_TASKS_JSON}")
    with open(LOCALIZATION_TASKS_JSON, encoding="utf-8") as f:
        tasks = json.load(f)
    if limit is not None:
        tasks = tasks[:limit]
    return tasks


def _normalize_path(p: str) -> str:
    """Normalize path for comparison (resolve relative, handle trailing)."""
    if not p:
        return ""
    return str(Path(p).resolve()).replace("\\", "/")


def _file_in_top_k(candidates: list[dict], correct_file: str, k: int) -> bool:
    """True if correct_file appears in top-k candidates."""
    if not correct_file:
        return False
    norm_correct = _normalize_path(correct_file)
    norm_root = _normalize_path(str(ROOT))
    for c in candidates[:k]:
        f = c.get("file") or ""
        if not f:
            continue
        norm_f = _normalize_path(f)
        if norm_correct in norm_f or norm_f.endswith(norm_correct) or norm_correct.endswith(norm_f.split("/")[-1]):
            return True
        # Also check basename match
        if Path(norm_correct).name == Path(norm_f).name:
            return True
    return False


def _symbol_in_top_k(candidates: list[dict], correct_symbol: str | None, k: int) -> bool:
    """True if correct_symbol appears in top-k candidates."""
    if not correct_symbol:
        return True  # No symbol to check
    correct_lower = correct_symbol.lower()
    for c in candidates[:k]:
        s = (c.get("symbol") or c.get("name") or "").lower()
        if correct_lower in s or s == correct_lower:
            return True
    return False


def run_mock(tasks: list[dict]) -> dict:
    """Mock run: no agent calls, placeholder metrics for CI."""
    n = len(tasks)
    summary = {
        "file_accuracy": 0.0,
        "function_accuracy": 0.0,
        "top_1_recall": 0.0,
        "top_3_recall": 0.0,
        "top_5_recall": 0.0,
        "avg_graph_nodes": 0.0,
        "avg_tool_calls": 0.0,
        "tasks_run": n,
        "mock": True,
    }
    return {"summary": summary, "results": []}


def run_full(tasks: list[dict]) -> dict:
    """Full eval: run localization pipeline for each task."""
    from agent.memory.state import AgentState
    from agent.retrieval.anchor_detector import detect_anchors
    from agent.retrieval.localization.localization_engine import localize_issue
    from agent.retrieval.localization.dependency_traversal import traverse_dependencies
    from agent.retrieval.search_pipeline import hybrid_retrieve

    results = []
    file_hits = 0
    function_hits = 0
    function_total = 0  # tasks that have correct_symbol
    top_1_hits = 0
    top_3_hits = 0
    top_5_hits = 0
    graph_nodes_list: list[int] = []

    state = AgentState(instruction="", current_plan={})
    state.context["project_root"] = str(ROOT)

    for i, task in enumerate(tasks):
        query = task.get("query", "")
        correct_file = task.get("correct_file", "")
        correct_symbol = task.get("correct_symbol")
        task_id = task.get("id", f"loc_{i:03d}")
        print(f"[{i+1}/{len(tasks)}] {task_id}: {query[:50]}...", flush=True)

        try:
            # Get search results
            search_result = hybrid_retrieve(query, state)
            search_results = search_result.get("results") or []

            # Get anchors
            anchors = detect_anchors(search_results, query)
            if not anchors:
                anchors = [{"file": correct_file, "symbol": correct_symbol or ""}]

            # Run localization
            localization_candidates = localize_issue(query, anchors, str(ROOT))

            # Graph nodes count
            anchor_sym = None
            for a in anchors:
                sym = a.get("symbol") or a.get("name_path")
                if sym:
                    anchor_sym = str(sym)
                    break
            if not anchor_sym and anchors:
                f = anchors[0].get("file", "")
                anchor_sym = Path(f).stem if f else None
            if anchor_sym:
                dep_result = traverse_dependencies(anchor_sym, str(ROOT))
                graph_nodes_list.append(dep_result.get("node_count", 0))
            else:
                graph_nodes_list.append(0)

            # Evaluate
            file_hit = _file_in_top_k(localization_candidates, correct_file, 10)
            function_hit = _symbol_in_top_k(localization_candidates, correct_symbol, 10)
            t1 = _file_in_top_k(localization_candidates, correct_file, 1) or _symbol_in_top_k(localization_candidates, correct_symbol, 1)
            t3 = _file_in_top_k(localization_candidates, correct_file, 3) or _symbol_in_top_k(localization_candidates, correct_symbol, 3)
            t5 = _file_in_top_k(localization_candidates, correct_file, 5) or _symbol_in_top_k(localization_candidates, correct_symbol, 5)

            if file_hit:
                file_hits += 1
            if correct_symbol is not None:
                function_total += 1
                if function_hit:
                    function_hits += 1
            if t1:
                top_1_hits += 1
            if t3:
                top_3_hits += 1
            if t5:
                top_5_hits += 1

            results.append({
                "id": task_id,
                "query": query[:80],
                "file_hit": file_hit,
                "function_hit": function_hit,
                "top_1": t1,
                "top_3": t3,
                "top_5": t5,
                "graph_nodes": graph_nodes_list[-1] if graph_nodes_list else 0,
            })
        except Exception as e:
            logger.exception("Localization failed for %s: %s", task_id, e)
            results.append({
                "id": task_id,
                "query": query[:80],
                "file_hit": False,
                "function_hit": False,
                "top_1": False,
                "top_3": False,
                "top_5": False,
                "graph_nodes": 0,
                "error": str(e),
            })
            graph_nodes_list.append(0)

    n = len(tasks)
    summary = {
        "file_accuracy": file_hits / n if n else 0.0,
        "function_accuracy": function_hits / function_total if function_total else 0.0,
        "top_1_recall": top_1_hits / n if n else 0.0,
        "top_3_recall": top_3_hits / n if n else 0.0,
        "top_5_recall": top_5_hits / n if n else 0.0,
        "avg_graph_nodes": sum(graph_nodes_list) / len(graph_nodes_list) if graph_nodes_list else 0.0,
        "avg_tool_calls": 0.0,  # Placeholder: requires full agent run
        "tasks_run": n,
        "mock": False,
    }
    return {"results": results, "summary": summary}


def main():
    parser = argparse.ArgumentParser(description="Phase 10.5 localization eval")
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
        "phase_10_5": {
            "file_accuracy": out.get("summary", {}).get("file_accuracy", 0),
            "function_accuracy": out.get("summary", {}).get("function_accuracy", 0),
            "top_k_recall": {
                "k1": out.get("summary", {}).get("top_1_recall", 0),
                "k3": out.get("summary", {}).get("top_3_recall", 0),
                "k5": out.get("summary", {}).get("top_5_recall", 0),
            },
            "avg_graph_nodes": out.get("summary", {}).get("avg_graph_nodes", 0),
            "avg_tool_calls": out.get("summary", {}).get("avg_tool_calls", 0),
        },
        "results": out.get("results", []),
    }

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOCALIZATION_REPORT_JSON, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print(f"\n=== Localization Eval Report ===")
    print(f"Written to {LOCALIZATION_REPORT_JSON}")
    for k, v in report["phase_10_5"].items():
        if isinstance(v, dict):
            print(f"  {k}: {v}")
        elif isinstance(v, float):
            print(f"  {k}: {v:.3f}")
        else:
            print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
