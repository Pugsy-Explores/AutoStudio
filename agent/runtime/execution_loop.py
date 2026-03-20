"""
Edit → Test → Fix execution loop. Snapshot-based rollback (no git); syntax validation
before tests; base_instruction + single retry hint; strategy explorer only when retries exhausted.
"""

import json
import logging
import shutil
import tempfile
from pathlib import Path

from config.agent_runtime import (
    ENABLE_SANDBOX,
    MAX_EDIT_ATTEMPTS,
    MAX_PATCH_FILES,
    MAX_PATCH_LINES,
    MAX_SAME_ERROR_RETRIES,
    TEST_TIMEOUT,
)
from editing.diff_planner import plan_diff
from editing.patch_executor import execute_patch
from editing.patch_generator import to_structured_patches

from agent.runtime.retry_guard import should_retry_strategy as _should_retry_strategy
from agent.runtime.syntax_validator import validate_project
from agent.tools.run_tests import run_tests
from agent.tools.validation_scope import resolve_inner_loop_validation
from agent.retrieval.target_resolution import detect_likely_import_shadowing

try:
    from agent.memory.execution_trajectory_store import append_trajectory as _append_trajectory
except ImportError:
    _append_trajectory = None

logger = logging.getLogger(__name__)

# Directories to ignore when copying for sandbox (reduce size and avoid .git issues)
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
    """
    Snapshot content of files that will be modified. Path -> content; None means new file.
    """
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
                snapshot[path] = None  # treat as skip on rollback (binary)
        else:
            snapshot[path] = None  # new file
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


def _infer_patch_type(patch: dict) -> str:
    """Infer patch text type from patch dict: text_sub, module_append, replace, structured."""
    if not patch or not isinstance(patch, dict):
        return "unknown"
    action = patch.get("action")
    target = patch.get("target_node", "")
    if action == "text_sub":
        return "text_sub"
    if action == "insert" and target == "module_append":
        return "module_append"
    if action in ("replace", "insert", "delete"):
        return "structured"
    return "unknown"


def _extract_validation_path_from_cmd(cmd: str | None) -> str | None:
    """Extract first .py path from validation command (e.g. pytest tests/x.py -> tests/x.py)."""
    if not cmd or not isinstance(cmd, str):
        return None
    import re
    m = re.search(r"[\w./\\]+\.py", cmd)
    return m.group(0).replace("\\", "/") if m else None


def _patch_touched_validation_path(files_modified: list[str], validation_path: str | None) -> bool:
    """True if any modified file matches or is the validation path (heuristic)."""
    if not validation_path or not files_modified:
        return False
    vnorm = validation_path.replace("\\", "/").strip()
    for f in files_modified:
        if not isinstance(f, str):
            continue
        fnorm = f.replace("\\", "/").strip()
        if fnorm == vnorm or fnorm.endswith("/" + vnorm) or vnorm.endswith("/" + fnorm):
            return True
    return False


def _total_patch_lines(changes: list[dict]) -> int:
    """Sum of patch line counts from changes (patch may be str or dict with 'code')."""
    total = 0
    for c in changes:
        patch = c.get("patch")
        if isinstance(patch, str):
            total += patch.count("\n") + (1 if patch.strip() else 0)
        elif isinstance(patch, dict):
            code = patch.get("code") or ""
            total += code.count("\n") + (1 if code.strip() else 0)
    return total


