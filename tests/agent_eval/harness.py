"""
Run core12 tasks against `run_hierarchical` with offline mocks (no LLM / no network).

Reuses the same mock strategy as `tests/evals/agent_eval_harness.py` but resolves
fixture roots under `tests/agent_eval/fixtures/`.
"""

from __future__ import annotations

# Pre-import numpy before mocks/threads to avoid RecursionError in rank_bm25 and reranker
import numpy  # noqa: F401

import json
import subprocess
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal
from unittest.mock import MagicMock, patch

from agent.memory.state import AgentState
from agent.orchestrator.deterministic_runner import run_hierarchical
from agent.orchestrator.plan_resolver import _derive_phase_subgoals

from tests.agent_eval.task_specs import TaskSpec

ExecutionMode = Literal["mocked", "real"]


def index_workspace(workspace: Path) -> dict[str, Any]:
    """
    Build the symbol graph + SQLite index under ``workspace/.symbol_graph``.

    Matches production and tests such as ``tests/test_agent_e2e.py`` so retrieval,
    repo map, and graph stages see real fixture symbols instead of an empty index.
    Embeddings are off by default for speed (set ``INDEX_EMBEDDINGS=1`` before first
    ``repo_index.indexer`` import if you need vector index in the same process).
    """
    import os

    os.environ.setdefault("INDEX_EMBEDDINGS", "0")
    from repo_index.indexer import index_repo

    out = workspace / ".symbol_graph"
    out.mkdir(parents=True, exist_ok=True)
    symbols, db_path = index_repo(str(workspace.resolve()), output_dir=str(out))
    return {
        "ok": True,
        "symbol_count": len(symbols),
        "index_sqlite": db_path,
        "symbol_graph_dir": str(out),
        "symbols_json": str(out / "symbols.json"),
    }


def _serialize_loop_output(loop_output: dict | None) -> dict[str, Any]:
    if loop_output is None:
        return {}
    try:
        from agent.observability.json_sanitize import json_safe_tree

        safe = json_safe_tree(loop_output)
        return json.loads(json.dumps(safe, default=str))
    except Exception as e:
        # Never call repr() on raw loop_output — cyclic graphs can RecursionError.
        return {"_serialization_error": True, "error": str(e)[:2000]}


def make_loop_result(state: AgentState, loop_output: dict) -> MagicMock:
    r = MagicMock()
    r.state = state
    r.loop_output = loop_output
    return r


def _exec_side_effect_success(state: AgentState, instruction: str, **kw):
    s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
    s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
    s.step_results = [
        type(
            "SR",
            (),
            {"action": "EXPLAIN", "success": True, "patch_size": 1, "files_modified": []},
        )()
    ]
    return make_loop_result(
        s,
        {
            "completed_steps": 1,
            "errors_encountered": [],
            "tool_calls": 1,
            "patches_applied": 0,
            "files_modified": [],
            "plan_result": state.current_plan,
            "start_time": 0.0,
        },
    )


def _compat_get_plan() -> dict:
    return {
        "plan_id": "bench_compat_plan",
        "steps": [
            {"id": 1, "action": "EXPLAIN", "description": "benchmark compat task", "reason": "stage12"},
        ],
    }


def _compat_parent_plan(parent_plan_id: str = "pplan_compat_bench") -> dict:
    return {
        "parent_plan_id": parent_plan_id,
        "compatibility_mode": True,
        "phases": [{}],
    }


def _is_docs_consistency_task(spec: TaskSpec) -> bool:
    """True when task requires doc/code alignment + validation (generic task semantics)."""
    tags = getattr(spec, "tags", ()) or ()
    return "docs" in tags and "consistency" in tags


def _is_explain_artifact_task(spec: TaskSpec) -> bool:
    """True when task requires writing an artifact file (generic task semantics)."""
    return getattr(spec, "grading_mode", "") == "explain_artifact"


