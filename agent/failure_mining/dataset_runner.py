"""Run task dataset through run_autonomous and store run metadata."""

import json
import logging
from pathlib import Path

from agent.meta.trajectory_store import AGENT_MEMORY_DIR, TRAJECTORIES_SUBDIR

logger = logging.getLogger(__name__)

MAX_TASKS = 300
MAX_RETRIES = 3
FAILURE_RUNS_SUBDIR = "failure_runs"


def _failure_runs_dir(project_root: str | Path) -> Path:
    """Return path to .agent_memory/failure_runs/."""
    root = Path(project_root).resolve()
    return root / AGENT_MEMORY_DIR / FAILURE_RUNS_SUBDIR


def run_dataset(
    tasks_path: str | Path,
    project_root: str | Path,
    max_tasks: int = MAX_TASKS,
    max_retries: int = MAX_RETRIES,
) -> list[dict]:
    """
    Load tasks from JSON, run each through run_autonomous, store metadata.

    Returns list of run metadata dicts (one per task).
    """
    from agent.autonomous.agent_loop import run_autonomous

    tasks_path = Path(tasks_path)
    project_root = Path(project_root).resolve()
    if not tasks_path.exists():
        raise FileNotFoundError(f"Tasks file not found: {tasks_path}")

    with open(tasks_path, encoding="utf-8") as f:
        tasks = json.load(f)

    if not isinstance(tasks, list):
        tasks = [tasks]

    tasks = tasks[:max_tasks]
    runs_dir = _failure_runs_dir(project_root)
    runs_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []
    for i, task in enumerate(tasks):
        goal = task.get("goal", task.get("task", task.get("instruction", "")))
        task_id_from_dataset = task.get("id", f"task_{i}")
        success_criteria = task.get("success_criteria")

        try:
            result = run_autonomous(
                goal,
                project_root=str(project_root),
                max_retries=max_retries,
                success_criteria=success_criteria,
            )
        except Exception as e:
            logger.exception("run_autonomous failed for %s: %s", task_id_from_dataset, e)
            result = {"evaluation": {"status": "FAILURE"}, "attempts": 1}

        evaluation = result.get("evaluation") or {}
        status_raw = evaluation.get("status", "FAILURE")
        status = "success" if status_raw == "SUCCESS" else "failure"
        attempts = result.get("attempts", 1)
        trajectory_length = result.get("completed_steps", 0)
        task_id = result.get("task_id", task_id_from_dataset)

        trajectory_file = str(
            project_root / AGENT_MEMORY_DIR / TRAJECTORIES_SUBDIR / f"{task_id}.json"
        )

        run_meta = {
            "task_id": task_id,
            "dataset_id": task_id_from_dataset,
            "status": status,
            "attempts": attempts,
            "trajectory_length": trajectory_length,
            "trajectory_file": trajectory_file,
        }

        run_path = runs_dir / f"{task_id}.json"
        with open(run_path, "w", encoding="utf-8") as f:
            json.dump(run_meta, f, indent=2)

        results.append(run_meta)
        logger.debug("[dataset_runner] %s: status=%s attempts=%s", task_id, status, attempts)

    return results
