"""Smoke tests: runner wiring + one-task harness."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.agent_eval.harness import run_single_task
from tests.agent_eval.suites.core12 import load_core12
from tests.agent_eval.task_specs import resolve_repo_dir


def test_resolve_all_fixture_roots_exist():
    for spec in load_core12():
        assert resolve_repo_dir(spec).is_dir(), spec.task_id


def test_run_single_task_mocked_smoke(tmp_path):
    spec = load_core12()[0]
    src = resolve_repo_dir(spec)
    ws = tmp_path / "ws"
    import shutil

    shutil.copytree(src, ws)
    out = run_single_task(spec, ws, trace_id="smoke-trace")
    assert out.task_id == spec.task_id
    assert out.structural_success is True
    assert out.loop_output_snapshot
    idx = out.extra.get("index") or {}
    assert idx.get("ok") is True
    assert (ws / ".symbol_graph" / "symbols.json").is_file()


def test_runner_writes_summary(run_suite_core12_mocked):
    run_dir, results, summary = run_suite_core12_mocked
    assert summary["total_tasks"] == 12
    assert len(results) == 12
    assert (Path(run_dir) / "summary.json").exists()
    assert (Path(run_dir) / "summary.md").exists()
    data = json.loads((Path(run_dir) / "summary.json").read_text(encoding="utf-8"))
    assert data["success_count"] >= 0
    first = next(Path(run_dir).glob("tasks/*/outcome.json"))
    payload = json.loads(first.read_text(encoding="utf-8"))
    assert "task_id" in payload
    assert "success" in payload
