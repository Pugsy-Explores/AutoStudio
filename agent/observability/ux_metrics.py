"""Session-level UX metrics: interaction_latency, steps_per_task, tool_calls, patch_success_rate."""

import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

REPORTS_DIR = Path("reports")
UX_METRICS_FILE = REPORTS_DIR / "ux_metrics.json"


def record_task_metrics(
    task_id: str,
    interaction_latency_seconds: float,
    steps_per_task: int,
    tool_calls: int,
    patch_success: bool | None = None,
    project_root: str | None = None,
) -> None:
    """
    Append one task's metrics to reports/ux_metrics.json.
    """
    root = Path(project_root or ".").resolve()
    metrics_path = root / UX_METRICS_FILE
    metrics_path.parent.mkdir(parents=True, exist_ok=True)

    entry = {
        "task_id": task_id,
        "timestamp": time.time(),
        "interaction_latency": interaction_latency_seconds,
        "steps_per_task": steps_per_task,
        "tool_calls": tool_calls,
    }
    if patch_success is not None:
        entry["patch_success"] = patch_success

    existing: list[dict] = []
    if metrics_path.exists():
        try:
            with open(metrics_path, encoding="utf-8") as f:
                existing = json.load(f)
        except (json.JSONDecodeError, OSError):
            existing = []

    if not isinstance(existing, list):
        existing = []

    existing.append(entry)
    # Keep last 1000 entries
    existing = existing[-1000:]

    try:
        with open(metrics_path, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2, default=str)
    except OSError as e:
        logger.debug("[ux_metrics] write failed: %s", e)


def compute_patch_success_rate(project_root: str | None = None) -> float:
    """Compute patch_success rate from recent metrics. Returns 0.0 if no EDIT tasks."""
    root = Path(project_root or ".").resolve()
    metrics_path = root / UX_METRICS_FILE
    if not metrics_path.exists():
        return 0.0
    try:
        with open(metrics_path, encoding="utf-8") as f:
            entries = json.load(f)
    except (json.JSONDecodeError, OSError):
        return 0.0
    patch_entries = [e for e in entries if "patch_success" in e]
    if not patch_entries:
        return 0.0
    return sum(1 for e in patch_entries if e.get("patch_success")) / len(patch_entries)
