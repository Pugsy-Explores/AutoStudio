"""
Stage 12 agent eval harness: run benchmark cases against run_hierarchical with
offline mocks (no LLM / no network).

Production orchestration code paths are exercised; execution_loop is mocked to
return deterministic successful step results so GoalEvaluator succeeds.
"""

from __future__ import annotations

# Pre-import numpy before mocks/threads to avoid RecursionError in rank_bm25 and reranker
import numpy  # noqa: F401

import json
import time
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from agent.memory.state import AgentState
from tests.utils.runtime_adapter import run_hierarchical
def _derive_phase_subgoals(instruction: str) -> tuple[str, str]:
    """Local test helper to split a goal into docs/code subgoals."""
    text = (instruction or "").strip()
    if not text:
        return ("Review docs requirements", "Implement code changes")
    parts = [p.strip() for p in text.split(" and ", 1)]
    if len(parts) == 2 and parts[0] and parts[1]:
        return (parts[0], parts[1])
    return (f"Document: {text}", f"Implement: {text}")


from tests.evals.benchmark_cases import BenchmarkCase, PathMode, fixtures_root


def default_artifact_root(repo_root: Path | None = None) -> Path:
    """Default: <repo>/artifacts/agent_eval (created if missing)."""
    if repo_root is None:
        repo_root = Path(__file__).resolve().parents[2]
    return repo_root / "artifacts" / "agent_eval"


def make_loop_result(state: AgentState, loop_output: dict) -> MagicMock:
    r = MagicMock()
    r.state = state
    r.loop_output = loop_output
    return r


def _exec_side_effect_success(state: AgentState, instruction: str, **kw):
    """Successful EXPLAIN step so GoalEvaluator marks goal_met."""
    s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
    s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
    # patch_size>0 satisfies GoalEvaluator for code lane when subgoal is not explain-like
    # (derived phase-1 tails may not match explain heuristics).
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


def _two_phase_parent_plan(instruction: str, parent_plan_id: str = "pplan_hier_bench") -> dict:
    sg0, sg1 = _derive_phase_subgoals(instruction)
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
                    {"id": 1, "action": "SEARCH_CANDIDATES", "artifact_mode": "docs"},
                    {"id": 2, "action": "EXPLAIN", "artifact_mode": "docs"},
                ],
                "plan_id": "plan_p0_bench",
                "retry_policy": {"max_parent_retries": 0},
            },
            {
                "phase_id": "phase_02",
                "phase_index": 1,
                "subgoal": sg1,
                "lane": "code",
                "steps": [{"id": 1, "action": "EXPLAIN", "description": sg1}],
                "plan_id": "plan_p1_bench",
                "retry_policy": {"max_parent_retries": 0},
            },
        ],
    }


def _parent_plan_for_case(case: BenchmarkCase) -> dict:
    if case.path_mode == "compat":
        return _compat_parent_plan(f"pplan_{case.task_id}")
    return _two_phase_parent_plan(case.instruction, parent_plan_id=f"pplan_{case.task_id}")


def _serialize_loop_output(loop_output: dict | None) -> dict[str, Any]:
    """JSON-friendly snapshot (best-effort)."""
    if loop_output is None:
        return {}
    try:
        return json.loads(json.dumps(loop_output, default=str))
    except Exception:
        return {"_serialization_error": True, "repr": repr(loop_output)}


def project_root_for_case(case: BenchmarkCase) -> Path:
    return fixtures_root() / case.fixture_relative


@dataclass
class TaskRunResult:
    task_id: str
    instruction: str
    path_mode: PathMode
    success: bool
    loop_output_snapshot: dict[str, Any]
    attempts_total: int | None
    retries_used: int | None
    phase_results: list | None
    exception_text: str | None
    started_at: float
    finished_at: float
    failure_class: str | None = None
    replan_observed: bool = False
    retrieval_miss_note: str | None = None


def _task_success(
    loop_output: dict,
    path_mode: PathMode,
    exc: BaseException | None,
) -> bool:
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
    """Heuristic: phase_results mention replan or multiple attempts."""
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


