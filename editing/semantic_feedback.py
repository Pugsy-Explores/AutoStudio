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
_FAILURE_EXPLANATION_MAX = 400


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


# --- Causal failure feedback (constraint-level, no solution hints) ---

def derive_failure_explanation(
    context: dict,
    *,
    patch_result: dict | None = None,
    semantic_feedback: dict | None = None,
) -> str:
    """
    Derive short factual failure_explanation from existing signals.
    No heuristics, no solution hints. Only what failed and why (constraint-level).
    """
    pvd = context.get("patch_validation_debug") or {}
    reject_reason = (
        (patch_result or {}).get("patch_reject_reason")
        or (patch_result or {}).get("failure_reason_code")
        or pvd.get("reason")
        or ""
    )
    reject_reason = str(reject_reason).strip().lower()

    # Patch reject reasons (pre-apply)
    if reject_reason == "patch_unchanged" or reject_reason == "patch_unchanged_repeat":
        return "Your patch did not modify the file (old == new)."
    if reject_reason == "no_progress_repeat":
        return "You repeated a previously attempted patch. Produce a different change."
    if reject_reason == "no_effect_change":
        return "Your patch did not modify the file (old == new)."
    if reject_reason == "patch_apply_failed":
        return "The OLD snippet does not exist in the current file content."
    if reject_reason == "wrong_target_file":
        return "The patch targeted a different file than the intended edit target."
    if reject_reason == "weakly_grounded_patch":
        return "The patch is not grounded in the provided file content."
    if reject_reason in ("target_not_found", "no_meaningful_diff"):
        return "The OLD snippet does not exist in the current file content."

    # Syntax validation
    sv = pvd.get("syntax_validation") or context.get("syntax_validation_result") or {}
    if not sv.get("valid") and sv.get("error"):
        err = str(sv.get("error", ""))[:200]
        return f"The patch produces invalid syntax: {err}"

    # Verification checks
    pv = pvd.get("patch_verification") or {}
    if not pv.get("valid") and pv.get("reason"):
        return f"The patch failed verification: {pv.get('reason', '')}"[:_FAILURE_EXPLANATION_MAX]

    # Test failure (semantic_feedback)
    if semantic_feedback and not semantic_feedback.get("tests_passed", True):
        summary = semantic_feedback.get("failure_summary", "")
        if summary:
            s = str(summary)[:_FAILURE_EXPLANATION_MAX - 20]
            return f"Tests failed: {s}"
        return "Tests failed."

    # Fallback from reject_reason
    if reject_reason:
        return f"The patch was rejected: {reject_reason}."

    return "The previous patch failed."


def format_stateful_feedback_for_retry(
    failures: list[str],
    attempted_actions: list[str],
    stagnation_count: int,
) -> str:
    """
    Format FAILURE_STATE block for retry. Exposes accumulated state, no solution hints.
    Uses action summaries (human-readable) for previous attempts.
    """
    last_failures = failures[-3:] if len(failures) > 3 else failures
    last_attempts = attempted_actions[-3:] if len(attempted_actions) > 3 else attempted_actions
    fail_lines = ["  - " + (f[:200] + "..." if len(f) > 200 else f) for f in last_failures] if last_failures else ["  (none)"]
    attempt_lines = ["  - " + s for s in last_attempts] if last_attempts else ["  (none)"]
    return "\n\n".join([
        "FAILURE_STATE:",
        "- Known failures:",
        "\n".join(fail_lines),
        "- Previous attempts:",
        "\n".join(attempt_lines),
        f"- Stagnation count: {stagnation_count}",
        "",
        "REQUIREMENT:",
        "- You MUST produce a patch that is different from previous attempts.",
        "- You MUST address at least one of the known failures.",
        "- Avoid identical patches; modifying same location is allowed if needed.",
    ])


def format_causal_feedback_for_retry(previous_patch: dict, failure_explanation: str) -> str:
    """
    Format causal feedback block for retry. Single source of delta.
    Prepended to instruction. No solution hints.
    """
    old_val = (previous_patch.get("old") or "")[:150]
    new_val = (previous_patch.get("new") or "")[:150]
    if old_val or new_val:
        patch_summary = f"old: {old_val!r} -> new: {new_val!r}"
    else:
        patch_summary = "(patch summary unavailable)"
    exp = (failure_explanation or "The previous patch failed.")[:_FAILURE_EXPLANATION_MAX]
    return "\n\n".join([
        "PREVIOUS_ATTEMPT:",
        f"- Patch: {patch_summary}",
        f"- Failure: {exp}",
        "",
        "REQUIREMENT:",
        "- You MUST produce a DIFFERENT patch.",
        "- You MUST resolve the above failure.",
        "- Do NOT repeat or trivially modify the previous patch.",
    ])


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


_ACTION_SUMMARY_SNIPPET_LEN = 40


def summarize_patch_action(patch: dict | None) -> str:
    """
    Human-readable summary of what was tried. No AST, no symbol inference beyond existing data.
    Single source of truth for action-level representation in retry prompt.
    """
    if not patch or not isinstance(patch, dict):
        return "(no patch)"
    file_path = (patch.get("file") or "").strip()
    symbol = (patch.get("symbol") or "").strip()
    old = (patch.get("old") or "").strip()
    new = (patch.get("new") or "").strip()
    if symbol:
        return f"Edited {symbol} in {file_path}: {old[:_ACTION_SUMMARY_SNIPPET_LEN]} → {new[:_ACTION_SUMMARY_SNIPPET_LEN]}"
    return f"Edited code in {file_path}: {old[:_ACTION_SUMMARY_SNIPPET_LEN]} → {new[:_ACTION_SUMMARY_SNIPPET_LEN]}"


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
    attempted_patches: list[str] | None = None,
) -> tuple[bool, bool, str]:
    """
    Check that retry patch is structurally different and targets same file/symbol.
    Returns (changed, same_target, reject_reason).
    reject_reason is non-empty when check fails.
    If attempted_patches provided and new_sig in it, returns no_progress_repeat.
    """
    new_prev = extract_previous_patch(new_patch_plan)
    if not new_prev:
        return True, True, ""  # No previous from new plan, allow

    new_sig = patch_signature(new_prev)

    # Stateful: reject if new patch was already attempted
    if attempted_patches and new_sig in attempted_patches:
        return False, True, "no_progress_repeat"

    if not previous_patch:
        return True, True, ""
    old_sig = patch_signature(previous_patch)
    if new_sig == old_sig:
        return False, True, "patch_unchanged_repeat"

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
