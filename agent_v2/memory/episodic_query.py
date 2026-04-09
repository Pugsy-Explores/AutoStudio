"""
Phase 5.1 — Read persisted TraceEmitter JSONL execution logs (filter + recency only).

No similarity search, embeddings, or scoring.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Cap directory walks so large episodic roots stay bounded.
MAX_TRACES_SCANNED = 20

_TRACE_PREFIX = "trace_"


def _dt_from_log_timestamp(ts: Any) -> datetime:
    """Parse ISO-8601 log timestamps for ordering (not lexical sort)."""
    if ts is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    s = str(ts).strip()
    if not s:
        return datetime.min.replace(tzinfo=timezone.utc)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _trace_id_from_subdir(name: str) -> str:
    if name.startswith(_TRACE_PREFIX):
        return name[len(_TRACE_PREFIX) :]
    return name


class EpisodicQuery:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = Path(base_dir)

    def query(
        self,
        tool: Optional[str] = None,
        success: Optional[bool] = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Return up to ``limit`` log rows, most recent first (by parsed ISO ``timestamp`` field).

        Scans up to ``MAX_TRACES_SCANNED`` subdirectories named ``trace_*`` under ``base_dir``,
        newest directory mtime first; within each directory reads all ``*.jsonl`` files.
        Each row includes ``trace_id`` (UUID portion after ``trace_``) plus ``_trace_subdir`` / ``_log_file``.
        """
        if limit < 1:
            return []
        if not self.base_dir.is_dir():
            return []

        trace_dirs = [
            p for p in self.base_dir.iterdir() if p.is_dir() and p.name.startswith(_TRACE_PREFIX)
        ]
        trace_dirs.sort(key=lambda p: p.stat().st_mtime_ns, reverse=True)
        trace_dirs = trace_dirs[:MAX_TRACES_SCANNED]

        matched: list[dict[str, Any]] = []
        for td in trace_dirs:
            for jsonl in sorted(td.glob("*.jsonl")):
                try:
                    text = jsonl.read_text(encoding="utf-8")
                except OSError:
                    continue
                for line in text.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(obj, dict):
                        continue
                    if tool is not None and obj.get("tool") != tool:
                        continue
                    if success is not None and obj.get("success") is not success:
                        continue
                    row = dict(obj)
                    row["trace_id"] = _trace_id_from_subdir(td.name)
                    row["_trace_subdir"] = td.name
                    row["_log_file"] = jsonl.name
                    matched.append(row)

        matched.sort(key=lambda r: _dt_from_log_timestamp(r.get("timestamp")), reverse=True)
        return matched[:limit]
