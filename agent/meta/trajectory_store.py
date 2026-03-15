"""
Trajectory store: persists execution trajectories for experience reuse.

Schema: {goal, attempts: [{steps, evaluation, diagnosis, strategy}], final_status, timestamp}
Location: .agent_memory/trajectories/<task_id>.json
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

AGENT_MEMORY_DIR = ".agent_memory"
TRAJECTORIES_SUBDIR = "trajectories"


def _trajectories_dir(project_root: str | None = None) -> Path:
    """Return path to .agent_memory/trajectories/."""
    root = Path(project_root or ".").resolve()
    return root / AGENT_MEMORY_DIR / TRAJECTORIES_SUBDIR


def record_attempt(
    task_id: str,
    state: "AgentState",
    evaluation: "EvaluationResult",
    diagnosis: dict | None = None,
    strategy: str | None = None,
    project_root: str | None = None,
) -> None:
    """
    Append one attempt to the trajectory. Creates file if it does not exist.

    Args:
        task_id: Unique task identifier
        state: AgentState after the attempt (completed_steps, step_results)
        evaluation: EvaluationResult from evaluator
        diagnosis: Optional Diagnosis.to_dict() from critic
        strategy: Optional retry strategy used
        project_root: Project root for path resolution
    """
    path = _trajectories_dir(project_root) / f"{task_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)

    # Load existing or create new
    data = _load_raw(path)
    goal = state.instruction or ""
    if not data:
        data = {
            "goal": goal,
            "attempts": [],
            "final_status": None,
            "timestamp": None,
        }

    # Build attempt record
    steps_summary = []
    for step, sr in zip(state.completed_steps or [], state.step_results or []):
        if not isinstance(step, dict):
            continue
        steps_summary.append({
            "action": step.get("action"),
            "description": (step.get("description") or "")[:200],
            "success": getattr(sr, "success", False),
            "classification": getattr(sr, "classification"),
        })

    attempt_record = {
        "steps": steps_summary,
        "evaluation": evaluation.to_dict() if hasattr(evaluation, "to_dict") else evaluation,
        "diagnosis": diagnosis,
        "strategy": strategy,
    }
    data.setdefault("attempts", []).append(attempt_record)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)

    logger.debug("[trajectory_store] recorded attempt for %s", task_id)


def finalize(
    task_id: str,
    final_status: str,
    project_root: str | None = None,
) -> None:
    """
    Set final_status on the trajectory and persist.

    Args:
        task_id: Task identifier
        final_status: SUCCESS | FAILURE | PARTIAL
        project_root: Project root for path resolution
    """
    path = _trajectories_dir(project_root) / f"{task_id}.json"
    if not path.exists():
        return
    data = _load_raw(path)
    if data:
        import time

        data["final_status"] = final_status
        data["timestamp"] = data.get("timestamp") or time.time()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
        logger.debug("[trajectory_store] finalized %s as %s", task_id, final_status)


def load_trajectory(task_id: str, project_root: str | None = None) -> dict | None:
    """Load trajectory by task_id. Returns None if not found."""
    path = _trajectories_dir(project_root) / f"{task_id}.json"
    return _load_raw(path)


def list_trajectories(project_root: str | None = None) -> list[str]:
    """List all trajectory task IDs."""
    d = _trajectories_dir(project_root)
    if not d.exists():
        return []
    return [p.stem for p in d.glob("*.json")]


def _load_raw(path: Path) -> dict | None:
    """Load JSON from path. Returns None if missing or invalid."""
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