def _two_phase_parent_plan(
    instruction: str,
    parent_plan_id: str = "pplan_hier_bench",
    spec: TaskSpec | None = None,
) -> dict:
    sg0, sg1 = _derive_phase_subgoals(instruction)
    # Stage 15: pass subgoal as query/description so SEARCH_CANDIDATES and EXPLAIN get usable context.
    # Stage 16: phase 1 shape varies by task class — docs-consistency needs EDIT, explain-artifact needs WRITE_ARTIFACT.
    phase_1_steps = _build_phase_1_steps(sg1, spec)
    return {
        "parent_plan_id": parent_plan_id,
        "instruction": instruction,
        "decomposition_type": "two_phase_docs_code",
        "compatibility_mode": False,
        "phases": [
            {
                "phase_id": "phase_01",
                "phase_index": 0,
                "subgoal": sg0,
                "lane": "docs",
                "steps": [
                    {
                        "id": 1,
                        "action": "SEARCH_CANDIDATES",
                        "artifact_mode": "docs",
                        "description": sg0,
                        "query": sg0,
                    },
                    {
                        "id": 2,
                        "action": "BUILD_CONTEXT",
                        "artifact_mode": "docs",
                        "description": "Build docs context from candidates",
                    },
                    {
                        "id": 3,
                        "action": "EXPLAIN",
                        "artifact_mode": "docs",
                        "description": sg0,
                    },
                ],
                "plan_id": "plan_p0_bench",
                "retry_policy": {"max_parent_retries": 0},
            },
            {
                "phase_id": "phase_02",
                "phase_index": 1,
                "subgoal": sg1,
                "lane": "code",
                "steps": phase_1_steps,
                "plan_id": "plan_p1_bench",
                "retry_policy": {"max_parent_retries": 0},
            },
        ],
    }


def _build_phase_1_steps(subgoal: str, spec: TaskSpec | None) -> list[dict]:
    """Build phase 1 steps from task semantics. Docs-consistency: SEARCH+EDIT; explain-artifact: SEARCH+EXPLAIN+WRITE_ARTIFACT."""
    if spec and _is_docs_consistency_task(spec):
        # Deterministic: docs-consistency requires SEARCH (grounding) + EDIT (align files) + validation via loop.
        return [
            {"id": 1, "action": "SEARCH", "description": subgoal, "reason": "Locate docs and code to align"},
            {"id": 2, "action": "EDIT", "description": subgoal, "reason": "Align docs with code per instruction"},
        ]
    if spec and _is_explain_artifact_task(spec):
        artifacts = getattr(spec, "expected_artifacts", ()) or ()
        path = artifacts[0] if artifacts else ""
        if path:
            return [
                {"id": 1, "action": "SEARCH", "description": subgoal, "reason": "Locate code/docs for explain artifact"},
                {"id": 2, "action": "EXPLAIN", "description": subgoal, "reason": "Produce explanation for artifact"},
                {"id": 3, "action": "WRITE_ARTIFACT", "artifact_path": path, "description": subgoal},
            ]
    return [{"id": 1, "action": "EXPLAIN", "description": subgoal}]


def _parent_plan_for_spec(spec: TaskSpec) -> dict:
    if spec.orchestration_path == "compat":
        return _compat_parent_plan(f"pplan_{spec.task_id}")
    return _two_phase_parent_plan(spec.instruction, parent_plan_id=f"pplan_{spec.task_id}", spec=spec)


def _task_success(loop_output: dict, path_mode: str, exc: BaseException | None) -> bool:
    if exc is not None:
        return False
    if path_mode == "hierarchical":
        return bool(loop_output.get("parent_goal_met"))
    errs = loop_output.get("errors_encountered") or []
    return isinstance(errs, list) and len(errs) == 0


def _failure_class_from(exc: BaseException | None, success: bool, loop_output: dict) -> str | None:
    if exc is not None:
        return "exception"
    if success:
        return None
    return "goal_or_parent_not_met"


def _replan_observed(loop_output: dict) -> bool:
    prs = loop_output.get("phase_results") or []
    if not isinstance(prs, list):
        return False
    for pr in prs:
        if not isinstance(pr, dict):
            continue
        ah = pr.get("attempt_history") or []
        if isinstance(ah, list) and len(ah) > 1:
            return True
    return False


