"""
Minimal semantic feedback extraction from test failures.
Enables test-driven iteration: convert test output to structured signal for EDIT retry.
No heuristics, basic structure only.
"""

from __future__ import annotations

import re

from config.editing_config import SEMANTIC_FEEDBACK_MAX_SUMMARY
_MAX_FAILING_TESTS = 5
_MAX_ERROR_LEN = 200
_MAX_EXPECTED_LEN = 100
_MAX_ACTUAL_LEN = 100


def extract_semantic_feedback(test_result: dict) -> dict:
    """
    Extract structured failure signal from test result.
    Kept SHORT (truncated).

    Args:
        test_result: {passed, stdout, stderr, error_type?} from run_tests.

    Returns:
        {
            "tests_passed": bool,
            "failure_summary": str,
            "failing_tests": [
                {"name": str, "error": str, "expected": str|None, "actual": str|None}
            ]
        }
    """
    passed = test_result.get("passed", True)
    if passed:
        return {
            "tests_passed": True,
            "failure_summary": "",
            "failing_tests": [],
        }

    stdout = test_result.get("stdout", "") or ""
    stderr = test_result.get("stderr", "") or ""
    combined = (stdout + "\n" + stderr).strip()

    failing_tests = _parse_failing_tests(combined)
    failure_summary = _build_failure_summary(combined, failing_tests)

    return {
        "tests_passed": False,
        "failure_summary": failure_summary[:SEMANTIC_FEEDBACK_MAX_SUMMARY],
        "failing_tests": failing_tests[: _MAX_FAILING_TESTS],
    }


def _parse_failing_tests(combined: str) -> list[dict]:
    """Parse FAILED lines into structured entries. Basic structure only."""
    # Pytest: "FAILED path::test_name - ErrorType: message" or "path::test_name FAILED"
    # Also: "path::test_name - ErrorType: message"
    pattern = re.compile(
        r"(?:FAILED\s+)?"
        r"([a-zA-Z0-9_/.\-]+\.py::[a-zA-Z0-9_]+)"
        r"(?:\s+-\s+|\s+FAILED\s*)?"
        r"([^\n]*)",
        re.MULTILINE,
    )
    seen = set()
    result = []
    for m in pattern.finditer(combined):
        name = m.group(1).strip()
        if name in seen:
            continue
        seen.add(name)
        rest = m.group(2).strip()
        error, expected, actual = _parse_error_line(rest)
        result.append({
            "name": name,
            "error": (error or rest or "test failed")[:_MAX_ERROR_LEN],
            "expected": expected[: _MAX_EXPECTED_LEN] if expected else None,
            "actual": actual[:_MAX_ACTUAL_LEN] if actual else None,
        })
    if not result:
        # Fallback: single synthetic entry from first non-empty line
        lines = [l.strip() for l in combined.split("\n") if l.strip()]
        first = lines[0][:_MAX_ERROR_LEN] if lines else "tests failed"
        result.append({"name": "unknown", "error": first, "expected": None, "actual": None})
    return result


def _parse_error_line(line: str) -> tuple[str | None, str | None, str | None]:
    """Extract error, expected, actual from assertion-like output."""
    if not line:
        return None, None, None
    # "AssertionError: assert 1 == 2"
    # "assert 1 == 2" -> expected "2", actual "1"
    match = re.search(r"assert\s+(.+?)\s*==\s*(.+)", line)
    if match:
        actual = match.group(1).strip()
        expected = match.group(2).strip()
        error = line
        return error, expected, actual
    # "AssertionError: expected X, got Y"
    match = re.search(r"expected\s+(.+?),\s*got\s+(.+)", line, re.I)
    if match:
        expected = match.group(1).strip()
        actual = match.group(2).strip()
        return line, expected, actual
    return line, None, None


def _build_failure_summary(combined: str, failing_tests: list[dict]) -> str:
    """Short structured summary."""
    if not failing_tests:
        return combined.strip()[:SEMANTIC_FEEDBACK_MAX_SUMMARY]
    parts = []
    for t in failing_tests[:3]:
        parts.append(f"{t['name']}: {t['error']}")
    return "; ".join(parts)