def _run_in_sandbox(project_root: str) -> tuple[str, str | None]:
    """
    If ENABLE_SANDBOX, copy project to temp dir and return (temp_path, original_root).
    Caller must rmtree(temp_path) when done. Otherwise return (project_root, None).
    """
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
    Run edit → test → fix loop with snapshot rollback and retry limits.
    Returns {success, files_modified?, patches_applied?, error?, reason?, attempt?, failure_type?}.
    """
    max_attempts = max_attempts or MAX_EDIT_ATTEMPTS
    timeout = timeout or TEST_TIMEOUT
    last_error: str | None = None
    same_error_count = 0
    trajectory_history: list[dict] = []

    # Sandbox: run in copy if enabled
    work_root, original_root = _run_in_sandbox(project_root)
    try:
        return _run_loop(
            instruction=instruction,
            context=context,
            project_root=work_root,
            max_attempts=max_attempts,
            timeout=timeout,
            trajectory_history=trajectory_history,
        )
    finally:
        if original_root is not None:
            try:
                shutil.rmtree(work_root, ignore_errors=True)
            except Exception as e:
                logger.debug("[execution_loop] sandbox cleanup: %s", e)


def _run_loop(
    instruction: str,
    context: dict,
    project_root: str,
    max_attempts: int,
    timeout: int,
    trajectory_history: list[dict],
) -> dict:
    last_error: str | None = None
    same_error_count = 0

    base_instruction = instruction
    current_instruction = base_instruction

    for attempt in range(1, max_attempts + 1):
        try:
            from agent.observability.metrics import (
                record_metric,
                EXECUTION_LOOP_ATTEMPTS,
            )
            record_metric(EXECUTION_LOOP_ATTEMPTS, 1.0, project_root=project_root, append_jsonl=False)
        except Exception:
            pass
        logger.info("[execution_loop] attempt=%d", attempt)

        current_instruction = context.get("instruction", base_instruction)

        # Stage 25: resolve validation command early so plan_diff can use it for target resolution
        val_scope = resolve_inner_loop_validation(project_root, context)
        for k, v in val_scope.items():
            if v is not None and k not in context:
                context[k] = v

        diff_plan = plan_diff(current_instruction, context)
        changes = diff_plan.get("changes", [])
        if not changes:
            _record_failure(project_root)
            return {
                "success": False,
                "error": "no_changes",
                "reason": "Planner produced no changes",
                "attempt": attempt,
                "failure_type": "no_changes",
            }

        num_files = len({c.get("file", "") for c in changes if c.get("file")})
        total_lines = _total_patch_lines(changes)
        if num_files > MAX_PATCH_FILES or total_lines > MAX_PATCH_LINES:
            _record_failure(project_root)
            return {
                "success": False,
                "error": "patch_rejected",
                "reason": f"Patch exceeds limits (files={num_files} max={MAX_PATCH_FILES}, lines={total_lines} max={MAX_PATCH_LINES})",
                "attempt": attempt,
                "failure_type": "patch_rejected",
            }

        snapshot = _snapshot_files(changes, project_root)
        patch_plan = to_structured_patches({"changes": changes}, current_instruction, context)
        if patch_plan.get("patch_generation_reject") == "weakly_grounded_patch":
            _gen_reject_reason = patch_plan.get("generation_rejected_reason")
            patch_result = {
                "success": False,
                "error": "patch_failed",
                "reason": "No grounded patch could be produced from the planner output",
                "patch_parse_ok": True,
                "patch_apply_ok": False,
                "patch_reject_reason": "weakly_grounded_patch",
                "failure_reason_code": "weakly_grounded_patch",
                "patches_applied": 0,
                # Stage 24: pass through generation rejection details
                "generation_rejected_reason": _gen_reject_reason,
            }
        else:
            patch_result = execute_patch(patch_plan, project_root)
        def _merge_patch_telemetry(extra: dict | None = None) -> None:
            strategies = [c.get("patch_strategy") for c in (patch_plan.get("changes") or []) if c.get("patch_strategy")]
            pp_changes = patch_plan.get("changes") or []
            patch_plan_summary = []
            # Stage 24 + Stage 26: aggregate grounded generation and semantic telemetry
            _s24_fields = (
                "grounded_candidate_count",
                "selected_candidate_rank",
                "patch_candidate_strategy",
                "patch_candidate_evidence_type",
                "patch_candidate_evidence_excerpt",
                "generation_rejected_reason",
                "candidate_rejected_semantic_reason",
                "selected_candidate_out_of_n",
                "candidate_semantic_match_score",
                "requested_symbol_name",
                "requested_return_value",
                "semantic_expectation_type",
            )
            s24_telem: dict = {}
            for c in pp_changes:
                patch = c.get("patch") if isinstance(c.get("patch"), dict) else {}
                patch_plan_summary.append({
                    "file": c.get("file"),
                    "symbol": c.get("symbol"),
                    "patch_strategy": c.get("patch_strategy"),
                    "patch_type": _infer_patch_type(patch),
                })
                for fk in _s24_fields:
                    if fk in c and fk not in s24_telem:
                        s24_telem[fk] = c[fk]
            chosen_file = ""
            for c in pp_changes:
                if c.get("file"):
                    chosen_file = c.get("file", "")
                    break
            base = {
                "patch_parse_ok": patch_result.get("patch_parse_ok"),
                "patch_apply_ok": patch_result.get("patch_apply_ok"),
                "patch_reject_reason": patch_result.get("patch_reject_reason"),
                "failure_reason_code": patch_result.get("failure_reason_code"),
                "patches_applied_this_attempt": patch_result.get("patches_applied"),
                "patch_strategies": strategies,
                "patch_plan_summary": patch_plan_summary,
                "attempted_target_files": context.get("search_target_candidates") or context.get("attempted_target_files"),
                "chosen_target_file": chosen_file,
                "target_resolution": context.get("target_resolution"),
            }
            base.update(s24_telem)
            # Also pull generation_rejected_reason from patch_result when set there
            if "generation_rejected_reason" not in base:
                _pgr = patch_result.get("generation_rejected_reason")
                if _pgr:
                    base["generation_rejected_reason"] = _pgr
            pe = patch_result.get("patch_effectiveness")
            if isinstance(pe, dict):
                base["patch_effectiveness"] = pe
            prev = context.get("edit_patch_telemetry")
            if isinstance(prev, dict):
                for k in (
                    "requested_validation_target",
                    "resolved_validation_command",
                    "resolved_validation_cwd",
                    "validation_scope_kind",
                ):
                    if k in prev:
                        base[k] = prev[k]
            if extra:
                base.update(extra)
            context["edit_patch_telemetry"] = base

        _merge_patch_telemetry()

        if not patch_result.get("success"):
            err = patch_result.get("error", "patch_failed")
            reason = patch_result.get("reason", "")
            fr = patch_result.get("failure_reason_code")
            if fr:
                context["edit_failure_reason"] = fr
            _rollback_snapshot(snapshot, project_root)
            _record_rollback(project_root)
            last_error, same_error_count = _update_same_error(last_error, same_error_count, err)
            if same_error_count >= MAX_SAME_ERROR_RETRIES:
                _record_failure(project_root)
                return {
                    "success": False,
                    "error": err,
                    "reason": reason,
                    "attempt": attempt,
                    "failure_type": err,
                    "failure_reason_code": fr,
                }
            if not _should_retry_strategy(err, attempt, max_attempts):
                _record_failure(project_root)
                return {
                    "success": False,
                    "error": err,
                    "reason": reason,
                    "attempt": attempt,
                    "failure_type": err,
                    "failure_reason_code": fr,
                }
            diagnosis, hints = _critic_and_retry(current_instruction, context, _Eval(reason=reason, status="FAILURE"))
            _apply_hints(base_instruction, context, hints)
            if attempt >= max_attempts:
                _run_strategy_explorer(current_instruction, hints, trajectory_history, context, project_root)
            trajectory_history.append({"attempt": attempt, "failure_type": err, "reason": reason})
            _append_trajectory_on_fail(
                current_instruction, context, changes, patch_plan, reason, err, diagnosis, project_root
            )
            continue

        # Patch applied; validate syntax before running tests
        files_modified = patch_result.get("files_modified", [])
        syntax_result = validate_project(project_root, modified_files=files_modified)
        if not syntax_result.get("valid"):
            _rollback_snapshot(snapshot, project_root)
            _record_rollback(project_root)
            try:
                from agent.observability.metrics import record_metric, SYNTAX_VALIDATION_FAILURES
                record_metric(SYNTAX_VALIDATION_FAILURES, 1.0, project_root=project_root, append_jsonl=False)
            except Exception:
                pass
            _record_failure(project_root)
            context["edit_failure_reason"] = "syntax_error"
            _merge_patch_telemetry({"patch_reject_reason": "project_syntax_invalid_after_patch"})
            return {
                "success": False,
                "error": "syntax_error",
                "reason": syntax_result.get("error", "syntax validation failed"),
                "attempt": attempt,
                "failure_type": "syntax_error",
                "failure_reason_code": "syntax_error",
            }

        val_scope = resolve_inner_loop_validation(project_root, context)
        test_cmd = val_scope.get("test_cmd")
        _merge_patch_telemetry(
            {
                "requested_validation_target": val_scope.get("requested_validation_target"),
                "resolved_validation_command": val_scope.get("resolved_validation_command"),
                "resolved_validation_cwd": val_scope.get("resolved_validation_cwd"),
                "validation_scope_kind": val_scope.get("validation_scope_kind"),
            }
        )
        test_result = run_tests(project_root, timeout=timeout, test_cmd=test_cmd)
        if test_result.get("passed"):
            if _append_trajectory:
                _append_trajectory(
                    goal=current_instruction,
                    plan=[c.get("file", "") for c in changes],
                    retrieved_files=context.get("retrieved_files", [])[:50],
                    patch=json.dumps(patch_plan)[:2000] if patch_plan else "",
                    test_output="",
                    failure_type=None,
                    retry_strategy=None,
                    success=True,
                    project_root=project_root,
                )
            return {
                "success": True,
                "files_modified": patch_result.get("files_modified", []),
                "patches_applied": patch_result.get("patches_applied", 0),
                "attempt": attempt,
            }

        err = test_result.get("error_type", "test_failure")
        stdout = test_result.get("stdout", "")
        stderr = test_result.get("stderr", "")
        reason = (stdout + "\n" + stderr).strip() or "tests failed"
        context["edit_failure_reason"] = "test_failure"
        # RCA telemetry: capture before/after snippets before rollback (Stage 22)
        before_snippets = {}
        after_snippets = {}
        for path, content in snapshot.items():
            try:
                rel = str(path.relative_to(Path(project_root).resolve())).replace("\\", "/")
            except ValueError:
                rel = str(path)
            before_snippets[rel] = (content[:400] + "..." if content and len(content) > 400 else (content or ""))
            if path.exists():
                try:
                    after_content = path.read_text(encoding="utf-8", errors="replace")
                    after_snippets[rel] = (after_content[:400] + "..." if len(after_content) > 400 else after_content)
                except (OSError, UnicodeDecodeError):
                    after_snippets[rel] = "(read failed)"
        val_path = _extract_validation_path_from_cmd(test_cmd)
        touched_val = _patch_touched_validation_path(files_modified, val_path)
        # Stage 25: import/env telemetry for validation failures
        import_telem = detect_likely_import_shadowing(reason)
        chosen_target = (changes[0].get("file") if changes else None) or ""
        _merge_patch_telemetry({
            "patch_reject_reason": "validation_tests_failed",
            "validation_command": test_cmd,
            "validation_failure_summary": reason[:500] if reason else None,
            "rollback_happened": True,
            "edit_rca_before_snippets": before_snippets,
            "edit_rca_after_snippets": after_snippets,
            "patch_touched_validation_path": touched_val,
            "chosen_target_file": chosen_target,
            "validation_cwd": val_scope.get("resolved_validation_cwd"),
            "likely_stdlib_shadowing": import_telem.get("likely_stdlib_shadowing"),
            "module_names_in_validation_error": import_telem.get("module_names_in_error", [])[:5],
        })
        _rollback_snapshot(snapshot, project_root)
        _record_rollback(project_root)
        last_error, same_error_count = _update_same_error(last_error, same_error_count, err)
        if same_error_count >= MAX_SAME_ERROR_RETRIES:
            _record_failure(project_root)
            return {
                "success": False,
                "error": err,
                "reason": reason[:500],
                "attempt": attempt,
                "failure_type": err,
                "failure_reason_code": "test_failure",
            }
        if not _should_retry_strategy(err, attempt, max_attempts):
            _record_failure(project_root)
            return {
                "success": False,
                "error": err,
                "reason": reason[:500],
                "attempt": attempt,
                "failure_type": err,
                "failure_reason_code": "test_failure",
            }
        evaluation = _Eval(reason=reason, status="FAILURE")
        diagnosis, hints = _critic_and_retry(current_instruction, context, evaluation)
        _apply_hints(base_instruction, context, hints)
        if attempt >= max_attempts:
            _run_strategy_explorer(current_instruction, hints, trajectory_history, context, project_root)
        trajectory_history.append({"attempt": attempt, "failure_type": err, "reason": reason[:300]})
        _append_trajectory_on_fail(
            current_instruction, context, changes, patch_plan, reason, err, None, project_root, hints=hints
        )

    _record_failure(project_root)
    return {
        "success": False,
        "error": "max_attempts_exceeded",
        "reason": f"Failed after {max_attempts} attempts",
        "attempt": max_attempts,
        "failure_type": "max_attempts_exceeded",
    }


def _update_same_error(last_error: str | None, same_error_count: int, err: str) -> tuple[str | None, int]:
    if err == last_error:
        return err, same_error_count + 1
    return err, 1


def _record_rollback(project_root: str) -> None:
    try:
        from agent.observability.metrics import record_metric, ROLLBACK_COUNT
        record_metric(ROLLBACK_COUNT, 1.0, project_root=project_root, append_jsonl=False)
    except Exception:
        pass


def _record_failure(project_root: str) -> None:
    try:
        from agent.observability.metrics import record_metric, EXECUTION_LOOP_FAILURES
        record_metric(EXECUTION_LOOP_FAILURES, 1.0, project_root=project_root, append_jsonl=False)
    except Exception:
        pass


def _run_strategy_explorer(
    current_instruction: str,
    hints,
    trajectory_history: list[dict],
    context: dict,
    project_root: str,
) -> None:
    try:
        from agent.observability.metrics import record_metric, STRATEGY_EXPLORER_USAGE
        record_metric(STRATEGY_EXPLORER_USAGE, 1.0, project_root=project_root, append_jsonl=False)
        from config.agent_runtime import MAX_STRATEGIES
        from agent.strategy.strategy_explorer import explore_strategies
        alternatives = explore_strategies(current_instruction, hints, trajectory_history, max_strategies=MAX_STRATEGIES)
        if alternatives:
            context["alternative_strategies"] = alternatives
    except Exception:
        pass


def _append_trajectory_on_fail(
    current_instruction: str,
    context: dict,
    changes: list[dict],
    patch_plan: dict,
    reason: str,
    err: str,
    diagnosis,
    project_root: str,
    hints=None,
) -> None:
    if not _append_trajectory:
        return
    retry_strategy = (getattr(diagnosis, "suggested_strategy", None) if diagnosis else None) or (
        getattr(hints, "strategy", None) if hints else None
    ) or ""
    _append_trajectory(
        goal=current_instruction,
        plan=[c.get("file", "") for c in changes],
        retrieved_files=context.get("retrieved_files", [])[:50],
        patch=json.dumps(patch_plan)[:2000] if patch_plan else "",
        test_output=reason,
        failure_type=err,
        retry_strategy=retry_strategy,
        success=False,
        project_root=project_root,
    )


class _Eval:
    """Minimal evaluation result for critic."""

    def __init__(self, reason: str, status: str):
        self.reason = reason
        self.status = status


def _critic_and_retry(instruction: str, context: dict, evaluation: _Eval) -> tuple:
    """Run critic and retry_planner; return (Diagnosis, RetryHints)."""
    from agent.memory.state import AgentState
    from agent.meta.critic import diagnose
    from agent.meta.evaluator import EvaluationResult
    from agent.meta.retry_planner import plan_retry

    state = AgentState(
        instruction=instruction,
        current_plan=context.get("current_plan", {}),
        completed_steps=[],
        step_results=[],
        context=context,
    )
    eval_result = EvaluationResult(status=evaluation.status, reason=evaluation.reason, score=0.0)
    diagnosis = diagnose(state, eval_result)
    hints = plan_retry(instruction, diagnosis)
    return diagnosis, hints


def _apply_hints(base_instruction: str, context: dict, hints) -> None:
    """Update context from RetryHints. Use base_instruction + single retry hint (no accumulation)."""
    if hints.plan_override:
        context["instruction"] = hints.plan_override
    elif hints.rewrite_query:
        context["instruction"] = base_instruction + "\nRetry hint: " + (hints.rewrite_query or "")
    else:
        context["instruction"] = base_instruction
    if hints.retrieve_files:
        context.setdefault("retrieved_files", []).extend(hints.retrieve_files)