def run_structural_agent(spec: TaskSpec, project_root: str, *, trace_id: str | None = None) -> dict[str, Any]:
    """Invoke `run_hierarchical` with mocked execution_loop; return loop_output dict."""
    parent = _parent_plan_for_spec(spec)
    tid = trace_id or f"bench-{spec.task_id}-{uuid.uuid4().hex[:8]}"
    loop_out: dict = {}
    exc: BaseException | None = None

    def _run() -> None:
        nonlocal loop_out
        with patch("agent.orchestrator.deterministic_runner.execution_loop") as mock_exec:
            mock_exec.side_effect = _exec_side_effect_success
            with patch(
                "agent.orchestrator.deterministic_runner.get_parent_plan",
                return_value=parent,
            ):
                if spec.orchestration_path == "compat":
                    with patch(
                        "agent.orchestrator.deterministic_runner.get_plan",
                        return_value=_compat_get_plan(),
                    ):
                        _state, loop_out = run_hierarchical(
                            spec.instruction,
                            project_root,
                            trace_id=tid,
                            log_event_fn=lambda *a, **k: None,
                        )
                else:
                    _state, loop_out = run_hierarchical(
                        spec.instruction,
                        project_root,
                        trace_id=tid,
                        log_event_fn=lambda *a, **k: None,
                    )

    try:
        _run()
    except Exception as e:
        exc = e

    if exc is None and spec.orchestration_path == "compat":
        from tests.hierarchical_test_locks import assert_compat_loop_output_has_no_hierarchical_keys

        assert_compat_loop_output_has_no_hierarchical_keys(loop_out)

    success = _task_success(loop_out, spec.orchestration_path, exc)
    return {
        "loop_output": loop_out if exc is None else {},
        "exception": exc,
        "structural_success": success,
        "failure_class": _failure_class_from(exc, success, loop_out if exc is None else {}),
        "replan_observed": _replan_observed(loop_out if exc is None else {}),
        "loop_output_snapshot": _serialize_loop_output(loop_out if exc is None else {}),
        "attempts_total": loop_out.get("attempts_total") if isinstance(loop_out, dict) else None,
        "retries_used": loop_out.get("retries_used") if isinstance(loop_out, dict) else None,
    }


