"""Load trajectories from trajectory store; tag each with status."""

from pathlib import Path

from agent.meta.trajectory_store import load_trajectory, list_trajectories

EVALUATION_STATUS_SUCCESS = "SUCCESS"


def load_trajectories(directory: str | Path) -> list[dict]:
    """
    Load all trajectories from the trajectory store under the given project root.
    Tags each trajectory with "status": "success" | "failure" derived from final_status.

    Args:
        directory: Project root (path to repo). Trajectories are loaded from
            .agent_memory/trajectories/ under this path.

    Returns:
        List of trajectory dicts, each with an added "status" key.
    """
    project_root = str(Path(directory).resolve())
    task_ids = list_trajectories(project_root)
    out: list[dict] = []
    for tid in task_ids:
        traj = load_trajectory(tid, project_root)
        if traj is None:
            continue
        traj["task_id"] = tid
        final = traj.get("final_status")
        status = "success" if final == EVALUATION_STATUS_SUCCESS else "failure"
        traj["status"] = status
        out.append(traj)
    return out
