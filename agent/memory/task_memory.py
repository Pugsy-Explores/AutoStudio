"""Persist long-running task state for resume and audit."""

import json
import logging
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

AGENT_MEMORY_DIR = ".agent_memory"
TASKS_SUBDIR = "tasks"


def _tasks_dir(project_root: str | None = None) -> Path:
    """Return path to .agent_memory/tasks/."""
    root = Path(project_root or ".").resolve()
    return root / AGENT_MEMORY_DIR / TASKS_SUBDIR


def save_task(
    task_id: str | None = None,
    instruction: str = "",
    plan: dict | None = None,
    steps: list | None = None,
    patches: list | None = None,
    files_modified: list | None = None,
    errors_encountered: list | None = None,
    results: dict | None = None,
    project_root: str | None = None,
) -> str:
    """
    Save task state to JSON.
    Returns task_id (generated if not provided).
    """
    tid = task_id or str(uuid.uuid4())
    tasks_path = _tasks_dir(project_root)
    tasks_path.mkdir(parents=True, exist_ok=True)
    file_path = tasks_path / f"{tid}.json"

    payload = {
        "task_id": tid,
        "instruction": instruction,
        "plan": plan or {},
        "steps": steps or [],
        "patches": patches or [],
        "files_modified": files_modified or [],
        "errors_encountered": errors_encountered or [],
        "results": results or {},
    }

    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)

    try:
        from agent.memory.task_index import index_task

        index_task(
            task_id=tid,
            instruction=instruction,
            files_modified=files_modified or [],
            errors=errors_encountered or [],
            project_root=project_root,
        )
    except Exception as e:
        logger.debug("[task_memory] task_index update skipped: %s", e)

    logger.info("[task_memory] task saved")
    return tid


def load_task(task_id: str, project_root: str | None = None) -> dict | None:
    """Load task state by task_id. Returns None if not found."""
    file_path = _tasks_dir(project_root) / f"{task_id}.json"
    if not file_path.exists():
        return None
    try:
        with open(file_path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def list_tasks(project_root: str | None = None) -> list[str]:
    """List all task IDs (without .json extension)."""
    tasks_path = _tasks_dir(project_root)
    if not tasks_path.exists():
        return []
    return [p.stem for p in tasks_path.glob("*.json")]