def run_shell_command(cmd: str, *, cwd: Path, timeout: int) -> tuple[int, str, str]:
    """Run a shell command; return (code, stdout, stderr)."""
    p = subprocess.run(
        cmd,
        shell=True,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return p.returncode, p.stdout or "", p.stderr or ""


# Stdlib module names that shadow local packages in adversarial repos.
# When workspace has logging/ and we run pytest from workspace, pytest loads
# workspace logging before stdlib, causing ImportError. Run from parent with
# no PYTHONPATH so pytest loads stdlib first; test file adds workspace via sys.path.
# Note: io/ cannot be fixed—Python's frozen io module always wins.
_STDLIB_SHADOW_DIRS = frozenset({"logging", "config", "parser", "ast", "types"})


def _workspace_has_stdlib_shadowing(workspace: Path) -> bool:
    """True if workspace has a top-level dir that shadows a stdlib module."""
    if not workspace or not workspace.is_dir():
        return False
    for name in _STDLIB_SHADOW_DIRS:
        if (workspace / name).is_dir():
            return True
    return False


def _transform_pytest_cmd_for_shadowing(cmd: str, workspace: Path) -> tuple[str, Path] | None:
    """
    When workspace has stdlib-shadowing packages and cmd uses pytest with PYTHONPATH=.,
    return (transformed_cmd, parent_cwd) so pytest loads stdlib first.
    Test files add workspace via sys.path.insert, so they find the local package.
    Returns None if no transformation needed.
    """
    if not _workspace_has_stdlib_shadowing(workspace):
        return None
    if "pytest" not in cmd.lower():
        return None
    # Strip PYTHONPATH=... from start so pytest loads stdlib
    import re

    transformed = re.sub(r"^PYTHONPATH=[^\s]+\s+", "", cmd.strip())
    if transformed == cmd:
        return None
    # Rewrite tests/X.py -> workspace_name/tests/X.py for cwd=workspace.parent
    ws_name = workspace.name
    transformed = re.sub(r"\btests/([\w/]+\.py)", rf"{ws_name}/tests/\1", transformed)
    parent = workspace.parent
    return (transformed, parent)


def run_validation_commands(spec: TaskSpec, cwd: Path) -> tuple[bool, list[dict[str, Any]]]:
    """Run each validation command in order; all must exit 0."""
    logs: list[dict[str, Any]] = []
    for cmd in spec.validation_commands:
        run_cwd = cwd
        run_cmd = cmd
        transformed = _transform_pytest_cmd_for_shadowing(cmd, cwd)
        if transformed is not None:
            run_cmd, run_cwd = transformed
        code, out, err = run_shell_command(run_cmd, cwd=run_cwd, timeout=spec.timeout_seconds)
        logs.append(
            {"command": run_cmd, "original_command": cmd, "exit_code": code, "stdout": out, "stderr": err}
        )
        if code != 0:
            return False, logs
    return True, logs


def explain_artifact_ok(spec: TaskSpec, cwd: Path) -> tuple[bool, str | None]:
    """Check expected_artifacts exist and contain explain_required_substrings."""
    for rel in spec.expected_artifacts:
        p = cwd / rel
        if not p.is_file():
            return False, f"missing artifact: {rel}"
        text = p.read_text(encoding="utf-8", errors="replace")
        for sub in spec.explain_required_substrings:
            if sub not in text:
                return False, f"missing substring in {rel}: {sub!r}"
    return True, None


@dataclass
class TaskOutcome:
    task_id: str
    success: bool
    validation_passed: bool
    retries_used: int | None
    replans_used: int | None
    attempts_total: int | None
    failure_class: str | None
    files_changed: list[str]
    diff_stat: dict[str, int]
    unrelated_files_changed: list[str]
    bad_edit_patterns: list[str]
    retrieval_miss_signals: list[str]
    notes: str
    structural_success: bool = False
    grading_mode: str = ""
    loop_output_snapshot: dict[str, Any] = field(default_factory=dict)
    validation_logs: list[dict[str, Any]] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_public_dict(self) -> dict[str, Any]:
        """Canonical per-task outcome fields (Stage 12 contract)."""
        return {
            "task_id": self.task_id,
            "success": self.success,
            "validation_passed": self.validation_passed,
            "retries_used": self.retries_used,
            "replans_used": self.replans_used,
            "attempts_total": self.attempts_total,
            "failure_class": self.failure_class,
            "files_changed": self.files_changed,
            "diff_stat": self.diff_stat,
            "unrelated_files_changed": self.unrelated_files_changed,
            "bad_edit_patterns": self.bad_edit_patterns,
            "retrieval_miss_signals": self.retrieval_miss_signals,
            "notes": self.notes,
        }

    def to_json_dict(self) -> dict[str, Any]:
        return asdict(self)


def compute_success(
    spec: TaskSpec,
    *,
    structural_success: bool,
    validation_passed: bool,
    explain_ok: bool | None,
) -> bool:
    if spec.grading_mode == "structural_loop":
        return structural_success
    if spec.grading_mode == "explain_artifact":
        return bool(explain_ok)
    return validation_passed


def _count_replans(loop_snapshot: dict[str, Any]) -> int:
    n = 0
    for pr in loop_snapshot.get("phase_results") or []:
        if not isinstance(pr, dict):
            continue
        ah = pr.get("attempt_history") or []
        if isinstance(ah, list) and len(ah) > 1:
            n += len(ah) - 1
    return n


def run_single_task(
    spec: TaskSpec,
    workspace: Path,
    *,
    trace_id: str | None = None,
    execution_mode: ExecutionMode = "mocked",
) -> TaskOutcome:
    """Setup (optional), index, structural agent (mocked or real execution_loop), validation, outcome."""
    notes_parts: list[str] = []
    for cmd in spec.setup_commands:
        code, out, err = run_shell_command(cmd, cwd=workspace, timeout=spec.timeout_seconds)
        if code != 0:
            notes_parts.append(f"setup_failed: {cmd!r} exit={code} err={err[:500]}")
    index_meta: dict[str, Any]
    try:
        index_meta = index_workspace(workspace)
    except Exception as e:  # noqa: BLE001 — capture for benchmark artifact; continue with run
        index_meta = {"ok": False, "error": str(e)}
        notes_parts.append(f"index_failed: {e!s}")

    git_meta: dict[str, Any] = {"skipped": True}
    if execution_mode == "real":
        from tests.agent_eval.workspace_artifacts import try_git_init_commit

        git_meta = try_git_init_commit(workspace)
        if not git_meta.get("ok"):
            notes_parts.append(f"git_baseline_failed: {git_meta.get('reason')!s}")

    if execution_mode == "real":
        from tests.agent_eval.real_execution import run_structural_agent_real

        structural = run_structural_agent_real(spec, str(workspace), trace_id=trace_id)
    else:
        structural = run_structural_agent(spec, str(workspace), trace_id=trace_id)
    struct_ok = bool(structural["structural_success"])
    retries = structural.get("retries_used")
    attempts = structural.get("attempts_total")
    if isinstance(attempts, bool) or not isinstance(attempts, int):
        attempts = attempts if isinstance(attempts, int) else None
    if isinstance(retries, bool) or not isinstance(retries, int):
        retries = retries if isinstance(retries, int) else None

    loop_snap = structural.get("loop_output_snapshot") or {}
    replans = _count_replans(loop_snap) if isinstance(loop_snap, dict) else 0
    if replans == 0 and structural.get("replan_observed"):
        replans = 1

    files_changed: list[str] = []
    diff_stat = {"insertions": 0, "deletions": 0}
    diff_text = ""
    unrelated: list[str] = []
    bad_patterns: list[str] = []
    ret_signals: list[str] = []

    if execution_mode == "real":
        from tests.agent_eval.workspace_artifacts import (
            git_diff_after,
            heuristic_unrelated_files,
            scan_bad_edit_patterns,
            retrieval_miss_signals_from_loop,
        )

        diff_text, files_changed, diff_stat = git_diff_after(workspace)
        unrelated = heuristic_unrelated_files(files_changed, spec.repo_path)
        bad_patterns = scan_bad_edit_patterns(diff_text)
        ret_signals = retrieval_miss_signals_from_loop(loop_snap)
        lo = structural.get("loop_output") if isinstance(structural.get("loop_output"), dict) else {}
        fm = lo.get("files_modified") if isinstance(lo, dict) else None
        if isinstance(fm, list) and fm:
            for f in fm:
                if isinstance(f, str) and f not in files_changed:
                    files_changed.append(f)

    val_ok = True
    val_logs: list[dict[str, Any]] = []
    if spec.validation_commands:
        val_ok, val_logs = run_validation_commands(spec, workspace)

    explain_ok: bool | None = None
    if spec.grading_mode == "explain_artifact":
        explain_ok, em = explain_artifact_ok(spec, workspace)
        if em:
            notes_parts.append(em)
        validation_passed_flag = bool(explain_ok)
    else:
        validation_passed_flag = val_ok

    success = compute_success(
        spec,
        structural_success=struct_ok,
        validation_passed=validation_passed_flag,
        explain_ok=explain_ok,
    )

    fc = structural.get("failure_class")
    if not val_ok and spec.grading_mode == "validation_exit_code":
        fc = fc or "validation_failed"
    if spec.grading_mode == "explain_artifact" and explain_ok is False:
        fc = fc or "explain_artifact_failed"

    from tests.agent_eval.failure_buckets import classify_failure_bucket, infer_first_failing_stage

    fb = None
    if not success:
        fb = classify_failure_bucket(
            success=success,
            structural_success=struct_ok,
            validation_passed=validation_passed_flag,
            failure_class=fc,
            loop_snapshot=loop_snap,
            validation_logs=val_logs,
            notes="; ".join(notes_parts) if notes_parts else "",
            index_ok=index_meta.get("ok") if isinstance(index_meta, dict) else None,
        )

    first_failing_stage = infer_first_failing_stage(
        success=success,
        structural_success=struct_ok,
        validation_passed=validation_passed_flag,
        loop_snapshot=loop_snap,
    )

    return TaskOutcome(
        task_id=spec.task_id,
        success=success,
        validation_passed=validation_passed_flag,
        retries_used=retries,
        replans_used=replans,
        attempts_total=attempts,
        failure_class=fc,
        files_changed=files_changed,
        diff_stat=diff_stat,
        unrelated_files_changed=unrelated,
        bad_edit_patterns=bad_patterns,
        retrieval_miss_signals=ret_signals,
        notes="; ".join(notes_parts) if notes_parts else "",
        structural_success=struct_ok,
        grading_mode=spec.grading_mode,
        loop_output_snapshot=structural.get("loop_output_snapshot") or {},
        validation_logs=val_logs,
        extra={
            "exception": str(structural["exception"]) if structural.get("exception") else None,
            "index": index_meta,
            "git_baseline": git_meta,
            "execution_mode": execution_mode,
            "failure_bucket": fb,
            "first_failing_stage": first_failing_stage,
            "diff_unified": diff_text[:200000] if diff_text else "",
            "edit_telemetry": (loop_snap.get("edit_telemetry") if isinstance(loop_snap, dict) else None)
            or {},
        },
    )
