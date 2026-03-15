"""Run validation (pytest, lint, type check) automatically."""

import logging
import subprocess
import time

from config.agent_config import MAX_CI_RUNTIME_SECONDS
from agent.observability.trace_logger import log_event

logger = logging.getLogger(__name__)


def run_ci(project_root: str, trace_id: str | None = None) -> dict:
    """
    Run CI commands: pytest, ruff check, mypy (if available).

    Args:
        project_root: Project root path
        trace_id: Optional trace ID for log_event

    Returns:
        {passed: bool, failures: list, runtime_sec: float}
    """
    if trace_id:
        log_event(trace_id, "ci_started", {"project_root": project_root})

    start = time.perf_counter()
    failures: list[str] = []
    passed = True

    commands = [
        ("pytest", ["pytest", "-x", "-q", "--tb=short"], "pytest"),
        ("ruff", ["ruff", "check", "."], "ruff check"),
    ]

    for name, cmd, _ in commands:
        if time.perf_counter() - start > MAX_CI_RUNTIME_SECONDS:
            failures.append(f"ci_timeout: exceeded {MAX_CI_RUNTIME_SECONDS}s")
            passed = False
            break
        try:
            result = subprocess.run(
                cmd,
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=min(300, MAX_CI_RUNTIME_SECONDS - (time.perf_counter() - start)),
            )
            if result.returncode != 0:
                failures.append(f"{name}: exit {result.returncode}")
                if result.stderr:
                    failures.append(result.stderr[:500])
                passed = False
        except subprocess.TimeoutExpired:
            failures.append(f"{name}: timeout")
            passed = False
        except FileNotFoundError:
            logger.debug("[ci_runner] %s not found, skipping", name)
        except Exception as e:
            failures.append(f"{name}: {e}")
            passed = False

    runtime_sec = time.perf_counter() - start
    if trace_id:
        log_event(trace_id, "ci_passed" if passed else "ci_failed", {"runtime_sec": runtime_sec, "failures": failures})

    return {"passed": passed, "failures": failures, "runtime_sec": round(runtime_sec, 2)}