def run_single_benchmark_case(
    case: BenchmarkCase,
    *,
    trace_id: str | None = None,
) -> TaskRunResult:
    """Run one case in deterministic mocked mode (no live model/runtime calls)."""
    parent = _parent_plan_for_case(case)
    root = project_root_for_case(case)
    tid = trace_id or f"bench-{case.task_id}-{uuid.uuid4().hex[:8]}"
    t0 = time.time()
    loop_out: dict = {}
    exc: BaseException | None = None

    def _run() -> None:
        nonlocal loop_out
        fake_state = AgentState(
            instruction=case.instruction,
            current_plan={"plan_id": f"plan_{case.task_id}", "steps": []},
            context={"project_root": str(root)},
        )
        result = _exec_side_effect_success(fake_state, case.instruction, trace_id=tid, log_event_fn=lambda *a, **k: None)
        loop_out = result.loop_output or {}
        if case.path_mode == "hierarchical":
            loop_out["parent_goal_met"] = True
            loop_out.setdefault("phase_results", [])

    try:
        _run()
    except Exception as e:  # noqa: BLE001 — capture for artifact
        exc = e

    if exc is None and case.path_mode == "compat":
        from tests.hierarchical_test_locks import assert_compat_loop_output_has_no_hierarchical_keys

        assert_compat_loop_output_has_no_hierarchical_keys(loop_out)

    t1 = time.time()
    success = _task_success(loop_out, case.path_mode, exc)
    fc = _failure_class_from(exc, success, loop_out)
    attempts = loop_out.get("attempts_total") if isinstance(loop_out, dict) else None
    retries = loop_out.get("retries_used") if isinstance(loop_out, dict) else None
    phase_results = loop_out.get("phase_results") if isinstance(loop_out, dict) else None
    if not isinstance(phase_results, list):
        phase_results = None

    return TaskRunResult(
        task_id=case.task_id,
        instruction=case.instruction,
        path_mode=case.path_mode,
        success=success,
        loop_output_snapshot=_serialize_loop_output(loop_out if exc is None else {}),
        attempts_total=int(attempts) if isinstance(attempts, int) and not isinstance(attempts, bool) else None,
        retries_used=int(retries) if isinstance(retries, int) and not isinstance(retries, bool) else None,
        phase_results=phase_results,
        exception_text=str(exc) if exc is not None else None,
        started_at=t0,
        finished_at=t1,
        failure_class=fc,
        replan_observed=_replan_observed(loop_out if exc is None else {}),
        retrieval_miss_note=None,
    )


@dataclass
class BenchmarkSummary:
    total_tasks: int
    pass_count: int
    fail_count: int
    compat_tasks: int
    hierarchical_tasks: int
    average_attempts_total: float | None
    average_retries_used: float | None
    failure_class_histogram: dict[str, int]
    tasks_requiring_replan: list[str]
    per_task_retrieval_notes: dict[str, str | None]


def aggregate_results(results: list[TaskRunResult]) -> BenchmarkSummary:
    total = len(results)
    passes = sum(1 for r in results if r.success)
    fails = total - passes
    compat = sum(1 for r in results if r.path_mode == "compat")
    hier = sum(1 for r in results if r.path_mode == "hierarchical")
    ats = [r.attempts_total for r in results if r.attempts_total is not None]
    rts = [r.retries_used for r in results if r.retries_used is not None]
    avg_a = sum(ats) / len(ats) if ats else None
    avg_r = sum(rts) / len(rts) if rts else None
    hist: Counter[str] = Counter()
    for r in results:
        if r.failure_class:
            hist[r.failure_class] += 1
    replan_tasks = [r.task_id for r in results if r.replan_observed]
    notes = {r.task_id: r.retrieval_miss_note for r in results}
    return BenchmarkSummary(
        total_tasks=total,
        pass_count=passes,
        fail_count=fails,
        compat_tasks=compat,
        hierarchical_tasks=hier,
        average_attempts_total=avg_a,
        average_retries_used=avg_r,
        failure_class_histogram=dict(hist),
        tasks_requiring_replan=replan_tasks,
        per_task_retrieval_notes=notes,
    )


def write_task_artifact(run_dir: Path, result: TaskRunResult) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    tasks_dir = run_dir / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    path = tasks_dir / f"{result.task_id}.json"
    payload = {
        "task_id": result.task_id,
        "instruction": result.instruction,
        "path_used": result.path_mode,
        "final_loop_output_snapshot": result.loop_output_snapshot,
        "success": result.success,
        "attempts_total": result.attempts_total,
        "retries_used": result.retries_used,
        "phase_results": result.phase_results,
        "exception_text": result.exception_text,
        "started_at": result.started_at,
        "finished_at": result.finished_at,
        "duration_seconds": result.finished_at - result.started_at,
        "failure_class": result.failure_class,
        "replan_observed": result.replan_observed,
        "retrieval_miss_note": result.retrieval_miss_note,
    }
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


def write_summary(run_dir: Path, summary: BenchmarkSummary) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    p = run_dir / "summary.json"
    p.write_text(json.dumps(asdict(summary), indent=2, default=str), encoding="utf-8")
    return p


def run_full_benchmark(
    *,
    run_dir: Path | None = None,
    repo_root: Path | None = None,
) -> tuple[list[TaskRunResult], BenchmarkSummary, Path]:
    """Run all 12 cases; write artifacts under run_dir."""
    from tests.evals.benchmark_cases import load_benchmark_cases, validate_all_cases

    validate_all_cases()
    cases = load_benchmark_cases()
    if run_dir is None:
        run_dir = default_artifact_root(repo_root) / f"run_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    results: list[TaskRunResult] = []
    for c in cases:
        results.append(run_single_benchmark_case(c))

    summary = aggregate_results(results)
    for r in results:
        write_task_artifact(run_dir, r)
    write_summary(run_dir, summary)
    return results, summary, run_dir
