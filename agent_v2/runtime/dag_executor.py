"""
DAG-backed plan execution: compile → schedule ready tasks → snapshot args → dispatch.

Replaces sequential PlanExecutor. PlanDocument is never mutated; runtime lives on ExecutionTask.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Literal, Optional, Protocol

from agent_v2.runtime.plan_compiler import compile_plan_document, plan_step_for_argument_generation
from agent_v2.runtime.phase1_tool_exposure import PLAN_STEP_TO_LEGACY_REACT_ACTION
from agent_v2.runtime.replanner import Replanner, merge_preserved_completed_steps
from agent_v2.runtime.session_memory import SessionMemory
from agent_v2.runtime.tool_mapper import coerce_to_tool_result, map_tool_result_to_execution_result
from agent_v2.runtime.tool_policy import plan_safe_shell_command_allowed
from agent_v2.runtime.trace_emitter import TraceEmitter, TraceEmitterFactory
from agent_v2.schemas.execution import (
    ErrorType,
    ExecutionError,
    ExecutionMetadata,
    ExecutionOutput,
    ExecutionResult,
)
from agent_v2.schemas.execution_task import CompiledExecutionGraph, ExecutionTask, TaskScheduler
from agent_v2.schemas.plan import PlanDocument, PlanStep
from agent_v2.schemas.policies import ExecutionPolicy
from agent_v2.validation.plan_validator import PlanValidator

_LOG = logging.getLogger(__name__)
_DEFAULT_POLICY = ExecutionPolicy(max_steps=8, max_retries_per_step=2, max_replans=2)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _metadata_dict(state: Any) -> dict:
    md = getattr(state, "metadata", None)
    return md if isinstance(md, dict) else {}


def _planner_session_memory_from_state(state: Any) -> SessionMemory:
    ctx = getattr(state, "context", None)
    if not isinstance(ctx, dict):
        try:
            state.context = {}
            ctx = state.context
        except Exception:
            return SessionMemory()
    key = "planner_session_memory"
    existing = ctx.get(key)
    if isinstance(existing, SessionMemory):
        return existing
    mem = SessionMemory()
    ctx[key] = mem
    return mem


def _plan_safe_execution_active(state: Any) -> bool:
    ctx = getattr(state, "context", None)
    if isinstance(ctx, dict) and ctx.get("plan_safe_execute"):
        return True
    md = getattr(state, "metadata", None)
    if isinstance(md, dict) and md.get("mode") == "plan":
        return True
    return False


class PlanArgumentGeneratorProtocol(Protocol):
    def generate(self, step: PlanStep, state: Any) -> dict:
        ...


def _merge_args_hints(task: ExecutionTask, generated: dict) -> dict:
    base: dict = {}
    h = task.input_hints
    for key in ("path", "query", "instruction", "command", "file"):
        val = h.get(key)
        if val is not None and str(val).strip():
            base[key] = val
    out = {**base}
    if isinstance(generated, dict):
        out.update(generated)
    return out


def _to_dispatch_step(task: ExecutionTask, args: dict) -> dict:
    pa = task.tool
    legacy = PLAN_STEP_TO_LEGACY_REACT_ACTION.get(pa)
    if legacy is None and pa != "finish":
        if pa == "shell":
            raise ValueError("shell uses _dispatch_shell, not ReAct dispatch")
        raise ValueError(f"Unsupported plan action for ReAct dispatch: {pa!r}")

    if pa == "finish":
        return {
            "id": task.plan_step_index,
            "step_id": task.id,
            "action": "FINISH",
            "_react_action_raw": "finish",
            "_react_args": {},
        }

    row: dict[str, Any] = {
        "id": task.plan_step_index,
        "step_id": task.id,
        "action": legacy,
        "artifact_mode": "code",
        "_react_thought": "",
        "_react_action_raw": pa,
        "_react_args": args,
    }
    if pa == "search":
        row["query"] = args.get("query", "")
        row["description"] = row["query"]
    elif pa == "open_file":
        row["path"] = args.get("path", "")
        row["description"] = row["path"]
    elif pa == "edit":
        row["path"] = args.get("path", "")
        row["edit_target_path"] = args.get("path", "")
        row["description"] = args.get("instruction", "")
    elif pa == "run_tests":
        row["description"] = ""
    return row


def _ensure_execution_result_contract(result: ExecutionResult, step_id: str) -> None:
    if not isinstance(result, ExecutionResult):
        raise TypeError(f"Expected ExecutionResult for step {step_id}, got {type(result).__name__}")
    if result.output is None or not str(result.output.summary or "").strip():
        raise ValueError(f"ExecutionResult for step {step_id} must include output.summary")


class DagExecutor:
    def __init__(
        self,
        dispatcher: Any,
        argument_generator: PlanArgumentGeneratorProtocol,
        replanner: Optional[Replanner] = None,
        policy: Optional[ExecutionPolicy] = None,
        trace_emitter_factory: Optional[TraceEmitterFactory] = None,
    ):
        self.dispatcher = dispatcher
        self.argument_generator = argument_generator
        self.replanner = replanner
        self._policy = policy or _DEFAULT_POLICY
        self._trace_emitter_factory: TraceEmitterFactory = trace_emitter_factory or TraceEmitter
        self.trace_emitter: TraceEmitter = self._trace_emitter_factory()

    def _sync_context_graph(self, state: Any, graph: CompiledExecutionGraph) -> None:
        ctx = getattr(state, "context", None)
        if not isinstance(ctx, dict):
            state.context = {}
            ctx = state.context
        ctx["dag_graph_tasks"] = {k: v.model_dump(mode="json") for k, v in graph.tasks_by_id.items()}

    def _completed_ids(self, graph: CompiledExecutionGraph) -> set[str]:
        return {t.id for t in graph.tasks_by_id.values() if t.runtime.status == "completed"}

    def _persist_completed_set(self, state: Any, graph: CompiledExecutionGraph) -> None:
        ctx = getattr(state, "context", None)
        if not isinstance(ctx, dict):
            return
        ctx["dag_completed_step_ids"] = list(self._completed_ids(graph))

    def _load_completed_set(self, state: Any) -> set[str]:
        ctx = getattr(state, "context", None)
        if not isinstance(ctx, dict):
            return set()
        raw = ctx.get("dag_completed_step_ids")
        if isinstance(raw, (list, set)):
            return {str(x) for x in raw}
        return set()

    def _init_or_restore_graph(self, plan: PlanDocument, state: Any) -> CompiledExecutionGraph:
        ctx = getattr(state, "context", None)
        if not isinstance(ctx, dict):
            state.context = {}
            ctx = state.context

        fp = plan.plan_id
        stored = ctx.get("dag_active_plan_id")
        if stored == fp and ctx.get("dag_graph_tasks"):
            raw_tasks = ctx["dag_graph_tasks"]
            if isinstance(raw_tasks, dict):
                try:
                    tasks = {k: ExecutionTask.model_validate(v) for k, v in raw_tasks.items()}
                    graph = CompiledExecutionGraph(plan_id=plan.plan_id, tasks_by_id=tasks)
                    for tid in self._load_completed_set(state):
                        if tid in graph.tasks_by_id:
                            t = graph.tasks_by_id[tid]
                            if t.runtime.status == "pending":
                                graph.tasks_by_id[tid] = t.model_copy(
                                    update={
                                        "runtime": t.runtime.model_copy(update={"status": "completed"})
                                    }
                                )
                    return graph
                except Exception:
                    pass

        graph = compile_plan_document(plan, policy=self._policy)
        done = self._load_completed_set(state)
        for tid in done:
            if tid in graph.tasks_by_id:
                t = graph.tasks_by_id[tid]
                graph.tasks_by_id[tid] = t.model_copy(
                    update={"runtime": t.runtime.model_copy(update={"status": "completed"})}
                )
        ctx["dag_active_plan_id"] = fp
        self._sync_context_graph(state, graph)
        return graph

    def finalize_run(self, state: Any, plan_for_id: PlanDocument, run_status: str) -> dict[str, Any]:
        ctx = getattr(state, "context", None)
        plan = plan_for_id
        if isinstance(ctx, dict):
            ap = ctx.get("active_plan_document")
            if isinstance(ap, PlanDocument):
                plan = ap
        trace = self.trace_emitter.build_trace(state.instruction, plan.plan_id)
        md = _metadata_dict(state)
        md["execution_trace_id"] = trace.trace_id
        md["plan_executor_run_status"] = run_status
        return {"status": run_status, "trace": trace, "state": state}

    def run(
        self,
        plan: PlanDocument,
        state: Any,
        *,
        trace_emitter: TraceEmitter | None = None,
    ) -> dict[str, Any]:
        if not isinstance(plan, PlanDocument):
            raise TypeError(f"DagExecutor.run expected PlanDocument, got {type(plan).__name__}")
        if getattr(state, "current_plan", None) is None:
            raise ValueError("DagExecutor.run requires state.current_plan before execution.")

        self.trace_emitter = trace_emitter if trace_emitter is not None else self._trace_emitter_factory()
        md0 = _metadata_dict(state)
        md0["executor_dispatch_count"] = 0
        md0.pop("plan_executor_abort", None)

        ctx = getattr(state, "context", None)
        if isinstance(ctx, dict):
            ctx["dag_completed_step_ids"] = []
            ctx.pop("dag_graph_tasks", None)
            ctx.pop("dag_active_plan_id", None)

        if isinstance(ctx, dict):
            ctx["active_plan_document"] = plan

        work_plan = plan
        while True:
            if isinstance(state.context, dict):
                ap = state.context.get("active_plan_document")
                if isinstance(ap, PlanDocument):
                    work_plan = ap
            outcome = self._run_graph_to_event(work_plan, state)
            if outcome[0] == "completed":
                return self.finalize_run(state, plan, "success")
            if outcome[0] in ("deadlock", "aborted"):
                return self.finalize_run(state, plan, "failed")
            assert outcome[0] == "failed"
            _, failed_task, result = outcome
            if self.replanner is None:
                return self.finalize_run(state, plan, "failed")
            md = _metadata_dict(state)
            if int(md.get("replan_attempt", 0)) >= int(self._max_replans(state)):
                md["plan_executor_status"] = "failed_final"
                return self.finalize_run(state, plan, "failed")

            pseudo_step = plan_step_for_argument_generation(failed_task)
            req = self.replanner.build_replan_request(state, work_plan, pseudo_step, result)
            lf = md.get("langfuse_trace")
            session = _planner_session_memory_from_state(state)
            vtm_raw = md.get("plan_validation_task_mode")
            vtm = str(vtm_raw).strip() if isinstance(vtm_raw, str) and str(vtm_raw).strip() else None
            replan_res, new_plan = self.replanner.replan(
                req,
                langfuse_trace=lf,
                obs=md.get("obs"),
                session=session,
                validation_task_mode=vtm,
            )
            if replan_res.status != "success" or new_plan is None:
                md["plan_executor_status"] = "failed_final"
                return self.finalize_run(state, plan, "failed")
            md["replan_attempt"] = replan_res.metadata.replan_attempt
            completed_ids = set(self._load_completed_set(state))
            if req.constraints.preserve_completed:
                new_plan = merge_preserved_completed_steps(
                    work_plan, new_plan, completed_step_ids=completed_ids
                )
            PlanValidator.validate_plan(new_plan, policy=self._policy, task_mode=vtm)
            if isinstance(state.context, dict):
                state.context["active_plan_document"] = new_plan
                state.context.pop("dag_graph_tasks", None)
                state.context.pop("dag_active_plan_id", None)
            work_plan = new_plan

    def _max_replans(self, state: Any) -> int:
        pol = getattr(state, "execution_policy", None)
        if pol is not None and getattr(pol, "max_replans", None) is not None:
            return int(pol.max_replans)
        return int(self._policy.max_replans)

    def _failure_or_deadlock_when_starved(
        self, graph: CompiledExecutionGraph
    ) -> tuple[Literal["deadlock"]] | tuple[Literal["failed"], ExecutionTask, ExecutionResult]:
        """No ready pending tasks but graph incomplete: failed upstream task vs cyclic/missing deps."""
        failed = [t for t in graph.tasks_by_id.values() if t.runtime.status == "failed"]
        if failed:
            failed.sort(key=lambda t: t.plan_step_index)
            ft = failed[0]
            lr = ft.runtime.last_result
            if lr is None:
                lr = ExecutionResult(
                    step_id=ft.id,
                    success=False,
                    status="failure",
                    output=ExecutionOutput(summary="task failed (missing last_result)", data={}),
                    error=ExecutionError(
                        type=ErrorType.unknown,
                        message="dag_executor: failed task has no last_result",
                    ),
                    metadata=ExecutionMetadata(
                        tool_name="dag_executor",
                        duration_ms=0,
                        timestamp=_utc_now(),
                    ),
                )
            return ("failed", ft, lr)
        return ("deadlock",)

    def _run_graph_to_event(
        self, plan: PlanDocument, state: Any
    ) -> tuple[str] | tuple[str, ExecutionTask, ExecutionResult]:
        graph = self._init_or_restore_graph(plan, state)
        max_rounds = len(graph.tasks_by_id) + 5
        for _ in range(max_rounds):
            completed = self._completed_ids(graph)
            if len(completed) == len(graph.tasks_by_id):
                return ("completed",)
            ready = TaskScheduler.ready_tasks(graph, completed)
            if not ready:
                out = self._failure_or_deadlock_when_starved(graph)
                if out[0] == "deadlock":
                    return ("deadlock",)
                return out
            task = ready[0]
            result = self._execute_task_with_retries(graph, task, plan, state)
            self._sync_context_graph(state, graph)
            self._persist_completed_set(state, graph)
            if not result.success:
                return ("failed", task, result)
        return ("deadlock",)

    def run_one_step(
        self,
        plan: PlanDocument,
        state: Any,
        *,
        trace_emitter: TraceEmitter | None = None,
    ) -> dict[str, Any]:
        if not isinstance(plan, PlanDocument):
            raise TypeError(f"DagExecutor.run_one_step expected PlanDocument, got {type(plan).__name__}")
        if getattr(state, "current_plan", None) is None:
            raise ValueError("DagExecutor.run_one_step requires state.current_plan before execution.")

        self.trace_emitter = trace_emitter if trace_emitter is not None else self._trace_emitter_factory()
        if isinstance(state.context, dict):
            state.context["active_plan_document"] = plan

        graph = self._init_or_restore_graph(plan, state)
        completed = self._completed_ids(graph)
        if len(completed) == len(graph.tasks_by_id):
            fin = next(iter(graph.tasks_by_id.values()))
            for t in graph.ordered_tasks():
                if t.tool == "finish":
                    fin = t
                    break
            return self.finalize_run(state, plan, "success")

        ready = TaskScheduler.ready_tasks(graph, completed)
        if not ready:
            out = self._failure_or_deadlock_when_starved(graph)
            if out[0] == "failed":
                _, ft, res = out
                md = _metadata_dict(state)
                md["plan_executor_status"] = "failed_step"
                md["last_failed_step_id"] = ft.id
                pseudo = plan_step_for_argument_generation(ft)
                return {
                    "status": "failed_step",
                    "failed_step": pseudo,
                    "failed_task": ft,
                    "result": res,
                    "state": state,
                }
            return self.finalize_run(state, plan, "failed")

        task = ready[0]
        result = self._execute_task_with_retries(graph, task, plan, state)
        self._sync_context_graph(state, graph)
        self._persist_completed_set(state, graph)

        if not result.success:
            md = _metadata_dict(state)
            md["plan_executor_status"] = "failed_step"
            md["last_failed_step_id"] = task.id
            pseudo = plan_step_for_argument_generation(task)
            return {
                "status": "failed_step",
                "failed_step": pseudo,
                "failed_task": task,
                "result": result,
                "state": state,
            }

        completed2 = self._completed_ids(graph)
        if len(completed2) == len(graph.tasks_by_id):
            return self.finalize_run(state, plan, "success")
        return {"status": "progress", "state": state}

    def _guard_limits(self, state: Any) -> str | None:
        md = _metadata_dict(state)
        cap = int(self._policy.max_executor_dispatches)
        n = int(md.get("executor_dispatch_count", 0))
        if n >= cap:
            return f"max_executor_dispatches ({cap}) reached"
        return None

    def _snapshot_arguments(self, task: ExecutionTask, state: Any) -> ExecutionTask:
        if task.arguments:
            return task
        if task.tool == "finish":
            return task.model_copy(update={"arguments": {}})
        ps = plan_step_for_argument_generation(task)
        gen = self.argument_generator.generate(ps, state)
        merged = _merge_args_hints(task, gen)
        return task.model_copy(update={"arguments": merged})

    def _plan_safe_guard(
        self, state: Any, task: ExecutionTask, merged: dict[str, Any]
    ) -> ExecutionResult | None:
        if not _plan_safe_execution_active(state):
            return None
        if task.tool == "edit":
            return ExecutionResult(
                step_id=task.id,
                success=False,
                status="failure",
                output=ExecutionOutput(
                    summary="plan_safe guard: edit is not allowed in this runtime mode",
                    data={},
                ),
                error=ExecutionError(
                    type=ErrorType.unknown,
                    message="plan_safe_guard: edit blocked at executor",
                ),
                metadata=ExecutionMetadata(
                    tool_name="plan_executor",
                    duration_ms=0,
                    timestamp=_utc_now(),
                ),
            )
        if task.tool == "shell":
            cmd = str(merged.get("command") or "").strip()
            if cmd and not plan_safe_shell_command_allowed(cmd):
                return ExecutionResult(
                    step_id=task.id,
                    success=False,
                    status="failure",
                    output=ExecutionOutput(
                        summary="plan_safe guard: shell command violates plan-mode policy",
                        data={},
                    ),
                    error=ExecutionError(
                        type=ErrorType.unknown,
                        message="plan_safe_guard: shell blocked at executor",
                    ),
                    metadata=ExecutionMetadata(
                        tool_name="plan_executor",
                        duration_ms=0,
                        timestamp=_utc_now(),
                    ),
                )
        return None

    def _dispatch_shell(self, task: ExecutionTask, args: dict, state: Any) -> ExecutionResult:
        cmd = str(args.get("command") or "").strip()
        if not cmd:
            tr = coerce_to_tool_result(
                {"success": False, "output": {}, "error": "shell requires non-empty command"},
                tool_name="shell",
            )
            return map_tool_result_to_execution_result(tr, step_id=task.id)

        shell = None
        if getattr(state, "context", None) is not None:
            shell = state.context.get("shell")
        if shell is None:
            from agent_v2.primitives.shell import Shell  # noqa: PLC0415

            shell = Shell()
            if getattr(state, "context", None) is not None:
                state.context["shell"] = shell

        raw = shell.run(cmd)
        tool_result = coerce_to_tool_result(raw, tool_name="shell")
        return map_tool_result_to_execution_result(tool_result, step_id=task.id)

    def _dispatch_once(self, task: ExecutionTask, state: Any) -> ExecutionResult:
        md = _metadata_dict(state)
        md["executor_dispatch_count"] = int(md.get("executor_dispatch_count", 0)) + 1
        merged = dict(task.arguments)
        guard = self._plan_safe_guard(state, task, merged)
        if guard is not None:
            _ensure_execution_result_contract(guard, task.id)
            return guard
        if task.tool == "shell":
            res = self._dispatch_shell(task, merged, state)
            _ensure_execution_result_contract(res, task.id)
            return res
        dispatch_dict = _to_dispatch_step(task, merged)
        res = self.dispatcher.execute(dispatch_dict, state)
        if isinstance(res, list):
            raise RuntimeError("DAG executor does not support list ExecutionResult from dispatcher here")
        _ensure_execution_result_contract(res, task.id)
        return res

    def _execute_task_with_retries(
        self,
        graph: CompiledExecutionGraph,
        task: ExecutionTask,
        plan: PlanDocument,
        state: Any,
    ) -> ExecutionResult:
        task = self._snapshot_arguments(task, state)
        graph.tasks_by_id[task.id] = task

        max_attempts = max(1, int(task.runtime.max_attempts))
        result: ExecutionResult | None = None
        now = _utc_now()
        task = task.model_copy(
            update={"runtime": task.runtime.model_copy(update={"status": "running", "started_at": now})}
        )
        graph.tasks_by_id[task.id] = task

        while task.runtime.attempts < max_attempts:
            abort = self._guard_limits(state)
            if abort is not None:
                _metadata_dict(state)["plan_executor_abort"] = abort
                result = ExecutionResult(
                    step_id=task.id,
                    success=False,
                    status="failure",
                    output=ExecutionOutput(summary=abort, data={}),
                    error=ExecutionError(type=ErrorType.unknown, message=abort),
                    metadata=ExecutionMetadata(
                        tool_name="dag_executor",
                        duration_ms=0,
                        timestamp=_utc_now(),
                    ),
                )
                rt = task.runtime.model_copy(
                    update={
                        "status": "failed",
                        "completed_at": _utc_now(),
                        "last_result": result,
                    }
                )
                graph.tasks_by_id[task.id] = task.model_copy(update={"runtime": rt})
                return result

            if task.tool == "finish":
                result = ExecutionResult(
                    step_id=task.id,
                    success=True,
                    status="success",
                    output=ExecutionOutput(summary="Finished per plan.", data={}),
                    error=None,
                    metadata=ExecutionMetadata(tool_name="finish", duration_ms=0, timestamp=_utc_now()),
                )
                rt = task.runtime.model_copy(
                    update={
                        "attempts": task.runtime.attempts + 1,
                        "status": "completed",
                        "completed_at": _utc_now(),
                        "last_result": result,
                    }
                )
                graph.tasks_by_id[task.id] = task.model_copy(update={"runtime": rt})
                ps = plan_step_for_argument_generation(graph.tasks_by_id[task.id])
                att = graph.tasks_by_id[task.id].runtime.attempts
                self.trace_emitter.record_step(
                    ps, result, task.plan_step_index, execution_attempts=att
                )
                self._update_state_history(state, task, str(result.output.summary or ""))
                return result

            result = self._dispatch_once(graph.tasks_by_id[task.id], state)
            task = graph.tasks_by_id[task.id]
            rt = task.runtime.model_copy(update={"attempts": task.runtime.attempts + 1})
            task = task.model_copy(update={"runtime": rt})
            graph.tasks_by_id[task.id] = task

            if result.success:
                rt2 = task.runtime.model_copy(
                    update={
                        "status": "completed",
                        "completed_at": _utc_now(),
                        "last_result": result,
                    }
                )
                task = task.model_copy(update={"runtime": rt2})
                graph.tasks_by_id[task.id] = task
                ps = plan_step_for_argument_generation(task)
                att = graph.tasks_by_id[task.id].runtime.attempts
                self.trace_emitter.record_step(
                    ps, result, task.plan_step_index, execution_attempts=att
                )
                self._update_state_history(state, task, str(result.output.summary or ""))
                return result

        assert result is not None
        rt = task.runtime.model_copy(
            update={"status": "failed", "completed_at": _utc_now(), "last_result": result}
        )
        graph.tasks_by_id[task.id] = task.model_copy(update={"runtime": rt})
        ps = plan_step_for_argument_generation(graph.tasks_by_id[task.id])
        att = graph.tasks_by_id[task.id].runtime.attempts
        self.trace_emitter.record_step(ps, result, task.plan_step_index, execution_attempts=att)
        return result

    @staticmethod
    def _update_state_history(state: Any, task: ExecutionTask, summary: str) -> None:
        if not hasattr(state, "history"):
            return
        state.history.append(
            {
                "step_id": task.id,
                "plan_action": task.tool,
                "action": task.tool,
                "observation": summary,
            }
        )
        if hasattr(state, "step_results"):
            state.step_results.append(
                {
                    "step_id": task.id,
                    "action": task.tool,
                    "result_summary": summary,
                }
            )
        if hasattr(state, "history") and hasattr(state, "step_results"):
            if len(state.history) != len(state.step_results):
                raise RuntimeError("AgentState invariant violated: len(history) != len(step_results)")
