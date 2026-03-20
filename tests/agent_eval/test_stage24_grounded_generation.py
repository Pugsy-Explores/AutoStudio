"""Stage 24 — Grounded patch generation tests.

All tests are generic and content-driven. No task_id-specific hacks.
Tests cover:
- return-op repair via exact source line match
- empty-check negation via exact source line match
- raw-return-to-split via bare return detection
- string constant rename via exact assignment match
- missing function append when confirmed absent
- version constant align via paired md header
- url constant align via paired md bold url
- candidate rejected when no evidence exists
- candidate ranking prefers exact text_sub over module_append
"""

from __future__ import annotations

from pathlib import Path

import pytest

from editing.grounded_patch_generator import (
    PatchCandidate,
    generate_grounded_candidates,
    grounded_generation_telemetry,
    select_best_candidate,
    validate_grounded_candidate,
    _try_return_binary_op_repair,
    _try_empty_check_negation,
    _try_raw_return_to_split,
    _try_string_constant_rename,
    _try_version_constant_align,
    _try_url_constant_align,
    _try_add_missing_function,
)
from editing.patch_generator import to_structured_patches
from editing.patch_executor import execute_patch
from tests.agent_eval.semantic_rca import classify_wrong_patch_root_cause


# ---------------------------------------------------------------------------
# Strategy unit tests
# ---------------------------------------------------------------------------

def test_return_binary_op_repair_divide():
    """Fix return a * b when instruction says divide."""
    src = "def normalize_ratios(a: float, b: float) -> float:\n    return a * b\n"
    c = _try_return_binary_op_repair(
        "Fix normalize_ratios so that 12 divided by 4 equals 3.0", src
    )
    assert c is not None
    assert c.strategy == "return_binary_op_repair"
    assert c.patch["action"] == "text_sub"
    assert "* b" in c.patch["old"] or "*" in c.patch["old"]
    assert "/ b" in c.patch["new"] or "/" in c.patch["new"]
    assert c.has_evidence()
    assert c.rank == 0


def test_return_binary_op_repair_no_match_when_already_correct():
    """No candidate when file already uses divide operator."""
    src = "def f(a, b):\n    return a / b\n"
    c = _try_return_binary_op_repair("Fix so it divides correctly", src)
    # No * b to fix — should be None
    assert c is None


def test_empty_check_negation():
    """Fix return len(s) == 0 when instruction says returns True for non-empty."""
    src = "def validate_input(s: str) -> bool:\n    return len(s) == 0\n"
    c = _try_empty_check_negation(
        "Fix the validation guard so it returns True for non-empty strings", src
    )
    assert c is not None
    assert c.strategy == "empty_check_negation"
    assert c.patch["action"] == "text_sub"
    assert "== 0" in c.patch["old"]
    assert "> 0" in c.patch["new"]
    assert c.has_evidence()
    assert c.rank == 0


def test_empty_check_negation_no_match_when_instruction_irrelevant():
    """No candidate when instruction does not mention non-empty."""
    src = "def f(s):\n    return len(s) == 0\n"
    c = _try_empty_check_negation("Fix the function return", src)
    assert c is None


def test_raw_return_to_split():
    """Fix return data when instruction says split on whitespace."""
    src = "def parse_bytes(data: bytes) -> list:\n    return data\n"
    c = _try_raw_return_to_split(
        "Fix parse_bytes to split on whitespace and return a list of tokens", src
    )
    assert c is not None
    assert c.strategy == "raw_return_to_split"
    assert c.patch["action"] == "text_sub"
    assert ".split()" in c.patch["new"]
    assert c.has_evidence()
    assert c.rank == 0


def test_raw_return_to_split_skipped_when_already_split():
    """No candidate when file already calls .split()."""
    src = "def f(data):\n    return data.split()\n"
    c = _try_raw_return_to_split("split on whitespace and return list of tokens", src)
    assert c is None


def test_string_constant_rename():
    """Rename BASE_URI from 'http' to 'https' using exact assignment match."""
    src = 'BASE_URI = "http"\n'
    c = _try_string_constant_rename(
        "Rename BASE_URI from 'http' to 'https' in mod_a/params.py and any dependent code", src
    )
    assert c is not None
    assert c.strategy == "string_constant_rename"
    assert c.patch["action"] == "text_sub"
    assert '"http"' in c.patch["old"]
    assert '"https"' in c.patch["new"]
    assert c.has_evidence()
    assert c.rank == 0


