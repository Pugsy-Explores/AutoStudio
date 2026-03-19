"""PR1: Stage 1 schema tests for Hierarchical Phased Orchestration."""

import re

import pytest

from agent.orchestrator.parent_plan import (
    make_compatibility_parent_plan,
    new_parent_plan_id,
    new_phase_id,
    validate_parent_plan_schema,
)


def test_new_phase_id_format():
    """new_phase_id returns string starting with 'phase_', 8 lowercase hex chars."""
    pid = new_phase_id()
    assert pid.startswith("phase_")
    assert len(pid) == 14  # "phase_" (6) + 8 hex
    suffix = pid[6:]
    assert re.match(r"^[0-9a-f]{8}$", suffix), f"expected 8 hex chars, got {suffix!r}"


def test_new_parent_plan_id_format():
    """new_parent_plan_id returns string starting with 'pplan_', 8 lowercase hex chars."""
    pid = new_parent_plan_id()
    assert pid.startswith("pplan_")
    assert len(pid) == 14  # "pplan_" (6) + 8 hex
    suffix = pid[6:]
    assert re.match(r"^[0-9a-f]{8}$", suffix), f"expected 8 hex chars, got {suffix!r}"


def test_make_compatibility_parent_plan_single_phase():
    """Result has exactly one phase."""
    flat = {"steps": [{"action": "SEARCH", "description": "x"}]}
    parent = make_compatibility_parent_plan(flat, "do something")
    assert len(parent["phases"]) == 1
    assert parent["phases"][0]["phase_index"] == 0


def test_make_compatibility_parent_plan_code_lane():
    """Code flat plan -> phases[0].lane == 'code', compatibility_mode == True."""
    flat = {"steps": [{"action": "SEARCH", "description": "x", "reason": "y"}]}
    parent = make_compatibility_parent_plan(flat, "find validate_plan")
    assert parent["phases"][0]["lane"] == "code"
    assert parent["compatibility_mode"] is True


def test_make_compatibility_parent_plan_docs_lane():
    """Docs flat plan (SEARCH_CANDIDATES + artifact_mode=docs) -> phases[0].lane == 'docs'."""
    flat = {
        "steps": [
            {"action": "SEARCH_CANDIDATES", "artifact_mode": "docs"},
            {"action": "BUILD_CONTEXT", "artifact_mode": "docs"},
            {"action": "EXPLAIN", "artifact_mode": "docs"},
        ]
    }
    parent = make_compatibility_parent_plan(flat, "find readme in docs")
    assert parent["phases"][0]["lane"] == "docs"
    assert parent["compatibility_mode"] is True


def test_make_compatibility_parent_plan_preserves_steps():
    """phases[0].steps == flat_plan['steps']."""
    steps = [{"action": "EDIT", "description": "edit foo"}]
    flat = {"steps": steps}
    parent = make_compatibility_parent_plan(flat, "edit foo")
    assert parent["phases"][0]["steps"] is steps


def test_make_compatibility_parent_plan_preserves_plan_id():
    """phases[0].plan_id == flat_plan['plan_id']."""
    flat = {"steps": [], "plan_id": "plan_abc123"}
    parent = make_compatibility_parent_plan(flat, "do it")
    assert parent["phases"][0]["plan_id"] == "plan_abc123"


def test_make_compatibility_parent_plan_instruction_stored():
    """parent_plan.instruction == instruction (unmodified)."""
    instruction = "Find architecture docs and explain replanner flow"
    flat = {"steps": []}
    parent = make_compatibility_parent_plan(flat, instruction)
    assert parent["instruction"] == instruction


def test_validate_parent_plan_schema_valid():
    """Well-formed plan -> True."""
    parent = {
        "parent_plan_id": "pplan_abc12345",
        "instruction": "do it",
        "decomposition_type": "compatibility",
        "phases": [
            {
                "phase_id": "phase_12345678",
                "phase_index": 0,
                "subgoal": "do it",
                "lane": "code",
                "steps": [],
                "plan_id": "plan_x",
                "validation": {
                    "require_ranked_context": False,
                    "require_explain_success": False,
                    "min_candidates": 0,
                },
                "retry_policy": {"max_parent_retries": 0},
            }
        ],
        "compatibility_mode": True,
    }
    assert validate_parent_plan_schema(parent) is True


def test_validate_parent_plan_schema_rejects_empty_phases():
    """phases=[] -> False."""
    parent = {
        "parent_plan_id": "pplan_abc12345",
        "instruction": "do it",
        "decomposition_type": "compatibility",
        "phases": [],
        "compatibility_mode": True,
    }
    assert validate_parent_plan_schema(parent) is False


def test_validate_parent_plan_schema_rejects_invalid_lane():
    """lane='mixed' -> False."""
    parent = {
        "parent_plan_id": "pplan_abc12345",
        "instruction": "do it",
        "decomposition_type": "compatibility",
        "phases": [
            {
                "phase_id": "phase_12345678",
                "phase_index": 0,
                "subgoal": "do it",
                "lane": "mixed",
                "steps": [],
                "plan_id": "plan_x",
                "validation": {
                    "require_ranked_context": False,
                    "require_explain_success": False,
                    "min_candidates": 0,
                },
                "retry_policy": {"max_parent_retries": 0},
            }
        ],
        "compatibility_mode": True,
    }
    assert validate_parent_plan_schema(parent) is False


def test_validate_parent_plan_schema_rejects_missing_phase_key():
    """Phase missing required key (e.g. lane) -> False."""
    parent = {
        "parent_plan_id": "pplan_abc12345",
        "instruction": "do it",
        "decomposition_type": "compatibility",
        "phases": [
            {
                "phase_id": "phase_12345678",
                "phase_index": 0,
                "subgoal": "do it",
                # "lane" missing
                "steps": [],
                "plan_id": "plan_x",
                "validation": {
                    "require_ranked_context": False,
                    "require_explain_success": False,
                    "min_candidates": 0,
                },
                "retry_policy": {"max_parent_retries": 0},
            }
        ],
        "compatibility_mode": True,
    }
    assert validate_parent_plan_schema(parent) is False


def test_validate_parent_plan_schema_rejects_missing_parent_key():
    """Parent missing required key (e.g. phases) -> False."""
    parent = {
        "parent_plan_id": "pplan_abc12345",
        "instruction": "do it",
        "decomposition_type": "compatibility",
        # "phases" missing
        "compatibility_mode": True,
    }
    assert validate_parent_plan_schema(parent) is False
