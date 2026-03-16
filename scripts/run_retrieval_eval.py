#!/usr/bin/env python3
"""Retrieval evaluation: recall@k, rerank gain, latency.

Runs hybrid retrieval + optional reranker on tasks from failure_mining_tasks.json.
Reports recall@10, recall@20, avg latency, candidate counts.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TASKS_JSON = ROOT / "tests" / "failure_mining_tasks.json"
REPORTS_DIR = ROOT / "reports"


def _extract_expected_files(goal: str) -> set[str]:
    """Heuristic: extract file paths mentioned in goal (e.g. tests/conftest.py)."""
    import re
    # Match path-like strings: word/word.ext, path/to/file.py
    pattern = r"[\w\-]+(?:/[\w\-\.]+)+"
    return set(re.findall(pattern, goal))


def run_retrieval_eval(
    tasks_path: Path | None = None,
    limit: int | None = None,
    project_root: str | None = None,
) -> dict:
    """Run retrieval eval; return metrics dict."""
    tasks_path = tasks_path or TASKS_JSON
    if not tasks_path.is_file():
        logger.error("Tasks file not found: %s", tasks_path)
        return {}

    with open(tasks_path, encoding="utf-8") as f:
        tasks = json.load(f)
    if limit:
        tasks = tasks[:limit]

    project_root = project_root or os.environ.get("SERENA_PROJECT_DIR") or str(ROOT)
    from agent.memory.state import AgentState
    from agent.retrieval.search_pipeline import hybrid_retrieve

    state = AgentState(instruction="", current_plan={"steps": []})
    state.context["project_root"] = project_root

    latencies: list[float] = []
    candidate_counts: list[int] = []
    recall_at_10: list[float] = []
    recall_at_20: list[float] = []

    for i, task in enumerate(tasks):
        goal = task.get("goal") or task.get("instruction") or ""
        if not goal:
            continue
        t0 = time.monotonic()
        out = hybrid_retrieve(goal, state)
        elapsed_ms = (time.monotonic() - t0) * 1000
        results = out.get("results") or []
        latencies.append(elapsed_ms)
        candidate_counts.append(len(results))

        expected = _extract_expected_files(goal)
        if expected:
            files_in_results = {r.get("file", "").replace(project_root, "").lstrip("/") for r in results}
            top10 = set(r.get("file", "").replace(project_root, "").lstrip("/") for r in results[:10])
            top20 = set(r.get("file", "").replace(project_root, "").lstrip("/") for r in results[:20])
            r10 = len(expected & top10) / len(expected) if expected else 0.0
            r20 = len(expected & top20) / len(expected) if expected else 0.0
            recall_at_10.append(r10)
            recall_at_20.append(r20)

        if (i + 1) % 20 == 0:
            logger.info("Processed %d/%d tasks", i + 1, len(tasks))

    n = len(latencies)
    metrics = {
        "tasks_run": n,
        "avg_latency_ms": round(sum(latencies) / n, 2) if n else 0,
        "p95_latency_ms": round(sorted(latencies)[int(n * 0.95)] if n else 0, 2),
        "avg_candidates": round(sum(candidate_counts) / n, 1) if n else 0,
        "recall_at_10": round(sum(recall_at_10) / len(recall_at_10), 4) if recall_at_10 else 0.0,
        "recall_at_20": round(sum(recall_at_20) / len(recall_at_20), 4) if recall_at_20 else 0.0,
    }
    return metrics


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Retrieval evaluation")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of tasks")
    parser.add_argument("--output", type=str, default=None, help="Output JSON path")
    parser.add_argument("--project-root", type=str, default=None)
    args = parser.parse_args()

    metrics = run_retrieval_eval(limit=args.limit, project_root=args.project_root)
    if not metrics:
        sys.exit(1)

    print(json.dumps(metrics, indent=2))
    if args.output:
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        out_path = Path(args.output)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(metrics, f, indent=2)
        logger.info("Wrote %s", out_path)


if __name__ == "__main__":
    main()