def test_string_constant_rename_no_match_when_value_absent():
    """No candidate when old value not in file."""
    src = 'BASE_URI = "ftp"\n'
    c = _try_string_constant_rename("Rename BASE_URI from 'http' to 'https'", src)
    assert c is None


def test_add_missing_function_bool():
    """Add cfg_verbose() -> bool when absent; extract return value from instruction."""
    src = "def get_timeout_ms() -> int:\n    return 5000\n"
    c = _try_add_missing_function(
        "Add cfg_verbose() -> bool in cfg/defaults.py that returns False by default",
        src,
        ".py",
    )
    assert c is not None
    assert c.strategy == "add_missing_function"
    assert c.patch["action"] == "insert"
    assert c.patch["target_node"] == "module_append"
    assert "cfg_verbose" in c.patch["code"]
    assert "False" in c.patch["code"]
    assert c.has_evidence()
    assert c.rank == 2


def test_add_missing_function_str_with_example():
    """Add get_severity() -> str; extract return value from e.g. 'WARN'."""
    src = "def is_tracing_enabled() -> bool:\n    return False\n"
    c = _try_add_missing_function(
        "Add get_severity() -> str in logging/levels.py returning a non-empty string (e.g. 'WARN').",
        src,
        ".py",
    )
    assert c is not None
    assert "get_severity" in c.patch["code"]
    assert "WARN" in c.patch["code"]


def test_add_missing_function_int():
    """Add max_retries() -> int returning 3."""
    src = "def get_backoff_sec() -> float:\n    return 1.0\n"
    c = _try_add_missing_function(
        "Add max_retries() -> int in the runtime options module returning 3.",
        src,
        ".py",
    )
    assert c is not None
    assert "max_retries" in c.patch["code"]
    assert "3" in c.patch["code"]


def test_add_missing_function_skipped_when_already_defined():
    """No candidate when function already in file."""
    src = "def cfg_verbose() -> bool:\n    return False\n"
    c = _try_add_missing_function(
        "Add cfg_verbose() -> bool returning False by default", src, ".py"
    )
    assert c is None


# ---------------------------------------------------------------------------
# Docs alignment tests
# ---------------------------------------------------------------------------

def test_version_constant_align(tmp_path):
    """Align version constant in .py to ## vX.Y.Z header in .md file."""
    (tmp_path / "RELEASE_NOTES.md").write_text(
        "# Release Notes\n\n## v1.5.0 (2024-06-01)\n\nFeature release.\n",
        encoding="utf-8",
    )
    py_content = 'BUILD_NUMBER = "2.0.0"  # mismatched\n'
    c = _try_version_constant_align(
        "Align RELEASE_NOTES.md and pkg/version.py so the version in the release header matches BUILD_NUMBER",
        "pkg/version.py",
        py_content,
        str(tmp_path),
    )
    assert c is not None
    assert c.strategy == "version_constant_align"
    assert c.patch["action"] == "text_sub"
    assert '"2.0.0"' in c.patch["old"]
    assert '"1.5.0"' in c.patch["new"]
    assert c.has_evidence()


def test_version_constant_align_no_change_when_already_aligned(tmp_path):
    """No candidate when versions already match."""
    (tmp_path / "RELEASE_NOTES.md").write_text("## v1.5.0\n", encoding="utf-8")
    py_content = 'BUILD_NUMBER = "1.5.0"\n'
    c = _try_version_constant_align(
        "Align RELEASE_NOTES.md and pkg/version.py",
        "pkg/version.py",
        py_content,
        str(tmp_path),
    )
    assert c is None


def test_url_constant_align(tmp_path):
    """Align URL constant in .py to bold URL in .md file."""
    (tmp_path / "SPEC.md").write_text(
        "# Specification\n\nDefault endpoint: **https://api.service.com/v2**\n",
        encoding="utf-8",
    )
    py_content = 'DEFAULT_ENDPOINT = "https://service.example.org/v1"  # mismatched\n'
    c = _try_url_constant_align(
        "Make SPEC.md and impl/spec.py agree on the default endpoint netloc",
        "impl/spec.py",
        py_content,
        str(tmp_path),
    )
    assert c is not None
    assert c.strategy == "url_constant_align"
    assert c.patch["action"] == "text_sub"
    assert "service.example.org" in c.patch["old"]
    assert "api.service.com" in c.patch["new"]
    assert c.has_evidence()


