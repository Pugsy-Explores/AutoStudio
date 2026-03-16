"""Shared test runner utilities. Used by run_tests tool and execution loop."""

import logging
import re
import subprocess

logger = logging.getLogger(__name__)

DEFAULT_TEST_CMD = "python -m pytest -x -q"


def run_tests_raw(
    project_root: str,
    test_cmd: str | None = None,
    timeout: int = 120,
) -> tuple[bool, str, str, str | None]:
    """
    Run project tests. Returns (passed, stdout, stderr, error_type).
    error_type is None on success; otherwise "test_failure" | "timeout" | "exception".
    """
    cmd = test_cmd or DEFAULT_TEST_CMD
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        if result.returncode == 0:
            return True, stdout, stderr, None
        return False, stdout, stderr, "test_failure"
    except subprocess.TimeoutExpired:
        return False, "", "", "timeout"
    except Exception as e:
        return False, "", str(e), "exception"


def extract_failed_test(combined: str) -> str | None:
    """Extract failed test node id from pytest output (e.g. tests/test_foo.py::test_bar)."""
    match = re.search(r"FAILED\s+([a-zA-Z0-9_/.-]+\.py::[a-zA-Z0-9_]+)", combined)
    if match:
        return match.group(1).strip()
    match = re.search(r"([a-zA-Z0-9_/.-]+\.py::[a-zA-Z0-9_]+)\s+FAILED", combined)
    if match:
        return match.group(1).strip()
    return None


def is_flaky(project_root: str, test_cmd: str, failure_output: str) -> bool:
    """
    Re-run failing test 2x. If 1 pass / 1 fail, treat as flaky.
    Returns True if flaky (caller may treat as success).
    """
    test_node = extract_failed_test(failure_output)
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
            logger.info("[test_runner_utils] flaky test detected")
            return True
    except Exception as e:
        logger.debug("[test_runner_utils] flaky check failed: %s", e)
    return False


def parse_pytest_failure(stdout: str, stderr: str) -> tuple[str | None, str, str]:
    """Extract file, error_type, and stacktrace from pytest output."""
    combined = stdout + "\n" + stderr
    file_match = re.search(r"([a-zA-Z0-9_/\\.-]+\.py)(?::\d+)?", combined)
    file_path = file_match.group(1) if file_match else None
    tb_match = re.search(r"(?:FAILED|Error|AssertionError|Exception)[^\n]*\n([\s\S]{0,500})", combined)
    stacktrace = tb_match.group(0).strip() if tb_match else combined[:500]
    return file_path, "test_failure", stacktrace
