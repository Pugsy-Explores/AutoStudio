"""
Execution trajectory store (schema v2). Append-only JSONL per run.
Sanitizes and truncates patch/test_output. Used by execution loop for checkpointing.
"""

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from config.agent_runtime import TRAJECTORY_STORE_DIR, TRAJECTORY_STORE_ENABLED

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "v2"
MAX_CHARS = 2000

_SENSITIVE_PATTERN = re.compile(
    r"\.env|secrets/|token=|[Pp]assword=|[Aa]pi[_-]?[Kk]ey=|[\w-]+=.*secret",
    re.IGNORECASE,
)


def _strip_sensitive(text: str) -> str:
    """Remove sensitive-looking substrings."""
    if not text:
        return ""
    return _SENSITIVE_PATTERN.sub("[REDACTED]", text)


def _truncate(text: str, max_chars: int = MAX_CHARS) -> str:
    """Truncate with suffix."""
    if not text or len(text) <= max_chars:
        return text or ""
    return text[:max_chars] + " ... [truncated]"


def append_trajectory(
    goal: str,
    plan: list,
    retrieved_files: list,
    patch: str,
    test_output: str,
    failure_type: str | None,
    retry_strategy: str | None,
    success: bool,
    project_root: str | None = None,
    working_diff: str | None = None,
    retrieval_context: str | None = None,
) -> None:
    """
    Append one execution attempt to the v2 trajectory JSONL.
    Sanitizes and truncates patch, test_output. Optional context checkpoint fields.
    """
    if not TRAJECTORY_STORE_ENABLED:
        return
    root = Path(project_root or ".").resolve()
    store_dir = root / TRAJECTORY_STORE_DIR
    store_dir.mkdir(parents=True, exist_ok=True)
    path = store_dir / "trajectories.jsonl"

    patch_safe = _truncate(_strip_sensitive(patch or ""))
    test_safe = _truncate(_strip_sensitive(test_output or ""))
    record = {
        "schema_version": SCHEMA_VERSION,
        "goal": (goal or "")[:500],
        "plan": plan[:50] if isinstance(plan, list) else [],
        "retrieved_files": (retrieved_files or [])[:100],
        "patch": patch_safe,
        "test_output": test_safe,
        "failure_type": failure_type or "",
        "retry_strategy": retry_strategy or "",
        "success": success,
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
    }
    if working_diff is not None:
        record["working_diff"] = _truncate(_strip_sensitive(working_diff), 1000)
    if retrieval_context is not None:
        record["retrieval_context"] = _truncate(_strip_sensitive(retrieval_context), 1000)

    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except OSError as e:
        logger.warning("[execution_trajectory_store] append failed: %s", e)