# ---------------------------------------------------------------------------
# Evidence-binding and validation tests
# ---------------------------------------------------------------------------

def test_candidate_rejected_when_no_evidence():
    """validate_grounded_candidate rejects candidate without evidence in file."""
    candidate = PatchCandidate(
        patch={"action": "text_sub", "old": "GHOST_LINE_NOT_IN_FILE", "new": "replacement"},
        strategy="test",
        evidence_type="test",
        evidence_excerpt="test",
        rank=0,
    )
    ok, reason = validate_grounded_candidate(candidate, "real file content here")
    assert ok is False
    assert reason == "target_region_not_found"


def test_candidate_rejected_no_evidence_type():
    """validate_grounded_candidate rejects candidate with empty evidence."""
    candidate = PatchCandidate(
        patch={"action": "text_sub", "old": "x", "new": "y"},
        strategy="test",
        evidence_type="",
        evidence_excerpt="",
        rank=0,
    )
    ok, reason = validate_grounded_candidate(candidate, "x = 1")
    assert ok is False
    assert reason == "no_grounded_evidence"


def test_candidate_rejected_no_effect():
    """validate_grounded_candidate rejects old == new."""
    candidate = PatchCandidate(
        patch={"action": "text_sub", "old": "same", "new": "same"},
        strategy="test",
        evidence_type="matched_line",
        evidence_excerpt="same",
        rank=0,
    )
    ok, reason = validate_grounded_candidate(candidate, "same text here")
    assert ok is False
    assert reason == "no_effect_change"


def test_candidate_accepted_when_valid():
    """validate_grounded_candidate accepts a well-formed candidate."""
    src = "    return a * b\n"
    candidate = PatchCandidate(
        patch={"action": "text_sub", "old": "return a * b", "new": "return a / b"},
        strategy="return_binary_op_repair",
        evidence_type="matched_return_op_line",
        evidence_excerpt="return a * b",
        rank=0,
    )
    ok, reason = validate_grounded_candidate(candidate, src)
    assert ok is True
    assert reason is None


# ---------------------------------------------------------------------------
# Candidate ranking test
# ---------------------------------------------------------------------------

def test_candidate_ranking_prefers_text_sub_over_module_append():
    """Exact text_sub (rank 0) beats module_append (rank 2)."""
    text_sub_candidate = PatchCandidate(
        patch={"action": "text_sub", "old": "return a * b", "new": "return a / b"},
        strategy="return_binary_op_repair",
        evidence_type="matched_return_op_line",
        evidence_excerpt="return a * b",
        rank=0,
    )
    append_candidate = PatchCandidate(
        patch={"action": "insert", "target_node": "module_append", "code": "\ndef f(): pass\n"},
        strategy="add_missing_function",
        evidence_type="confirmed_function_absence",
        evidence_excerpt="f not found",
        rank=2,
    )
    candidates = [append_candidate, text_sub_candidate]
    best = select_best_candidate(candidates)
    assert best is text_sub_candidate


# ---------------------------------------------------------------------------
# Telemetry tests
# ---------------------------------------------------------------------------

def test_telemetry_fields_present():
    """grounded_generation_telemetry returns all expected fields."""
    c = PatchCandidate(
        patch={"action": "text_sub", "old": "x", "new": "y"},
        strategy="return_binary_op_repair",
        evidence_type="matched_return_op_line",
        evidence_excerpt="x = ...",
        rank=0,
    )
    t = grounded_generation_telemetry([c], c)
    assert t["grounded_candidate_count"] == 1
    assert t["selected_candidate_rank"] == 0
    assert t["patch_candidate_strategy"] == "return_binary_op_repair"
    assert t["patch_candidate_evidence_type"] == "matched_return_op_line"
    assert t["generation_rejected_reason"] is None


def test_telemetry_empty_when_no_candidates():
    """grounded_generation_telemetry with empty list reports zero candidates."""
    t = grounded_generation_telemetry([], None, "no_grounded_candidate_found")
    assert t["grounded_candidate_count"] == 0
    assert t["selected_candidate_rank"] == -1
    assert t["generation_rejected_reason"] == "no_grounded_candidate_found"


# ---------------------------------------------------------------------------
# Integration: generate_grounded_candidates
# ---------------------------------------------------------------------------

