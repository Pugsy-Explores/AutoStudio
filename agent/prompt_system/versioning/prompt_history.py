"""Records which prompt version was used in each run (reads from trace logger)."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from config.observability_config import get_trace_dir


@dataclass
class PromptUsageRecord:
    """One record of prompt usage in a run."""

    prompt_name: str
    version: str
    timestamp: str
    task_name: str | None


def _parse_trace_file(trace_path: Path) -> list[PromptUsageRecord]:
    """Parse a single trace JSON file for prompt usage in stages."""
    records: list[PromptUsageRecord] = []
    if not trace_path.exists() or trace_path.suffix != ".json":
        return records
    try:
        import json

        with open(trace_path, encoding="utf-8") as f:
            data = json.load(f)
        stages = data.get("stages", [])
        for s in stages:
            summary = s.get("summary") or {}
            prompt = summary.get("prompt_name") or summary.get("prompt")
            if prompt:
                records.append(
                    PromptUsageRecord(
                        prompt_name=prompt,
                        version=summary.get("version", "v1"),
                        timestamp=str(data.get("finished_at", datetime.now().isoformat())),
                        task_name=data.get("task_id"),
                    )
                )
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return records


def get_recent_prompt_usage(project_root: str | None = None) -> list[PromptUsageRecord]:
    """
    Get recent prompt usage from trace files in .agent_memory/traces/.
    Returns empty list if no traces or trace format does not include prompt metadata.
    """
    traces_dir = Path(get_trace_dir(project_root))
    if not traces_dir.is_dir():
        return []
    records: list[PromptUsageRecord] = []
    for p in sorted(traces_dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:20]:
        records.extend(_parse_trace_file(p))
    return records
