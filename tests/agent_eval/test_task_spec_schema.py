"""Schema validation for Stage 12 task specs."""

from __future__ import annotations

import pytest

from tests.agent_eval.suites.core12 import CORE12_TASKS, load_core12
from tests.agent_eval.task_specs import TaskSpec, validate_suite, validate_task_spec


def test_core12_loads_twelve_tasks():
    tasks = load_core12()
    assert len(tasks) == 12
    ids = [t.task_id for t in tasks]
    assert len(ids) == len(set(ids))


def test_validate_each_core12_task():
    for t in CORE12_TASKS:
        validate_task_spec(t)


def test_validate_suite_core12():
    validate_suite(load_core12())


def test_invalid_task_rejected():
    bad = TaskSpec(
        task_id="",
        layer="mini_repo",
        repo_id="x",
        repo_path="mini_repos/mr01_arch",
        instruction="do something",
    )
    with pytest.raises(ValueError):
        validate_task_spec(bad)


def test_public_outcome_keys():
    from tests.agent_eval.harness import TaskOutcome

    o = TaskOutcome(
        task_id="t",
        success=False,
        validation_passed=False,
        retries_used=0,
        replans_used=0,
        attempts_total=2,
        failure_class=None,
        files_changed=[],
        diff_stat={"insertions": 0, "deletions": 0},
        unrelated_files_changed=[],
        bad_edit_patterns=[],
        retrieval_miss_signals=[],
        notes="",
    )
    pub = o.to_public_dict()
    assert set(pub.keys()) == {
        "task_id",
        "success",
        "validation_passed",
        "retries_used",
        "replans_used",
        "attempts_total",
        "failure_class",
        "files_changed",
        "diff_stat",
        "unrelated_files_changed",
        "bad_edit_patterns",
        "retrieval_miss_signals",
        "notes",
    }