def test_generate_grounded_candidates_for_ratios_repair():
    """generate_grounded_candidates finds return_binary_op_repair for divide instruction."""
    src = "def normalize_ratios(a: float, b: float) -> float:\n    return a * b\n"
    candidates = generate_grounded_candidates(
        "Fix normalize_ratios in core/ratios.py so that 12 divided by 4 equals 3.0",
        "core/ratios.py",
        src,
        "/fake/root",
    )
    assert len(candidates) > 0
    best = select_best_candidate(candidates)
    assert best is not None
    assert best.strategy == "return_binary_op_repair"
    ok, _ = validate_grounded_candidate(best, src)
    assert ok is True


def test_generate_grounded_candidates_for_parse_repair():
    """generate_grounded_candidates finds raw_return_to_split for whitespace-split instruction."""
    src = "def parse_bytes(data: bytes) -> list:\n    return data\n"
    candidates = generate_grounded_candidates(
        "Fix parse_bytes in io/bytes_parser.py to split on whitespace and return a list of tokens",
        "io/bytes_parser.py",
        src,
        "/fake/root",
    )
    assert len(candidates) > 0
    best = select_best_candidate(candidates)
    assert best is not None
    assert best.strategy == "raw_return_to_split"


def test_generate_grounded_candidates_for_feature_add():
    """generate_grounded_candidates finds add_missing_function for feature instruction."""
    src = "def get_timeout_ms() -> int:\n    return 5000\n"
    candidates = generate_grounded_candidates(
        "Add cfg_verbose() -> bool in cfg/defaults.py that returns False by default",
        "cfg/defaults.py",
        src,
        "/fake/root",
    )
    assert len(candidates) > 0
    best = select_best_candidate(candidates)
    assert best is not None
    assert best.strategy == "add_missing_function"


def test_generate_no_candidates_for_unrelated_instruction():
    """generate_grounded_candidates returns empty list when instruction doesn't match content."""
    src = "x = 1\n"
    candidates = generate_grounded_candidates(
        "Refactor the database layer to use async",
        "utils.py",
        src,
        "/fake/root",
    )
    # No patterns should match
    assert len(candidates) == 0


# ---------------------------------------------------------------------------
# Full pipeline: to_structured_patches uses grounded layer
# ---------------------------------------------------------------------------

def test_to_structured_patches_uses_grounded_for_divide(tmp_path):
    """to_structured_patches grounded layer produces text_sub for a divide instruction."""
    f = tmp_path / "ratios.py"
    f.write_text("def normalize_ratios(a, b):\n    return a * b\n", encoding="utf-8")
    context = {"project_root": str(tmp_path)}
    plan = {
        "changes": [
            {
                "file": "ratios.py",
                "symbol": "normalize_ratios",
                "action": "modify",
                "patch": "Apply changes from: Fix so 12 divided by 4 equals 3.0",
            }
        ]
    }
    result = to_structured_patches(plan, "Fix ratios.py so that 12 divided by 4 equals 3.0", context)
    assert "patch_generation_reject" not in result
    changes = result.get("changes", [])
    assert len(changes) > 0
    patch = changes[0].get("patch", {})
    assert patch.get("action") == "text_sub"
    assert "/" in patch.get("new", "")


def test_to_structured_patches_uses_grounded_for_missing_function(tmp_path):
    """to_structured_patches grounded layer appends missing function."""
    f = tmp_path / "defaults.py"
    f.write_text("def get_timeout_ms() -> int:\n    return 5000\n", encoding="utf-8")
    context = {"project_root": str(tmp_path)}
    plan = {
        "changes": [
            {
                "file": "defaults.py",
                "symbol": "",
                "action": "modify",
                "patch": "Apply changes from: Add cfg_verbose",
            }
        ]
    }
    result = to_structured_patches(
        plan,
        "Add cfg_verbose() -> bool in defaults.py that returns False by default",
        context,
    )
    assert "patch_generation_reject" not in result
    changes = result.get("changes", [])
    assert len(changes) > 0
    patch = changes[0].get("patch", {})
    assert patch.get("action") == "insert"
    assert "cfg_verbose" in (patch.get("code") or "")