def format_semantic_feedback_for_instruction(semantic_feedback: dict) -> str:
    """
    Format semantic feedback for injection into EDIT instruction.
    Includes improvement constraint.
    """
    if not semantic_feedback or semantic_feedback.get("tests_passed", True):
        return ""
    summary = semantic_feedback.get("failure_summary", "")
    failing = semantic_feedback.get("failing_tests", [])
    lines = [
        "SEMANTIC_FEEDBACK:",
        "Previous patch failed due to the following errors.",
        "Modify the implementation to fix these failures.",
        "",
        f"Failure summary: {summary}",
    ]
    for t in failing[:3]:
        lines.append(f"- {t.get('name', '?')}: {t.get('error', '')}")
        if t.get("expected") or t.get("actual"):
            lines.append(f"  expected: {t.get('expected', '?')}; actual: {t.get('actual', '?')}")
    lines.append("")
    lines.append("Do NOT repeat previous patch. Change behavior to fix failing tests.")
    return "\n".join(lines)


# --- Semantic iteration (generalized improvement contract) ---

def extract_previous_patch(patch_plan: dict) -> dict | None:
    """
    Extract previous_patch = {old, new} from patch plan for structural comparison.
    text_sub: old, new. insert/replace/delete: old="", new=code (or symbol+code).
    """
    changes = patch_plan.get("changes") or []
    if not changes:
        return None
    c = changes[0]
    patch = c.get("patch")
    if not isinstance(patch, dict):
        return None
    action = patch.get("action", "")
    file_path = c.get("file", "") or patch.get("file", "")
    symbol = c.get("symbol", "") or patch.get("symbol", "")
    if action == "text_sub":
        return {
            "old": patch.get("old", ""),
            "new": patch.get("new", ""),
            "file": file_path,
            "symbol": symbol or None,
        }
    code = patch.get("code", "")
    return {
        "old": "",
        "new": str(code),
        "file": file_path,
        "symbol": symbol or None,
    }


def patch_signature(prev: dict | None) -> str:
    """Canonical string for structural comparison. Deterministic."""
    if not prev:
        return ""
    # Normalize: strip whitespace, consistent order
    old = (prev.get("old") or "").strip()
    new = (prev.get("new") or "").strip()
    file_path = (prev.get("file") or "").strip()
    symbol = (prev.get("symbol") or "").strip()
    return f"{file_path}|{symbol}|{old}->{new}"


def normalize_failure_signature(failure_summary: str) -> str:
    """Normalized signature for stagnation detection."""
    s = (failure_summary or "").strip().lower()
    # Collapse whitespace
    s = re.sub(r"\s+", " ", s)
    return s[:300]  # Bound


def format_previous_attempt_for_instruction(previous_patch: dict, previous_failure: dict) -> str:
    """
    Format PREVIOUS_ATTEMPT block for retry. Includes PATCH (OLD/NEW) and FAILURE.
    """
    lines = [
        "PREVIOUS_ATTEMPT:",
        "",
        "PATCH:",
        "  OLD:",
        _indent_block(previous_patch.get("old") or ""),
        "  NEW:",
        _indent_block(previous_patch.get("new") or ""),
        "",
        "FAILURE:",
        _indent_block(previous_failure.get("failure_summary") or ""),
        "",
        "Your new patch MUST meaningfully differ from the previous patch and address the failure.",
        "Do NOT repeat or trivially modify the previous change.",
        "Modify the logic to fix the failing behavior.",
    ]
    return "\n".join(lines)


def _indent_block(text: str, prefix: str = "    ") -> str:
    if not text:
        return prefix + "(none)"
    return "\n".join(prefix + line for line in text.split("\n"))


def check_structural_improvement(
    new_patch_plan: dict,
    previous_patch: dict | None,
    binding: dict | None,
) -> tuple[bool, bool, str]:
    """
    Check that retry patch is structurally different and targets same file/symbol.
    Returns (changed, same_target, reject_reason).
    reject_reason is non-empty when check fails.
    """
    if not previous_patch:
        return True, True, ""
    new_prev = extract_previous_patch(new_patch_plan)
    if not new_prev:
        return True, True, ""  # No previous from new plan, allow

    new_sig = patch_signature(new_prev)
    old_sig = patch_signature(previous_patch)
    if new_sig == old_sig:
        return False, True, "patch_unchanged"

    # Same target check: new plan must target binding.file and binding.symbol
    binding_file = (binding or {}).get("file") or ""
    binding_symbol = (binding or {}).get("symbol") or ""
    new_file = (new_prev.get("file") or "").strip()
    new_symbol = (new_prev.get("symbol") or "").strip()
    # Normalize paths for comparison (both relative)
    def _norm(p: str) -> str:
        return p.replace("\\", "/").strip().lower()
    if binding_file and new_file and _norm(new_file) != _norm(binding_file):
        return True, False, "wrong_target_file"
    if binding_symbol and new_symbol and new_symbol != binding_symbol:
        return True, False, "wrong_target_symbol"

    return True, True, ""
