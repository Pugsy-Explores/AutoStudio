"""Shared test runner utilities. Used by run_tests tool and execution loop."""

import logging
import re
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_TEST_CMD = "python -m pytest -x -q"

# Stdlib module names that shadow local packages. When workspace has logging/ and we run
# pytest from workspace with PYTHONPATH=., pytest loads workspace logging before stdlib.
# Run from parent with no PYTHONPATH so pytest loads stdlib first.
_STDLIB_SHADOW_DIRS = frozenset({"logging", "config", "parser", "ast", "types"})


def _workspace_has_stdlib_shadowing(project_root: str) -> bool:
    """True if project_root has a top-level dir that shadows a stdlib module."""
    root = Path(project_root)
    if not root or not root.is_dir():
        return False
    for name in _STDLIB_SHADOW_DIRS:
        if (root / name).is_dir():
            return True
    return False


def _transform_pytest_cmd_for_shadowing(cmd: str, project_root: str) -> tuple[str, str] | None:
    """
    When project_root has stdlib-shadowing packages and cmd uses pytest with PYTHONPATH=.,
    return (transformed_cmd, parent_cwd) so pytest loads stdlib first.
    Returns None if no transformation needed.
    """
    if not _workspace_has_stdlib_shadowing(project_root):
        return None
    if "pytest" not in cmd.lower():
        return None
    transformed = re.sub(r"^PYTHONPATH=[^\s]+\s+", "", cmd.strip())
    if transformed == cmd:
        return None
    ws_name = Path(project_root).name
    transformed = re.sub(r"\btests/([\w/]+\.py)", rf"{ws_name}/tests/\1", transformed)
    parent = str(Path(project_root).parent)
    return (transformed, parent)


def run_tests_raw(
    project_root: str,
    test_cmd: str | None = None,
    timeout: int = 120,
) -> tuple[bool, str, str, str | None]:
    """
    Run project tests. Returns (passed, stdout, stderr, error_type).
    error_type is None on success; otherwise "test_failure" | "timeout" | "exception".
    When project_root has stdlib-shadowing dirs (logging/, config/, etc.), runs from
    parent with stripped PYTHONPATH so pytest loads stdlib first.
    """
    cmd = test_cmd or DEFAULT_TEST_CMD
    cwd = project_root
    transformed = _transform_pytest_cmd_for_shadowing(cmd, project_root)
    if transformed is not None:
        cmd, cwd = transformed
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=cwd,
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
