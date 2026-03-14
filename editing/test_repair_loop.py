"""Test-guided repair loop: run tests after patches, repair on failure."""

import logging
import os
import re
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_REPAIR_ATTEMPTS = 3
DEFAULT_TEST_CMD = "python -m pytest -x -q"
COMPILE_BEFORE_TEST = os.environ.get("COMPILE_BEFORE_TEST", "1").lower() in ("1", "true", "yes")


def _is_test_repair_enabled() -> bool:
    """Set TEST_REPAIR_ENABLED=0 to skip test run (patch only)."""
    return os.environ.get("TEST_REPAIR_ENABLED", "1").lower() in ("1", "true", "yes")


def _run_tests(project_root: str, test_cmd: str | None = None) -> tuple[bool, str, str | None, str | None]:
    """
    Run project tests. Returns (passed, stdout, stderr, error_type).
    """
    cmd = test_cmd or os.environ.get("TEST_CMD", DEFAULT_TEST_CMD)
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=120,
        )
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        if result.returncode == 0:
            return True, stdout, stderr, None
        # Parse failure: extract file, error type, stacktrace
        error_type = "test_failure"
        return False, stdout, stderr, error_type
    except subprocess.TimeoutExpired:
        return False, "", "", "timeout"
    except Exception as e:
        return False, "", str(e), "exception"


def _extract_failed_test(combined: str) -> str | None:
    """Extract failed test node id from pytest output (e.g. tests/test_foo.py::test_bar)."""
    match = re.search(r"FAILED\s+([a-zA-Z0-9_/\\.-]+\\.py::[a-zA-Z0-9_]+)", combined)
    if match:
        return match.group(1).strip()
    match = re.search(r"([a-zA-Z0-9_/\\.-]+\\.py::[a-zA-Z0-9_]+)\s+FAILED", combined)
    if match:
        return match.group(1).strip()
    return None


def _is_flaky(project_root: str, test_cmd: str, failure_output: str) -> bool:
    """
    Re-run failing test 2x. If 1 pass / 1 fail, treat as flaky.
    Returns True if flaky (skip repair).
    """
    test_node = _extract_failed_test(failure_output)
    if not test_node:
        return False
    run_cmd = f"python -m pytest {test_node} --count=2 -q"
    try:
        result = subprocess.run(
            run_cmd,
            shell=True,
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=60,
        )
        out = (result.stdout or "") + (result.stderr or "")
        pass_count = out.count(" passed")
        fail_count = out.count(" failed")
        if pass_count >= 1 and fail_count >= 1:
            logger.info("[test_repair] flaky test detected, skipping repair")
            return True
    except Exception as e:
        logger.debug("[test_repair] flaky check failed: %s", e)
    return False


def _run_compile(files_modified: list[str], project_root: str) -> tuple[bool, str]:
    """Run py_compile on modified files. Returns (success, error_message)."""
    if not files_modified:
        return True, ""
    try:
        result = subprocess.run(
            ["python", "-m", "py_compile"] + list(files_modified),
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return True, ""
        return False, (result.stderr or result.stdout or "compile failed")[:500]
    except Exception as e:
        return False, str(e)[:500]


def _parse_pytest_failure(stdout: str, stderr: str) -> tuple[str | None, str | None, str]:
    """Extract file, error_type, and stacktrace from pytest output."""
    combined = stdout + "\n" + stderr
    file_match = re.search(r"([a-zA-Z0-9_/\\.-]+\.py)(?::\d+)?", combined)
    file_path = file_match.group(1) if file_match else None
    # Try to get first line of traceback
    tb_match = re.search(r"(?:FAILED|Error|AssertionError|Exception)[^\n]*\n([\s\S]{0,500})", combined)
    stacktrace = tb_match.group(0).strip() if tb_match else combined[:500]
    return file_path, "test_failure", stacktrace


def run_with_repair(
    patch_plan: dict,
    project_root: str,
    context: dict,
    max_attempts: int = MAX_REPAIR_ATTEMPTS,
    test_cmd: str | None = None,
) -> dict:
    """
    Execute patch, run tests, and repair on failure.
    patch_plan: {changes: [...]} in patch_executor format.
    context: state.context for diff_planner (ranked_context, etc.).
    Returns {success, files_modified?, patches_applied?, repair_attempts?, error?}.
    """
    from editing.patch_executor import execute_patch
    from editing.patch_generator import to_structured_patches

    instruction = context.get("instruction", "")
    current_plan = patch_plan
    last_result: dict = {}

    for attempt in range(max_attempts):
        logger.info("[test_repair] attempt=%d", attempt + 1)

        result = execute_patch(current_plan, project_root)
        if not result.get("success"):
            return {
                "success": False,
                "error": result.get("error", "patch_failed"),
                "reason": result.get("reason", ""),
                "repair_attempts": attempt,
            }

        if not _is_test_repair_enabled():
            return {
                "success": True,
                "files_modified": result.get("files_modified", []),
                "patches_applied": result.get("patches_applied", 0),
                "repair_attempts": attempt,
            }

        files_modified = result.get("files_modified", [])
        if COMPILE_BEFORE_TEST and files_modified:
            compile_ok, compile_err = _run_compile(files_modified, project_root)
            if not compile_ok:
                repair_instruction = f"Fix syntax/compile error:\n{compile_err}"
                logger.info("[test_repair] compile failed, planning repair")
                from editing.diff_planner import plan_diff

                repair_context = dict(context)
                repair_context["instruction"] = repair_instruction
                repair_context["ranked_context"] = repair_context.get("ranked_context", [])
                diff_plan = plan_diff(repair_instruction, repair_context)
                changes = diff_plan.get("changes", [])
                if changes:
                    current_plan = to_structured_patches(diff_plan, repair_instruction, repair_context)
                    continue
                return {
                    "success": False,
                    "error": "compile_failed",
                    "reason": compile_err,
                    "repair_attempts": attempt + 1,
                }

        passed, stdout, stderr, error_type = _run_tests(project_root, test_cmd)
        if passed:
            return {
                "success": True,
                "files_modified": result.get("files_modified", []),
                "patches_applied": result.get("patches_applied", 0),
                "repair_attempts": attempt,
            }

        combined = (stdout or "") + "\n" + (stderr or "")
        if _is_flaky(project_root, test_cmd, combined):
            return {
                "success": True,
                "files_modified": result.get("files_modified", []),
                "patches_applied": result.get("patches_applied", 0),
                "repair_attempts": attempt,
                "flaky_detected": True,
            }

        # Parse failure and plan repair
        file_path, err_type, stacktrace = _parse_pytest_failure(stdout or "", stderr or "")
        repair_instruction = f"Fix test failure in {file_path or 'unknown'}: {err_type}\n{stacktrace}"
        logger.info("[test_repair] planning repair: %s", repair_instruction[:100])

        from editing.diff_planner import plan_diff

        repair_context = dict(context)
        repair_context["instruction"] = repair_instruction
        repair_context["ranked_context"] = repair_context.get("ranked_context", [])
        if file_path:
            repair_context.setdefault("retrieved_files", []).append(file_path)

        diff_plan = plan_diff(repair_instruction, repair_context)
        changes = diff_plan.get("changes", [])
        if not changes:
            return {
                "success": False,
                "error": "repair_no_changes",
                "reason": "Planner produced no repair changes",
                "repair_attempts": attempt + 1,
            }

        current_plan = to_structured_patches(diff_plan, repair_instruction, repair_context)
        last_result = result

    return {
        "success": False,
        "error": "repair_exhausted",
        "reason": f"Repair failed after {max_attempts} attempts",
        "repair_attempts": max_attempts,
    }
