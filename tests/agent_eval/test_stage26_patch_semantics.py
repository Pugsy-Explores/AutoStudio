"""Stage 26 — Patch semantics, candidate ranking, and semantic rejection tests.

All tests are generic. No task-id-specific hacks.
Tests cover:
- generic add-function with literal return value
- candidate ranking prefers exact symbol/value match over weaker candidates
- semantic rejection when patch does not implement requested return literal
- semantic rejection when appended function name does not match instruction
- docs/code align candidate must modify one side meaningfully
"""

from __future__ import annotations

from pathlib import Path

import pytest

from editing.grounded_patch_generator import (
    PatchCandidate,
    generate_grounded_candidates,
    select_best_candidate,
    validate_grounded_candidate,
    validate_semantic_grounded_candidate,
    _try_add_missing_function,
)
from editing.test_runner_utils import (
    _workspace_has_stdlib_shadowing,
    _transform_pytest_cmd_for_shadowing,
)
from tests.agent_eval.semantic_rca import classify_wrong_patch_root_cause


# ---------------------------------------------------------------------------
# Add-function with literal return value
# ---------------------------------------------------------------------------


def test_add_missing_function_with_literal_return_value():
    """Add get_severity() -> str returning 'WARN' when instruction says e.g. 'WARN'."""
    src = '''"""Logging levels."""
def is_tracing_enabled() -> bool:
    return False
'''
    c = _try_add_missing_function(
        "Add get_severity() -> str in logging/levels.py returning a non-empty string (e.g. 'WARN').",
        src,
        ".py",
    )
    assert c is not None
    assert c.strategy == "add_missing_function"
    assert "def get_severity()" in c.patch["code"]
    assert 'return "WARN"' in c.patch["code"]
    assert "-> str" in c.patch["code"]


def test_add_missing_function_returns_requested_literal():
    """Add function uses literal from instruction, not default empty string."""
    src = "def other(): pass\n"
    c = _try_add_missing_function(
        "Add get_level() -> str returning 'INFO'.",
        src,
        ".py",
    )
    assert c is not None
    assert 'return "INFO"' in c.patch["code"]


# ---------------------------------------------------------------------------
# Candidate ranking
# ---------------------------------------------------------------------------


def test_candidate_ranking_prefers_exact_symbol_match():
    """When multiple candidates exist, prefer one with exact symbol in instruction."""
    inst = "Add get_severity() -> str returning 'WARN'."
    src = "def is_tracing_enabled(): return False\n"
    candidates = generate_grounded_candidates(inst, "levels.py", src, "/tmp")
    assert len(candidates) >= 1
    best = select_best_candidate(candidates, inst)
    assert best is not None
    assert "get_severity" in best.patch.get("code", "")
    assert best.extra.get("semantic_match_score", 0) >= 0


def test_semantic_ranking_scores_return_literal_alignment():
    """Candidates with matching return literal get higher semantic score."""
    inst = "Add get_severity() -> str returning a non-empty string (e.g. 'WARN')."
    src = "def other(): pass\n"
    candidates = generate_grounded_candidates(inst, "levels.py", src, "/tmp")
    assert len(candidates) >= 1
    best = candidates[0]
    assert "semantic_match_score" in best.extra
    assert 'return "WARN"' in best.patch.get("code", "")


# ---------------------------------------------------------------------------
# Semantic rejection
# ---------------------------------------------------------------------------


def test_semantic_rejection_when_patch_missing_requested_return_literal():
    """Reject candidate that returns empty string when instruction says return 'WARN'."""
    c = PatchCandidate(
        patch={
            "action": "insert",
            "target_node": "module_append",
            "code": '\ndef get_severity() -> str:\n    return ""\n',
        },
        strategy="add_missing_function",
        evidence_type="confirmed_function_absence",
        evidence_excerpt="get_severity not found",
        rank=2,
    )
    ok, reason = validate_semantic_grounded_candidate(
        c,
        "Add get_severity() -> str returning 'WARN'.",
    )
    assert ok is False
    assert reason == "requested_literal_not_realized"