def test_full_pipeline_ratios_repair(tmp_path):
    """Full pipeline: grounded text_sub is applied and produces correct file content."""
    f = tmp_path / "ratios.py"
    f.write_text("def normalize_ratios(a, b):\n    return a * b\n", encoding="utf-8")
    context = {"project_root": str(tmp_path)}
    plan = {
        "changes": [
            {
                "file": "ratios.py",
                "symbol": "normalize_ratios",
                "action": "modify",
                "patch": "Apply changes from: Fix so 12 divided by 4 equals 3.0",
            }
        ]
    }
    patch_plan = to_structured_patches(
        plan, "Fix ratios.py so that 12 divided by 4 equals 3.0", context
    )
    result = execute_patch(patch_plan, project_root=str(tmp_path))
    assert result.get("success") is True
    content = f.read_text(encoding="utf-8")
    assert "return a / b" in content
    assert "return a * b" not in content


def test_full_pipeline_feature_add(tmp_path):
    """Full pipeline: grounded module_append produces a working missing function."""
    f = tmp_path / "defaults.py"
    f.write_text("def get_timeout_ms() -> int:\n    return 5000\n", encoding="utf-8")
    context = {"project_root": str(tmp_path)}
    plan = {
        "changes": [
            {
                "file": "defaults.py",
                "symbol": "",
                "action": "modify",
                "patch": "Apply changes from: Add cfg_verbose",
            }
        ]
    }
    patch_plan = to_structured_patches(
        plan,
        "Add cfg_verbose() -> bool in defaults.py that returns False by default",
        context,
    )
    result = execute_patch(patch_plan, project_root=str(tmp_path))
    assert result.get("success") is True
    content = f.read_text(encoding="utf-8")
    assert "def cfg_verbose" in content
    assert "return False" in content


# ---------------------------------------------------------------------------
# RCA integration
# ---------------------------------------------------------------------------

def test_rca_no_grounded_candidate_found_cause():
    """semantic RCA classifies no_grounded_candidate_found when grounded_count==0."""
    cause = classify_wrong_patch_root_cause(
        success=False,
        structural_success=False,
        validation_passed=False,
        failure_bucket="edit_grounding_failure",
        loop_snapshot={
            "edit_telemetry": {
                "patch_reject_reason": "weakly_grounded_patch",
                "edit_failure_reason": "weakly_grounded_patch",
                "grounded_candidate_count": 0,
                "generation_rejected_reason": "no_grounded_candidate_found",
                "patch_apply_ok": False,
            }
        },
        validation_logs=[],
        instruction="Fix the validation guard",
    )
    assert cause == "no_grounded_candidate_found"


def test_rca_grounded_candidate_wrong_behavior():
    """semantic RCA classifies grounded_candidate_wrong_behavior when strategy + validation fail."""
    cause = classify_wrong_patch_root_cause(
        success=False,
        structural_success=False,
        validation_passed=False,
        failure_bucket="validation_regression",
        loop_snapshot={
            "edit_telemetry": {
                "patch_reject_reason": "validation_tests_failed",
                "patch_candidate_strategy": "return_binary_op_repair",
                "grounded_candidate_count": 1,
                "patch_apply_ok": True,
                "patches_applied": 1,
            }
        },
        validation_logs=[],
        instruction="Fix the divide function",
    )
    assert cause == "grounded_candidate_wrong_behavior"


# ---------------------------------------------------------------------------
# Anti-overfit checks
# ---------------------------------------------------------------------------

def test_no_task_id_hacks_in_grounded_generator():
    """grounded_patch_generator must not reference adversarial task IDs."""
    import editing.grounded_patch_generator as gpg

    src = open(gpg.__file__, encoding="utf-8").read()
    for task_id_fragment in (
        "adv_repair",
        "adv_feature",
        "adv_docs",
        "adv_multifile",
        "adversarial12",
        "normalize_ratios",
        "parse_bytes",
        "cfg_verbose",
        "get_severity",
        "BASE_URI",
        "BUILD_NUMBER",
        "DEFAULT_ENDPOINT",
        "CURRENT_VERSION",
    ):
        assert task_id_fragment not in src, (
            f"grounded_patch_generator.py must not reference benchmark-specific name {task_id_fragment!r}"
        )


def test_no_task_id_hacks_in_patch_generator():
    """patch_generator.py must not reference adversarial task IDs."""
    import editing.patch_generator as pg

    src = open(pg.__file__, encoding="utf-8").read()
    for frag in ("adv_repair", "adv_feature", "adversarial12"):
        assert frag not in src, f"patch_generator.py must not reference {frag!r}"
