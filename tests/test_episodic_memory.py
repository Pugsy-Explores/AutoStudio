"""Phase 5.1 — episodic log persistence and EpisodicQuery."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from agent_v2.config import get_agent_v2_episodic_log_dir
from agent_v2.memory.episodic_query import EpisodicQuery
from agent_v2.runtime.trace_emitter import TraceEmitter
from agent_v2.schemas.execution import (
    ExecutionMetadata,
    ExecutionOutput,
    ExecutionResult,
)
from agent_v2.schemas.execution_task import ExecutionTask


def _ok_result(step_id: str) -> ExecutionResult:
    return ExecutionResult(
        step_id=step_id,
        success=True,
        status="success",
        output=ExecutionOutput(summary="ok", data={}),
        error=None,
        metadata=ExecutionMetadata(
            tool_name="search", duration_ms=1, timestamp="2026-01-01T00:00:00Z"
        ),
    )


def test_trace_emitter_writes_jsonl_with_tool(tmp_path: Path) -> None:
    emitter = TraceEmitter(log_dir=str(tmp_path))
    task = ExecutionTask(id="task-a", tool="open_file", arguments={"path": "x.py"})
    emitter.record_execution_attempt(task, _ok_result("task-a"), attempt_number=1, duration_ms=5)

    subdirs = list(tmp_path.glob("trace_*"))
    assert len(subdirs) == 1
    jsonl_files = list(subdirs[0].glob("*.jsonl"))
    assert len(jsonl_files) == 1
    line = jsonl_files[0].read_text(encoding="utf-8").strip()
    row = json.loads(line)
    assert row["task_id"] == "task-a"
    assert row["tool"] == "open_file"
    assert row["success"] is True


def test_episodic_query_filters_tool_and_success(tmp_path: Path) -> None:
    d1 = tmp_path / "trace_aaa"
    d1.mkdir(parents=True)
    (d1 / "t1.jsonl").write_text(
        json.dumps(
            {
                "task_id": "1",
                "tool": "search",
                "attempt_number": 1,
                "arguments": {},
                "success": True,
                "error_type": None,
                "error_message": None,
                "timestamp": "2026-01-02T10:00:00Z",
                "duration_ms": 1,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (d1 / "t2.jsonl").write_text(
        json.dumps(
            {
                "task_id": "2",
                "tool": "edit",
                "attempt_number": 1,
                "arguments": {},
                "success": False,
                "error_type": "tool_error",
                "error_message": "x",
                "timestamp": "2026-01-02T11:00:00Z",
                "duration_ms": 1,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    q = EpisodicQuery(tmp_path)
    search_ok = q.query(tool="search", success=True, limit=10)
    assert len(search_ok) == 1
    assert search_ok[0]["task_id"] == "1"
    assert search_ok[0]["trace_id"] == "aaa"

    edit_fail = q.query(tool="edit", success=False, limit=10)
    assert len(edit_fail) == 1
    assert edit_fail[0]["task_id"] == "2"
    assert edit_fail[0]["trace_id"] == "aaa"


def test_episodic_query_recency_by_timestamp(tmp_path: Path) -> None:
    d1 = tmp_path / "trace_old"
    d1.mkdir(parents=True)
    (d1 / "a.jsonl").write_text(
        json.dumps(
            {
                "task_id": "old",
                "tool": "search",
                "attempt_number": 1,
                "arguments": {},
                "success": True,
                "error_type": None,
                "error_message": None,
                "timestamp": "2026-01-01T00:00:00Z",
                "duration_ms": 1,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    d2 = tmp_path / "trace_new"
    d2.mkdir(parents=True)
    (d2 / "b.jsonl").write_text(
        json.dumps(
            {
                "task_id": "new",
                "tool": "search",
                "attempt_number": 1,
                "arguments": {},
                "success": True,
                "error_type": None,
                "error_message": None,
                "timestamp": "2026-01-03T00:00:00Z",
                "duration_ms": 1,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    q = EpisodicQuery(tmp_path)
    rows = q.query(tool="search", limit=10)
    assert [r["task_id"] for r in rows] == ["new", "old"]
    assert rows[0]["trace_id"] == "new"


def test_episodic_query_timestamp_sort_parsed_not_lexical(tmp_path: Path) -> None:
    """Earlier calendar month can sort after later month if lexical order differed — datetime fixes it."""
    d = tmp_path / "trace_one"
    d.mkdir(parents=True)
    (d / "x.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "task_id": "feb",
                        "tool": "search",
                        "attempt_number": 1,
                        "arguments": {},
                        "success": True,
                        "error_type": None,
                        "error_message": None,
                        "timestamp": "2026-02-01T00:00:00Z",
                        "duration_ms": 1,
                    }
                ),
                json.dumps(
                    {
                        "task_id": "oct",
                        "tool": "search",
                        "attempt_number": 1,
                        "arguments": {},
                        "success": True,
                        "error_type": None,
                        "error_message": None,
                        "timestamp": "2026-10-01T00:00:00Z",
                        "duration_ms": 1,
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    q = EpisodicQuery(tmp_path)
    rows = q.query(tool="search", limit=10)
    assert [r["task_id"] for r in rows] == ["oct", "feb"]


def test_episodic_query_max_traces_scanned_skips_oldest_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agent_v2.memory.episodic_query.MAX_TRACES_SCANNED", 3)
    # trace_batch0 oldest … trace_batch4 newest; only 3 newest dirs scanned (batch4,3,2)
    t0 = int(time.time())
    rows = []
    for i in range(5):
        d = tmp_path / f"trace_batch{i}"
        d.mkdir()
        rows.append(d)
    oldest = tmp_path / "trace_batch0"
    (oldest / "only.jsonl").write_text(
        json.dumps(
            {
                "task_id": "hidden",
                "tool": "search",
                "attempt_number": 1,
                "arguments": {},
                "success": True,
                "error_type": None,
                "error_message": None,
                "timestamp": "2026-06-01T00:00:00Z",
                "duration_ms": 1,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    newest = tmp_path / "trace_batch4"
    (newest / "hit.jsonl").write_text(
        json.dumps(
            {
                "task_id": "seen",
                "tool": "search",
                "attempt_number": 1,
                "arguments": {},
                "success": True,
                "error_type": None,
                "error_message": None,
                "timestamp": "2026-01-01T00:00:00Z",
                "duration_ms": 1,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    # Set dir mtimes after writes so file creation does not reorder directories
    for i, d in enumerate(rows):
        os.utime(d, (t0 + i * 10_000_000, t0 + i * 10_000_000))
    q = EpisodicQuery(tmp_path)
    rows = q.query(tool="search", limit=10)
    ids = {r["task_id"] for r in rows}
    assert "hidden" not in ids
    assert "seen" in ids


def test_get_agent_v2_episodic_log_dir_empty_disables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_V2_EPISODIC_LOG_DIR", "")
    # Re-import would cache module; call function which reads os.environ at call time
    assert get_agent_v2_episodic_log_dir() is None