def test_semantic_rejection_when_function_name_mismatch():
    """Reject candidate that defines wrong function when instruction says Add fname()."""
    c = PatchCandidate(
        patch={
            "action": "insert",
            "target_node": "module_append",
            "code": '\ndef wrong_name() -> str:\n    return "WARN"\n',
        },
        strategy="add_missing_function",
        evidence_type="confirmed_function_absence",
        evidence_excerpt="get_severity not found",
        rank=2,
    )
    ok, reason = validate_semantic_grounded_candidate(
        c,
        "Add get_severity() -> str returning 'WARN'.",
    )
    assert ok is False
    assert reason == "requested_symbol_not_implemented"


def test_semantic_accept_when_function_and_literal_match():
    """Accept candidate that defines correct function with correct return."""
    c = PatchCandidate(
        patch={
            "action": "insert",
            "target_node": "module_append",
            "code": '\ndef get_severity() -> str:\n    return "WARN"\n',
        },
        strategy="add_missing_function",
        evidence_type="confirmed_function_absence",
        evidence_excerpt="get_severity not found",
        rank=2,
    )
    ok, reason = validate_semantic_grounded_candidate(
        c,
        "Add get_severity() -> str returning a non-empty string (e.g. 'WARN').",
    )
    assert ok is True
    assert reason is None


def test_semantic_rejection_align_candidate_modifies_neither():
    """Reject align candidate when old == new (no meaningful change)."""
    c = PatchCandidate(
        patch={"action": "text_sub", "old": "X = 1", "new": "X = 1"},
        strategy="version_constant_align",
        evidence_type="matched_version_constant_and_md_header",
        evidence_excerpt="X=1.0",
        rank=1,
    )
    ok, reason = validate_semantic_grounded_candidate(
        c,
        "Align RELEASE_NOTES.md and pkg/version.py so the version matches.",
    )
    assert ok is False
    assert reason == "align_candidate_modifies_neither"


# ---------------------------------------------------------------------------
# Validation shadowing (execution loop env)
# ---------------------------------------------------------------------------


def test_workspace_has_stdlib_shadowing_detects_logging(tmp_path):
    """Workspace with logging/ dir is detected as stdlib-shadowing."""
    assert _workspace_has_stdlib_shadowing(str(tmp_path)) is False
    (tmp_path / "logging").mkdir()
    assert _workspace_has_stdlib_shadowing(str(tmp_path)) is True
    (tmp_path / "logging").rmdir()
    assert _workspace_has_stdlib_shadowing(str(tmp_path)) is False


def test_transform_pytest_cmd_strips_pythonpath(tmp_path):
    """When workspace has logging/, transform strips PYTHONPATH and rewrites test path."""
    (tmp_path / "logging").mkdir()
    result = _transform_pytest_cmd_for_shadowing(
        "PYTHONPATH=. python3 -m pytest tests/test_levels.py -q",
        str(tmp_path),
    )
    assert result is not None
    cmd, cwd = result
    assert "PYTHONPATH" not in cmd
    assert tmp_path.name in cmd or "test_levels" in cmd


# ---------------------------------------------------------------------------
# RCA classification
# ---------------------------------------------------------------------------


def test_rca_classifies_semantic_rejection():
    """When candidate_rejected_semantic_reason is set, classifier returns finer cause."""
    cause = classify_wrong_patch_root_cause(
        success=False,
        structural_success=False,
        validation_passed=False,
        failure_bucket="edit_grounding_failure",
        loop_snapshot={},
        validation_logs=[],
        instruction="Add get_severity() -> str returning 'WARN'.",
    )
    # Without telemetry we get unknown or generic; with telemetry we get finer cause
    et = {"candidate_rejected_semantic_reason": "requested_symbol_not_implemented"}
    # Simulate via loop_snapshot
    snap = {"edit_telemetry": et}
    cause2 = classify_wrong_patch_root_cause(
        success=False,
        structural_success=False,
        validation_passed=False,
        failure_bucket="edit_grounding_failure",
        loop_snapshot=snap,
        validation_logs=[],
        instruction="Add get_severity() -> str returning 'WARN'.",
    )
    assert cause2 == "requested_symbol_not_implemented"


def test_no_task_id_specific_hacks():
    """Verify no task_id or repo name in grounded generation logic."""
    import inspect
    from editing import grounded_patch_generator as gpg
    src = inspect.getsource(gpg)
    assert "adv_feature_severity" not in src
    assert "av04_severity" not in src
    assert "get_severity" not in src  # no hardcoded task-specific names
