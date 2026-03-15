"""Observability and trace configuration."""

import os
from pathlib import Path


AGENT_MEMORY_DIR = os.getenv("AGENT_MEMORY_DIR", ".agent_memory")
TRACES_SUBDIR = os.getenv("TRACES_SUBDIR", "traces")
MAX_TRACE_SIZE_BYTES = int(os.getenv("MAX_TRACE_SIZE_BYTES", str(500 * 1024)))


def get_trace_dir(project_root: str | None = None) -> Path:
    """Return path to .agent_memory/traces/ under project root."""
    root = Path(project_root or ".").resolve()
    return root / AGENT_MEMORY_DIR / TRACES_SUBDIR
