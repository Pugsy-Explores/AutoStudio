"""
Edit → Test execution. Snapshot-based rollback (no git); syntax validation before tests.
Simplified: single attempt only; no critic, retry_planner, or semantic_feedback loop.
"""

import logging
import shutil
import tempfile
from pathlib import Path

from config.agent_runtime import ENABLE_SANDBOX, TEST_TIMEOUT
from editing.diff_planner import plan_diff
from editing.patch_executor import execute_patch
from editing.patch_generator import to_structured_patches

from agent.runtime.syntax_validator import validate_project
from agent.tools.run_tests import run_tests
from agent.tools.validation_scope import resolve_inner_loop_validation

logger = logging.getLogger(__name__)

_SANDBOX_IGNORE = shutil.ignore_patterns(
    ".git", "__pycache__", "node_modules", ".venv", "venv", "*.pyc", ".mypy_cache", ".pytest_cache"
)


def _resolve_path(file_path: str, project_root: str) -> Path:
    """Resolve file path relative to project_root."""
    root = Path(project_root).resolve()
    p = Path(file_path)
    if not p.is_absolute():
        p = root / file_path
    return p.resolve()


def _snapshot_files(changes: list[dict], project_root: str) -> dict[Path, str | None]:
    """Snapshot content of files that will be modified. Path -> content; None means new file."""
    snapshot: dict[Path, str | None] = {}
    for c in changes:
        file_path = c.get("file", "")
        if not file_path:
            continue
        path = _resolve_path(file_path, project_root)
        if path.exists():
            try:
                snapshot[path] = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                snapshot[path] = None
        else:
            snapshot[path] = None
    return snapshot


def _rollback_snapshot(snapshot: dict[Path, str | None], project_root: str) -> None:
    """Restore files from snapshot. None = delete file (was new)."""
    for path, content in snapshot.items():
        try:
            if content is None:
                path.unlink(missing_ok=True)
            else:
                path.write_text(content, encoding="utf-8")
        except OSError as e:
            logger.warning("[execution_loop] rollback failed for %s: %s", path, e)


def _run_in_sandbox(project_root: str) -> tuple[str, str | None]:
    """If ENABLE_SANDBOX, copy project to temp dir. Otherwise return (project_root, None)."""
    if not ENABLE_SANDBOX:
        return project_root, None
    temp_dir = tempfile.mkdtemp(prefix="autostudio_sandbox_")
    try:
        shutil.copytree(project_root, temp_dir, dirs_exist_ok=True, ignore=_SANDBOX_IGNORE)
        return temp_dir, project_root
    except Exception as e:
        logger.warning("[execution_loop] sandbox copy failed, using project_root: %s", e)
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass
        return project_root, None


def run_edit_test_fix_loop(
    instruction: str,
    context: dict,
    project_root: str,
    max_attempts: int | None = None,
    timeout: int | None = None,
) -> dict:
    """
    Single-attempt edit pipeline: plan_diff -> to_structured_patches -> execute_patch
    -> validate_project -> run_tests. Snapshot rollback on failure.
    Returns {success, files_modified?, patches_applied?, error?, reason?, attempt?, failure_type?}.
    """
    timeout = timeout or TEST_TIMEOUT
    work_root, original_root = _run_in_sandbox(project_root)
    try:
        return _run_edit_once(instruction, context, work_root, timeout)
    finally:
        if original_root is not None:
            try:
                shutil.rmtree(work_root, ignore_errors=True)
            except Exception as e:
                logger.debug("[execution_loop] sandbox cleanup: %s", e)


def _run_edit_once(instruction: str, context: dict, project_root: str, timeout: int) -> dict:
    """Single-attempt: plan_diff -> to_structured_patches -> execute -> validate -> tests."""
    val_scope = resolve_inner_loop_validation(project_root, context)
    for k, v in val_scope.items():
        if v is not None and k not in context:
            context[k] = v
    test_cmd = val_scope.get("test_cmd")

    current_instruction = context.get("instruction", instruction)
    diff_plan = plan_diff(current_instruction, context)
    changes = diff_plan.get("changes", [])

    if not changes:
        test_result = run_tests(project_root, timeout=timeout, test_cmd=test_cmd)
        if test_result.get("passed"):
            return {"success": True, "files_modified": [], "patches_applied": 0, "attempt": 1, "executed": False}
        return {
            "success": False,
            "error": "no_changes",
            "reason": "Planner produced no changes; validation failed",
            "attempt": 1,
            "failure_type": "no_changes",
            "executed": False,
        }

    patch_plan = to_structured_patches({"changes": changes}, current_instruction, context)

    if patch_plan.get("already_correct"):
        test_result = run_tests(project_root, timeout=timeout, test_cmd=test_cmd)
        if test_result.get("passed"):
            return {"success": True, "files_modified": [], "patches_applied": 0, "attempt": 1, "executed": False}
        return {
            "success": False,
            "error": "tests_failed",
            "reason": (test_result.get("stdout", "") + "\n" + test_result.get("stderr", "")).strip() or "Validation failed",
            "attempt": 1,
            "failure_type": "noop_rejected",
            "executed": False,
        }

    pp_changes = patch_plan.get("changes") or []
    if not pp_changes:
        test_result = run_tests(project_root, timeout=timeout, test_cmd=test_cmd)
        if test_result.get("passed"):
            return {"success": True, "files_modified": [], "patches_applied": 0, "attempt": 1, "executed": False}
        return {
            "success": False,
            "error": "no_changes",
            "reason": "Patch plan empty; validation failed",
            "attempt": 1,
            "failure_type": "no_changes",
            "executed": False,
        }

    snapshot = _snapshot_files(pp_changes, project_root)
    patch_result = execute_patch(patch_plan, project_root)

    if not patch_result.get("success"):
        _rollback_snapshot(snapshot, project_root)
        return {
            "success": False,
            "error": "patch_apply_failed",
            "reason": patch_result.get("reason", "patch apply failed"),
            "attempt": 1,
            "failure_type": "patch_apply_failed",
            "failure_reason_code": patch_result.get("failure_reason_code"),
            "executed": True,
        }

    files_modified = patch_result.get("files_modified") or []
    syntax_result = validate_project(project_root, modified_files=files_modified)
    if not syntax_result.get("valid"):
        _rollback_snapshot(snapshot, project_root)
        return {
            "success": False,
            "error": "syntax_error",
            "reason": syntax_result.get("error", "syntax validation failed"),
            "attempt": 1,
            "failure_type": "syntax_error",
            "failure_reason_code": "syntax_error",
            "executed": True,
        }

    test_result = run_tests(project_root, timeout=timeout, test_cmd=test_cmd)
    if test_result.get("passed"):
        return {
            "success": True,
            "files_modified": patch_result.get("files_modified", []),
            "patches_applied": patch_result.get("patches_applied", 0),
            "attempt": 1,
            "executed": True,
        }

    _rollback_snapshot(snapshot, project_root)
    reason = (test_result.get("stdout", "") + "\n" + test_result.get("stderr", "")).strip() or "Tests failed"
    return {
        "success": False,
        "error": test_result.get("error_type", "test_failure"),
        "reason": reason,
        "attempt": 1,
        "failure_type": "tests_failed",
        "failure_reason_code": "tests_failed",
        "executed": True,
    }
