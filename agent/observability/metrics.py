"""
Runtime metrics for retrieval, patch, test, and retry. In-memory counters + optional JSONL append.
"""

import json
import logging
import os
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_counters: dict[str, float] = {}
_metrics_dir: str | None = None


def _metrics_path(project_root: str | None = None) -> Path | None:
    base = project_root or os.environ.get("SERENA_PROJECT_DIR") or os.getcwd()
    return Path(base) / "data" / "metrics" / "metrics.jsonl"


# Execution loop telemetry (runtime safety hardening)
EXECUTION_LOOP_ATTEMPTS = "execution_loop_attempts"
EXECUTION_LOOP_FAILURES = "execution_loop_failures"
SYNTAX_VALIDATION_FAILURES = "syntax_validation_failures"
ROLLBACK_COUNT = "rollback_count"
STRATEGY_EXPLORER_USAGE = "strategy_explorer_usage"


def record_metric(
    name: str,
    value: float,
    trace_id: str | None = None,
    project_root: str | None = None,
    append_jsonl: bool = True,
) -> None:
    """Record a metric (in-memory and optionally append to data/metrics/metrics.jsonl)."""
    with _lock:
        _counters[name] = _counters.get(name, 0) + value
    if append_jsonl:
        try:
            path = _metrics_path(project_root)
            if path:
                path.parent.mkdir(parents=True, exist_ok=True)
                record = {"metric": name, "value": value, "timestamp": time.time(), "trace_id": trace_id}
                with open(path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record, default=str) + "\n")
        except OSError as e:
            logger.debug("[metrics] append failed: %s", e)


def get_metrics() -> dict[str, float]:
    """Return current in-memory metric counters (copy)."""
    with _lock:
        return dict(_counters)


def reset_metrics() -> None:
    """Clear in-memory counters (e.g. for tests)."""
    with _lock:
        _counters.clear()
