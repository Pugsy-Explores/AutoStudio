"""Stage 25 — Target resolution and validation-target contamination tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.retrieval.target_resolution import (
    is_validation_script_path,
    validation_script_paths_from_instruction,
    validation_script_paths_from_command,
    inferred_source_files_from_validation,
    resolve_module_descriptor_to_files,
    resolve_edit_targets_for_plan,
    detect_likely_import_shadowing,
)
from agent.retrieval.task_semantics import instruction_asks_to_modify_validation_script
from editing.diff_planner import plan_diff
from tests.agent_eval.semantic_rca import classify_wrong_patch_root_cause


def test_is_validation_script_path():
    """Validation script patterns are detected."""
    assert is_validation_script_path("bin/assert_guard.py") is True
    assert is_validation_script_path("scripts/assert_release_match.py") is True
    assert is_validation_script_path("scripts/check_readme.py") is True
    assert is_validation_script_path("bin/verify_something.py") is True
    assert is_validation_script_path("tests/test_ratios.py") is True
    assert is_validation_script_path("core/ratios.py") is False
    assert is_validation_script_path("validation/guard.py") is False


def test_validation_script_paths_from_instruction():
    """Extract validation script paths from instruction text."""
    inst = "Fix the validation guard. Run bin/assert_guard.py to verify."
    paths = validation_script_paths_from_instruction(inst)
    assert "bin/assert_guard.py" in paths

    inst2 = "Align RELEASE_NOTES. Run scripts/assert_release_match.py"
    paths2 = validation_script_paths_from_instruction(inst2)
    assert "scripts/assert_release_match.py" in paths2


def test_validation_script_paths_from_command():
    """Extract validation script path from command string."""
    cmd = "python3 bin/assert_guard.py"
    paths = validation_script_paths_from_command(cmd)
    assert "bin/assert_guard.py" in paths

    cmd2 = "PYTHONPATH=. python3 -m pytest tests/test_parser.py -q"
    paths2 = validation_script_paths_from_command(cmd2)
    assert "tests/test_parser.py" in paths2


def test_inferred_source_files_from_validation(tmp_path):
    """Parse validation script imports to find source files."""
    (tmp_path / "validation").mkdir()
    (tmp_path / "validation" / "guard.py").write_text("def validate_input(s): return len(s) > 0\n")
    (tmp_path / "validation" / "__init__.py").write_text("")
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "assert_guard.py").write_text(
        "from validation.guard import validate_input\nif not validate_input('ok'): exit(1)\n"
    )
    sources = inferred_source_files_from_validation("bin/assert_guard.py", str(tmp_path))
    assert any("validation/guard.py" in p or "validation\\guard.py" in p for p, _ in sources)


def test_resolve_module_descriptor_runtime_options(tmp_path):
    """'runtime options module' resolves to runtime/options.py."""
    (tmp_path / "runtime").mkdir()
    (tmp_path / "runtime" / "options.py").write_text("def get_backoff(): return 1.0\n")
    result = resolve_module_descriptor_to_files(
        "Add max_retries() -> int in the runtime options module returning 3.",
        str(tmp_path),
    )
    paths = [p for p, _ in result]
    assert any("runtime/options.py" in p or "runtime\\options.py" in p for p in paths)


def test_resolve_module_descriptor_validation_guard(tmp_path):
    """'validation guard' resolves to validation/guard.py."""
    (tmp_path / "validation").mkdir()
    (tmp_path / "validation" / "guard.py").write_text("def validate_input(s): return False\n")
    (tmp_path / "validation" / "__init__.py").write_text("")
    result = resolve_module_descriptor_to_files(
        "Fix the validation guard so it returns True for non-empty strings.",
        str(tmp_path),
    )
    paths = [p for p, _ in result]
    assert any("validation/guard.py" in p.replace("\\", "/") for p in paths)


def test_instruction_asks_to_modify_validation():
    """instruction_asks_to_modify_validation_script detects explicit requests."""
    assert instruction_asks_to_modify_validation_script("modify the test script") is True
    assert instruction_asks_to_modify_validation_script("update the assert script") is True
    assert instruction_asks_to_modify_validation_script("Fix the validation guard") is False


def test_resolve_edit_targets_source_over_validator(tmp_path):
    """Inferred source file ranks above validation script."""
    (tmp_path / "validation").mkdir()
    (tmp_path / "validation" / "guard.py").write_text("def validate_input(s): return len(s)==0\n")
    (tmp_path / "validation" / "__init__.py").write_text("")
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "assert_guard.py").write_text(
        "from validation.guard import validate_input\nif not validate_input('ok'): exit(1)\n"
    )
    context = {
        "project_root": str(tmp_path),
        "resolved_validation_command": "python3 bin/assert_guard.py",
    }
    result = resolve_edit_targets_for_plan(
        "Fix the validation guard so it returns True for non-empty strings. Run bin/assert_guard.py to verify.",
        str(tmp_path),
        context,
    )
    ranked = result.get("edit_targets_ranked", [])
    assert ranked
    top_path, top_penalty, _ = ranked[0]
    assert top_penalty < 80
    assert "validation" in top_path and "guard" in top_path
    assert "assert_guard" not in top_path or top_penalty >= 80


def test_resolve_edit_targets_explicit_path_still_wins(tmp_path):
    """Explicit edit path beats inferred candidates."""
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "ratios.py").write_text("def normalize_ratios(a,b): return a*b\n")
    context = {"project_root": str(tmp_path)}
    result = resolve_edit_targets_for_plan(
        "Fix normalize_ratios in core/ratios.py so that 12 divided by 4 equals 3.0",
        str(tmp_path),
        context,
    )
    ranked = result.get("edit_targets_ranked", [])
    assert ranked
    top_path, top_penalty, evidence = ranked[0]
    assert top_penalty == 0
    assert "core/ratios.py" in top_path.replace("\\", "/")
    assert "explicit" in evidence.lower()


def test_detect_likely_import_shadowing():
    """Import errors mentioning stdlib names are flagged."""
    out = "ModuleNotFoundError: No module named 'io.bytes_parser'"
    r = detect_likely_import_shadowing(out)
    assert r.get("likely_stdlib_shadowing") is True
    assert "io" in (r.get("module_names_in_error") or [])

    out2 = "ImportError: cannot import name 'get_severity' from 'logging.levels'"
    r2 = detect_likely_import_shadowing(out2)
    assert r2.get("likely_stdlib_shadowing") is True
    assert "logging" in (r2.get("module_names_in_error") or [])

    out3 = "AssertionError: expected 3.0 got 48.0"
    r3 = detect_likely_import_shadowing(out3)
    assert r3.get("likely_stdlib_shadowing") is False


def test_plan_diff_uses_validation_guard_source(tmp_path):
    """plan_diff selects validation/guard.py over bin/assert_guard.py when context has validation cmd."""
    (tmp_path / "validation").mkdir()
    (tmp_path / "validation" / "guard.py").write_text("def validate_input(s: str) -> bool:\n    return len(s) == 0\n")
    (tmp_path / "validation" / "__init__.py").write_text("")
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "assert_guard.py").write_text(
        "from validation.guard import validate_input\nif not validate_input('ok'): exit(1)\nexit(0)\n"
    )
    context = {
        "project_root": str(tmp_path),
        "resolved_validation_command": "python3 bin/assert_guard.py",
    }
    plan = plan_diff(
        "Fix the validation guard so it returns True for non-empty strings. Run bin/assert_guard.py to verify.",
        context,
    )
    changes = plan.get("changes", [])
    assert changes
    first_file = changes[0].get("file", "")
    assert "validation" in first_file and "guard" in first_file
    assert "assert_guard" not in first_file


def test_plan_diff_resolves_runtime_options_module(tmp_path):
    """plan_diff resolves 'runtime options module' to runtime/options.py when no explicit path."""
    (tmp_path / "runtime").mkdir()
    (tmp_path / "runtime" / "options.py").write_text("def get_backoff_sec(): return 1.0\n")
    (tmp_path / "runtime" / "__init__.py").write_text("")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_options.py").write_text("from runtime.options import max_retries\nassert max_retries()==3\n")
    context = {
        "project_root": str(tmp_path),
        "resolved_validation_command": "python3 -m pytest tests/test_options.py -q",
    }
    plan = plan_diff(
        "Add max_retries() -> int in the runtime options module returning 3.",
        context,
    )
    changes = plan.get("changes", [])
    assert changes
    first_file = changes[0].get("file", "")
    assert "runtime" in first_file and "options" in first_file


def test_rca_validation_script_selected_as_target():
    """RCA classifies validation_script_selected_as_target when chosen file is validation script."""
    cause = classify_wrong_patch_root_cause(
        success=False,
        structural_success=False,
        validation_passed=False,
        failure_bucket="edit_grounding_failure",
        loop_snapshot={
            "edit_telemetry": {
                "chosen_target_file": "bin/assert_guard.py",
                "patch_reject_reason": "weakly_grounded_patch",
                "target_resolution": {"validation_scripts": ["bin/assert_guard.py"]},
            }
        },
        validation_logs=[],
        instruction="Fix the validation guard",
    )
    assert cause == "validation_script_selected_as_target"


def test_rca_likely_import_shadowing():
    """RCA classifies likely_import_shadowing when validation failed and telemetry says so."""
    cause = classify_wrong_patch_root_cause(
        success=False,
        structural_success=False,
        validation_passed=False,
        failure_bucket="validation_regression",
        loop_snapshot={
            "edit_telemetry": {
                "patch_reject_reason": "validation_tests_failed",
                "patch_candidate_strategy": "raw_return_to_split",
                "likely_stdlib_shadowing": True,
                "module_names_in_validation_error": ["io"],
            }
        },
        validation_logs=[],
        instruction="Fix parse_bytes",
    )
    assert cause == "likely_import_shadowing_or_env_conflict"


def test_no_task_id_hacks_in_target_resolution():
    """target_resolution must not reference adversarial task IDs."""
    import agent.retrieval.target_resolution as tr

    src = open(tr.__file__, encoding="utf-8").read()
    for frag in ("adv_repair", "adv_feature", "adv_docs", "adversarial12", "av09", "av10"):
        assert frag not in src, f"target_resolution must not reference {frag!r}"
