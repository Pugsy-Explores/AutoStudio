"""
Runtime-only test runner. NOT planner-visible; used by execution loop only.
Detects test framework and runs tests with fallback when no tests run.
"""

import json
import logging
import subprocess
from pathlib import Path

from config.agent_runtime import TEST_TIMEOUT
from editing.test_runner_utils import (
    run_tests_raw,
    is_flaky,
)

logger = logging.getLogger(__name__)


def _detect_test_cmd(project_root: str) -> tuple[str, str]:
    """
    Return (test_cmd, fallback_cmd) for the project.
    Order: pyproject/pytest.ini -> poetry.lock -> tox.ini -> Makefile -> package.json -> go.mod -> Cargo.toml.
    """
    root = Path(project_root)
    if (root / "pyproject.toml").exists() or (root / "pytest.ini").exists():
        return "python -m pytest -x -q", "python -m py_compile ."
    if (root / "poetry.lock").exists():
        return "poetry run pytest -x -q", "poetry run python -m py_compile ."
    if (root / "tox.ini").exists():
        return "tox", "python -m py_compile ."
    makefile = root / "Makefile"
    if makefile.exists():
        content = makefile.read_text()
        if "test:" in content or "test\n" in content:
            return "make test", "make build" if "build:" in content or "build\n" in content else "python -m py_compile ."
    pkg = root / "package.json"
    if pkg.exists():
        try:
            data = json.loads(pkg.read_text())
            scripts = data.get("scripts") or {}
            if "test" in scripts:
                return "npm test", scripts.get("build", "npm run build")
        except Exception:
            pass
        return "npm test", "npm run build"
    if (root / "go.mod").exists():
        return "go test ./...", "go build ./..."
    if (root / "Cargo.toml").exists():
        return "cargo test", "cargo build"
    return "python -m pytest -x -q", "python -m py_compile ."


def run_tests(
    project_root: str,
    timeout: int | None = None,
    test_cmd: str | None = None,
) -> dict:
    """
    Run project tests. Runtime-only; not exposed to planner.
    Returns {passed, stdout, stderr, error_type?, fallback_used?, flaky_detected?}.
    """
    timeout = timeout or TEST_TIMEOUT
    if test_cmd is None:
        test_cmd, fallback_cmd = _detect_test_cmd(project_root)
    else:
        fallback_cmd = "python -m py_compile ."

    passed, stdout, stderr, error_type = run_tests_raw(project_root, test_cmd, timeout)
    out = {"passed": passed, "stdout": stdout or "", "stderr": stderr or "", "error_type": error_type}

    if passed:
        try:
            from agent.observability.metrics import record_metric
            record_metric("test_pass", 1.0, project_root=project_root, append_jsonl=False)
        except Exception:
            pass
        return out

    try:
        from agent.observability.metrics import record_metric
        record_metric("test_fail", 1.0, project_root=project_root, append_jsonl=False)
    except Exception:
        pass
    combined = (stdout or "") + "\n" + (stderr or "")
    if "no tests ran" in combined.lower() or "collected 0 items" in combined.lower():
        passed_fb, stdout_fb, stderr_fb, _ = run_tests_raw(project_root, fallback_cmd, timeout=60)
        out["fallback_used"] = True
        out["passed"] = passed_fb
        out["stdout"] = stdout_fb
        out["stderr"] = stderr_fb
        out["error_type"] = None if passed_fb else "fallback_failed"
        return out

    if error_type == "test_failure" and is_flaky(project_root, test_cmd, combined):
        out["passed"] = True
        out["flaky_detected"] = True
        out["error_type"] = None
    return out
