"""Persist successful solutions for experience reuse."""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

AGENT_MEMORY_DIR = ".agent_memory"
SOLUTIONS_SUBDIR = "solutions"


def _solutions_dir(project_root: str | None = None) -> Path:
    """Return path to .agent_memory/solutions/."""
    root = Path(project_root or ".").resolve()
    return root / AGENT_MEMORY_DIR / SOLUTIONS_SUBDIR


def save_solution(
    task_id: str,
    goal: str,
    files_modified: list[str],
    patch_summary: str,
    success: bool = True,
    project_root: str | None = None,
    developer_accepted: bool | None = None,
) -> None:
    """
    Save a successful solution to JSON.

    Args:
        task_id: Unique task identifier
        goal: The goal/task description
        files_modified: List of file paths modified
        patch_summary: Short summary of the patch pattern
        success: Whether the solution succeeded
        project_root: Project root for path resolution
        developer_accepted: Optional developer acceptance flag
    """
    import time

    solutions_path = _solutions_dir(project_root)
    solutions_path.mkdir(parents=True, exist_ok=True)
    file_path = solutions_path / f"{task_id}.json"

    payload = {
        "task_id": task_id,
        "goal": goal,
        "files_modified": files_modified or [],
        "patch_summary": patch_summary or "",
        "success": success,
        "timestamp": time.time(),
        "developer_accepted": developer_accepted,
    }

    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)

    logger.info("[solution_memory] solution saved for %s", task_id)


def load_solution(task_id: str, project_root: str | None = None) -> dict | None:
    """Load solution by task_id. Returns None if not found."""
    file_path = _solutions_dir(project_root) / f"{task_id}.json"
    if not file_path.exists():
        return None
    try:
        with open(file_path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def list_solutions(project_root: str | None = None) -> list[str]:
    """List all solution task IDs."""
    solutions_path = _solutions_dir(project_root)
    if not solutions_path.exists():
        return []
    return [p.stem for p in solutions_path.glob("*.json")]


def mark_accepted(
    task_id: str,
    accepted: bool,
    project_root: str | None = None,
) -> bool:
    """Mark a solution as developer-accepted or rejected. Returns True if updated."""
    data = load_solution(task_id, project_root)
    if not data:
        return False
    data["developer_accepted"] = accepted
    file_path = _solutions_dir(project_root) / f"{task_id}.json"
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    logger.debug("[solution_memory] marked %s as accepted=%s", task_id, accepted)
    return True
