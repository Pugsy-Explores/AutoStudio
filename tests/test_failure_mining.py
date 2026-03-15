"""Unit tests for agent/failure_mining package."""

import json
import tempfile
from pathlib import Path

import pytest

# Add project root
ROOT = Path(__file__).resolve().parent.parent
import sys
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.failure_mining.failure_taxonomy import FAILURE_TYPES
from agent.failure_mining.failure_extractor import (
    FailureRecord,
    extract_records,
    _detect_loop,
    _extract_symbols_from_text,
)
from agent.failure_mining.failure_clusterer import (
    cluster_by_failure_type,
    cluster_by_failure_type_step_type,
    cluster_all,
)
from agent.failure_mining.root_cause_report import _compute_metrics, generate_report


def test_failure_taxonomy_includes_new_types():
    assert "hallucinated_symbol" in FAILURE_TYPES
    assert "loop_failure" in FAILURE_TYPES
    assert "retrieval_miss" in FAILURE_TYPES


def test_detect_loop_three_consecutive():
    steps = [
        {"action": "SEARCH", "description": "a"},
        {"action": "SEARCH", "description": "b"},
        {"action": "SEARCH", "description": "c"},
    ]
    assert _detect_loop(steps) is True


def test_detect_loop_two_consecutive():
    steps = [
        {"action": "SEARCH", "description": "a"},
        {"action": "SEARCH", "description": "b"},
    ]
    assert _detect_loop(steps) is False


def test_detect_loop_interleaved():
    steps = [
        {"action": "SEARCH", "description": "a"},
        {"action": "EDIT", "description": "b"},
        {"action": "SEARCH", "description": "c"},
    ]
    assert _detect_loop(steps) is False


def test_extract_symbols_from_text():
    text = "The function load_dataset and class StepResult are used."
    syms = _extract_symbols_from_text(text)
    assert "load_dataset" in syms
    assert "StepResult" in syms


def test_extract_records_trajectory_length():
    trajectories = [
        {
            "task_id": "t1",
            "status": "failure",
            "attempts": [
                {
                    "attempt": 0,
                    "steps": [
                        {"action": "SEARCH", "description": "find x"},
                        {"action": "EDIT", "description": "fix x"},
                    ],
                    "diagnosis": None,
                    "strategy": None,
                    "evaluation": {"reason": "patch failed", "status": "FAILURE"},
                },
            ],
        },
    ]
    records = extract_records(trajectories)
    assert len(records) == 1
    assert records[0].trajectory_length == 2
    assert records[0].step_type == "EDIT"
    assert records[0].status == "failure"


def test_extract_records_loop_detection():
    trajectories = [
        {
            "task_id": "t2",
            "status": "failure",
            "attempts": [
                {
                    "attempt": 0,
                    "steps": [
                        {"action": "SEARCH", "description": "a"},
                        {"action": "SEARCH", "description": "b"},
                        {"action": "SEARCH", "description": "c"},
                    ],
                    "diagnosis": None,
                    "strategy": None,
                    "evaluation": {"status": "FAILURE"},
                },
            ],
        },
    ]
    records = extract_records(trajectories)
    assert len(records) == 1
    assert records[0].failure_type == "loop_failure"


def test_extract_records_diagnosis_takes_precedence():
    trajectories = [
        {
            "task_id": "t3",
            "status": "failure",
            "attempts": [
                {
                    "attempt": 0,
                    "steps": [
                        {"action": "SEARCH", "description": "a"},
                        {"action": "SEARCH", "description": "b"},
                        {"action": "SEARCH", "description": "c"},
                    ],
                    "diagnosis": {"failure_type": "retrieval_miss"},
                    "strategy": "expand_search_scope",
                    "evaluation": {"status": "FAILURE"},
                },
            ],
        },
    ]
    records = extract_records(trajectories)
    assert len(records) == 1
    assert records[0].failure_type == "retrieval_miss"


def test_cluster_by_failure_type():
    records = [
        FailureRecord("t1", 0, "retrieval_miss", "", None, 0, 0, 5, "SEARCH", "failure"),
        FailureRecord("t2", 0, "retrieval_miss", "", None, 0, 0, 3, "SEARCH", "failure"),
        FailureRecord("t3", 0, "incorrect_patch", "", None, 0, 0, 4, "EDIT", "failure"),
    ]
    clusters = cluster_by_failure_type(records)
    assert len(clusters["retrieval_miss"]) == 2
    assert len(clusters["incorrect_patch"]) == 1


def test_cluster_by_failure_type_step_type():
    records = [
        FailureRecord("t1", 0, "retrieval_miss", "", None, 0, 0, 5, "SEARCH", "failure"),
        FailureRecord("t2", 0, "retrieval_miss", "", None, 0, 0, 3, "EDIT", "failure"),
    ]
    clusters = cluster_by_failure_type_step_type(records)
    assert "(retrieval_miss, SEARCH)" in clusters
    assert "(retrieval_miss, EDIT)" in clusters


def test_compute_metrics_avg_steps():
    records = [
        FailureRecord("t1", 0, "unknown", "", None, 0, 0, 5, "SEARCH", "success"),
        FailureRecord("t2", 0, "unknown", "", None, 0, 0, 10, "EDIT", "success"),
        FailureRecord("t3", 0, "retrieval_miss", "", None, 0, 0, 15, "SEARCH", "failure"),
    ]
    metrics = _compute_metrics(records)
    assert metrics["avg_steps_success"] == 7.5
    assert metrics["avg_steps_failure"] == 15.0


def test_compute_metrics_loop_failure_rate():
    records = [
        FailureRecord("t1", 0, "loop_failure", "", None, 0, 0, 5, "SEARCH", "failure"),
        FailureRecord("t2", 0, "retrieval_miss", "", None, 0, 0, 3, "SEARCH", "failure"),
    ]
    metrics = _compute_metrics(records)
    assert metrics["loop_failure_rate"] == 0.5


def test_generate_report():
    records = [
        FailureRecord("t1", 0, "retrieval_miss", "", None, 0, 0, 5, "SEARCH", "failure"),
        FailureRecord("t2", 0, "incorrect_patch", "", None, 0, 0, 4, "EDIT", "failure"),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        md_path, json_path = generate_report(records, tmp)
        assert md_path.exists()
        assert json_path.exists()
        data = json.loads(json_path.read_text())
        assert "metrics" in data
        assert "avg_steps_success" in data["metrics"]
        assert "avg_steps_failure" in data["metrics"]
        assert "loop_failure_rate" in data["metrics"]


def test_trajectory_loader_integration(tmp_path):
    """Test trajectory loader with real trajectory files."""
    traj_dir = tmp_path / ".agent_memory" / "trajectories"
    traj_dir.mkdir(parents=True)
    traj_data = {
        "goal": "Fix bug",
        "attempts": [
            {
                "attempt": 0,
                "steps": [{"action": "SEARCH", "description": "find x"}],
                "evaluation": {"status": "FAILURE", "reason": "patch failed"},
                "diagnosis": None,
                "strategy": None,
            }
        ],
        "final_status": "FAILURE",
    }
    (traj_dir / "task_abc.json").write_text(json.dumps(traj_data))

    from agent.failure_mining.trajectory_loader import load_trajectories

    trajs = load_trajectories(tmp_path)
    assert len(trajs) == 1
    assert trajs[0]["task_id"] == "task_abc"
    assert trajs[0]["status"] == "failure"
    assert trajs[0]["final_status"] == "FAILURE"
