"""Stage 16 — hierarchical phase-shape repair tests for docs-consistency and explain-artifact tasks."""

from __future__ import annotations

import pytest

from tests.agent_eval.harness import (
    _build_phase_1_steps,
    _is_docs_consistency_task,
    _is_explain_artifact_task,
    _parent_plan_for_spec,
    _two_phase_parent_plan,
)
from tests.agent_eval.suites.core12 import load_core12
from tests.hierarchical_test_locks import (
    HIERARCHICAL_LOOP_OUTPUT_KEYS,
    assert_compat_loop_output_has_no_hierarchical_keys,
)


def test_docs_consistency_phase1_has_edit_steps():
    """Docs-consistency tasks must have phase 1 with SEARCH and EDIT (not EXPLAIN-only)."""
    specs = load_core12()
    docs_specs = [s for s in specs if _is_docs_consistency_task(s)]
    assert len(docs_specs) >= 3, "core12 has 3 docs-consistency tasks"
    for spec in docs_specs:
        plan = _parent_plan_for_spec(spec)
        phases = plan.get("phases") or []
        assert len(phases) == 2
        p1_steps = phases[1].get("steps") or []
        actions = [(s.get("action") or "").upper() for s in p1_steps]
        assert "EDIT" in actions, f"{spec.task_id}: phase 1 must include EDIT"
        assert "SEARCH" in actions, f"{spec.task_id}: phase 1 must include SEARCH for grounding"


def test_explain_artifact_phase1_has_write_artifact():
    """Explain-artifact tasks must have phase 1 with SEARCH, EXPLAIN, WRITE_ARTIFACT."""
    specs = load_core12()
    explain_specs = [s for s in specs if _is_explain_artifact_task(s)]
    assert len(explain_specs) >= 1
    for spec in explain_specs:
        plan = _parent_plan_for_spec(spec)
        phases = plan.get("phases") or []
        assert len(phases) == 2
        p1_steps = phases[1].get("steps") or []
        actions = [(s.get("action") or "").upper() for s in p1_steps]
        assert "WRITE_ARTIFACT" in actions, f"{spec.task_id}: phase 1 must include WRITE_ARTIFACT"
        assert "EXPLAIN" in actions
        assert "SEARCH" in actions
        write_step = next((s for s in p1_steps if (s.get("action") or "").upper() == "WRITE_ARTIFACT"), None)
        assert write_step is not None
        assert write_step.get("artifact_path") == spec.expected_artifacts[0]


def test_compat_plan_unchanged():
    """Compat tasks must retain single-phase plan; no hierarchical phase shape."""
    specs = load_core12()
    compat_specs = [s for s in specs if s.orchestration_path == "compat"]
    for spec in compat_specs:
        plan = _parent_plan_for_spec(spec)
        assert plan.get("compatibility_mode") is True
        phases = plan.get("phases") or []
        assert len(phases) == 1


def test_hierarchical_invariants_two_phases():
    """Hierarchical plans must have exactly 2 phases with docs then code lanes."""
    specs = load_core12()
    hier_specs = [s for s in specs if s.orchestration_path == "hierarchical"]
    for spec in hier_specs:
        plan = _parent_plan_for_spec(spec)
        assert plan.get("compatibility_mode") is False
        phases = plan.get("phases") or []
        assert len(phases) == 2
        assert phases[0].get("lane") == "docs"
        assert phases[1].get("lane") == "code"
        assert plan.get("decomposition_type") == "two_phase_docs_code"


def test_no_new_compat_loop_output_keys():
    """Compat path must not add hierarchical-only keys to loop_output."""
    from tests.agent_eval.harness import run_structural_agent
    from tests.agent_eval.task_specs import resolve_repo_dir

    specs = load_core12()
    compat_spec = next(s for s in specs if s.orchestration_path == "compat")
    root = resolve_repo_dir(compat_spec)
    result = run_structural_agent(compat_spec, str(root))
    loop_out = result.get("loop_output_snapshot") or {}
    assert_compat_loop_output_has_no_hierarchical_keys(loop_out)
    for k in HIERARCHICAL_LOOP_OUTPUT_KEYS:
        assert k not in loop_out


def test_default_phase1_when_no_spec():
    """When spec is None, phase 1 defaults to EXPLAIN-only (backwards compat)."""
    plan = _two_phase_parent_plan("Find docs and explain", parent_plan_id="test", spec=None)
    phases = plan.get("phases") or []
    p1_steps = phases[1].get("steps") or []
    assert len(p1_steps) == 1
    assert (p1_steps[0].get("action") or "").upper() == "EXPLAIN"


def test_build_phase_1_steps_docs_consistency():
    """_build_phase_1_steps returns planner output for docs-consistency."""
    from tests.agent_eval.suites.core12 import CORE12_TASKS

    spec = next(s for s in CORE12_TASKS if s.task_id == "core12_mini_docs_version")
    steps = _build_phase_1_steps("Make README and constants agree", spec)
    actions = [(s.get("action") or "").upper() for s in steps]
    assert "EDIT" in actions
    assert "SEARCH" in actions


def test_build_phase_1_steps_explain_artifact():
    """_build_phase_1_steps returns SEARCH+EXPLAIN+WRITE_ARTIFACT for explain-artifact."""
    from tests.agent_eval.suites.core12 import CORE12_TASKS

    spec = next(s for s in CORE12_TASKS if s.task_id == "core12_pin_requests_explain_trace")
    steps = _build_phase_1_steps("Write explain_out.txt", spec)
    actions = [(s.get("action") or "").upper() for s in steps]
    assert "WRITE_ARTIFACT" in actions
    assert "EXPLAIN" in actions
    assert "SEARCH" in actions
    write_step = next((s for s in steps if (s.get("action") or "").upper() == "WRITE_ARTIFACT"), None)
    assert write_step.get("artifact_path") == "benchmark_local/artifacts/explain_out.txt"
