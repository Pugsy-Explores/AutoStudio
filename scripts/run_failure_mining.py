#!/usr/bin/env python3
"""
Phase 16 Failure Mining: run tasks, load trajectories, extract failures, cluster, report.

Pipeline:
  dataset_runner -> trajectory_loader -> failure_extractor -> failure_clusterer -> root_cause_report

Usage:
  python scripts/run_failure_mining.py --tasks 300
  python scripts/run_failure_mining.py --tasks 50 --skip-run   # analyze existing trajectories only
"""

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TASKS_JSON = ROOT / "tests" / "failure_mining_tasks.json"
REPORTS_DIR = ROOT / "reports"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run failure mining pipeline")
    parser.add_argument("--tasks", type=int, default=300, help="Max tasks to run")
    parser.add_argument("--skip-run", action="store_true", help="Skip dataset_runner; analyze existing trajectories")
    parser.add_argument("--use-judge", action="store_true", help="Use LLM to relabel unknown failure types")
    args = parser.parse_args()

    project_root = str(ROOT)

    if not args.skip_run:
        if not TASKS_JSON.exists():
            logger.error("Dataset not found: %s. Create tests/failure_mining_tasks.json first.", TASKS_JSON)
            return 1
        from agent.failure_mining.dataset_runner import run_dataset
        logger.info("[failure_mining] Running dataset (%d tasks)...", args.tasks)
        run_dataset(TASKS_JSON, project_root, max_tasks=args.tasks)
        logger.info("[failure_mining] Dataset run complete")

    from agent.failure_mining.trajectory_loader import load_trajectories
    from agent.failure_mining.failure_extractor import extract_records
    from agent.failure_mining.root_cause_report import generate_report

    logger.info("[failure_mining] Loading trajectories...")
    trajectories = load_trajectories(project_root)
    logger.info("[failure_mining] Loaded %d trajectories", len(trajectories))

    if not trajectories:
        logger.warning("[failure_mining] No trajectories found. Run without --skip-run first.")
        return 0

    logger.info("[failure_mining] Extracting failure records...")
    records = extract_records(trajectories, project_root=project_root)

    if args.use_judge:
        from agent.failure_mining.failure_judge import relabel_unknown_records
        goals = {t.get("task_id", ""): t.get("goal", "") for t in trajectories}
        records = relabel_unknown_records(records, trajectory_goals=goals)

    logger.info("[failure_mining] Generating report...")
    md_path, json_path = generate_report(records, REPORTS_DIR)
    logger.info("[failure_mining] Report written: %s", md_path)
    logger.info("[failure_mining] Stats: %s", json_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
