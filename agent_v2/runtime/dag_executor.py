"""
DAG execution: compile_plan → in-memory ExecutionTask dict → dependency-only schedule → dispatch.

No PlanStep in this module. No state.context["dag_graph_tasks"].

DagExecutor owns execution semantics and retry logic:

Responsibilities:
- Argument snapshotting and freezing (deep immutability)
- Attempt counter management (single source of truth)
- Context-aware retry decisions (_should_retry)
- Execution termination conditions
- Consistency validation (not replay)

Scheduler is responsible for:
- Dependency-based ordering
- Lifecycle state transitions
- Calling executor for execution

No duplication: executor owns all execution semantics.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Literal, Optional, Protocol

from agent_v2.runtime.dag_scheduler import DagScheduler, SchedulerResult
from agent_v2.runtime.plan_compiler import compile_plan, tasks_by_id
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
from agent_v2.schemas.execution_task import ExecutionTask, TaskScheduler
from agent_v2.schemas.plan import PlanDocument
from agent_v2.schemas.policies import ExecutionPolicy
from agent_v2.validation.plan_validator import PlanValidator
from agent.models.model_config import TASK_MODELS

_LOG = logging.getLogger(__name__)
_DEFAULT_POLICY = ExecutionPolicy(max_steps=8, max_retries_per_step=2, max_replans=2)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _metadata_dict(state: Any) -> dict:
    md = getattr(state, "metadata", None)
    if not isinstance(md, dict):
        state.metadata = {}
        return state.metadata
    return md


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
    def generate(self, task: ExecutionTask, state: Any) -> dict:
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


def _dispatch_numeric_id(task_id: str) -> int:
    h = hash(task_id)
    return abs(h) % (2**31 - 1) or 1


def _to_dispatch_step(task: ExecutionTask, args: dict) -> dict:
    pa = task.tool
    legacy = PLAN_STEP_TO_LEGACY_REACT_ACTION.get(pa)
    if legacy is None and pa != "finish":
        if pa == "shell":
            raise ValueError("shell uses _dispatch_shell, not ReAct dispatch")
        raise ValueError(f"Unsupported plan action for ReAct dispatch: {pa!r}")

    nid = _dispatch_numeric_id(task.id)
    if pa == "finish":
        return {
            "id": nid,
            "step_id": task.id,
            "action": "FINISH",
            "_react_action_raw": "finish",
            "_react_args": {},
        }

    row: dict[str, Any] = {
        "id": nid,
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
        trace_log_dir: str | None = None,
    ):
        self.dispatcher = dispatcher
        self.argument_generator = argument_generator
        self.replanner = replanner
        self._policy = policy or _DEFAULT_POLICY
        self._trace_emitter_factory: TraceEmitterFactory = trace_emitter_factory or TraceEmitter
        self.trace_emitter: TraceEmitter = self._trace_emitter_factory(log_dir=trace_log_dir)
        self._tasks_by_id: dict[str, ExecutionTask] = {}
        self._active_plan_id: str | None = None
        self._persistent_completed_ids: set[str] = set()

        # Create explicit scheduler
        self._scheduler = DagScheduler(dispatcher, argument_generator, policy)

    def get_tasks_by_id(self) -> dict[str, ExecutionTask]:
        return dict(self._tasks_by_id)

    def get_completed_step_ids(self) -> set[str]:
        return set(self._persistent_completed_ids)

    def _completed_ids(self) -> set[str]:
        return {t.id for t in self._tasks_by_id.values() if t.status == "completed"}

    def _publish_progress_metadata(self, state: Any, plan: PlanDocument) -> None:
        md = _metadata_dict(state)
        md["executor_dag_plan_id"] = plan.plan_id
        md["executor_dag_total"] = len(self._tasks_by_id)
        done = self._completed_ids()
        md["executor_dag_completed"] = len(done)
        md["executor_dag_completed_ids"] = sorted(done)

    def _clear_executor_context_keys(self, state: Any) -> None:
        ctx = getattr(state, "context", None)
        if isinstance(ctx, dict):
            ctx.pop("dag_graph_tasks", None)
            ctx.pop("dag_completed_step_ids", None)
            ctx.pop("dag_active_plan_id", None)
        md = _metadata_dict(state)
        for k in (
            "executor_dag_plan_id",
            "executor_dag_total",
            "executor_dag_completed",
            "executor_dag_completed_ids",
        ):
            md.pop(k, None)

    def _ensure_tasks(self, plan: PlanDocument) -> None:
        if self._active_plan_id == plan.plan_id and self._tasks_by_id:
            return
        compiled = compile_plan(plan, policy=self._policy)
        self._tasks_by_id = tasks_by_id(compiled)
        self._active_plan_id = plan.plan_id
        for cid in self._persistent_completed_ids:
            if cid in self._tasks_by_id:
                t = self._tasks_by_id[cid]
                self._tasks_by_id[cid] = t.model_copy(update={"status": "completed"})

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

        self._tasks_by_id.clear()
        self._persistent_completed_ids.clear()
        self._active_plan_id = None
        self._clear_executor_context_keys(state)

        self.trace_emitter = trace_emitter if trace_emitter is not None else self._trace_emitter_factory()
        md0 = _metadata_dict(state)
        md0["executor_dispatch_count"] = 0
        md0.pop("plan_executor_abort", None)

        ctx = getattr(state, "context", None)
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

            req = self.replanner.build_replan_request(
                state,
                work_plan,
                failed_task,
                result,
                tasks_by_id=self._tasks_by_id,
            )
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
            completed_ids = set(self._persistent_completed_ids)
            if req.constraints.preserve_completed:
                new_plan = merge_preserved_completed_steps(
                    work_plan, new_plan, completed_step_ids=completed_ids
                )
            PlanValidator.validate_plan(new_plan, policy=self._policy, task_mode=vtm)
            if isinstance(state.context, dict):
                state.context["active_plan_document"] = new_plan
            self._tasks_by_id.clear()
            self._active_plan_id = None
            work_plan = new_plan

    def _max_replans(self, state: Any) -> int:
        pol = getattr(state, "execution_policy", None)
        if pol is not None and getattr(pol, "max_replans", None) is not None:
            return int(pol.max_replans)
        return int(self._policy.max_replans)

    def _failure_or_deadlock_when_starved(
        self,
    ) -> tuple[Literal["deadlock"]] | tuple[Literal["failed"], ExecutionTask, ExecutionResult]:
        failed = [t for t in self._tasks_by_id.values() if t.status == "failed"]
        if failed:
            failed.sort(key=lambda t: t.id)
            ft = failed[0]
            lr = ft.last_result
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
        """Use explicit scheduler instead of implicit loop."""
        self._ensure_tasks(plan)
        self._publish_progress_metadata(state, plan)

        # Snapshot arguments before execution
        tasks = self._snapshot_all_arguments(state)

        # Run scheduler
        result = self._scheduler.run_scheduler(tasks, state)

        # Update internal state from scheduler result
        self._tasks_by_id = self._scheduler._tasks_by_id
        self._completed_ids = self._scheduler._completed_ids
        self._persistent_completed_ids.update(self._completed_ids)
        self._publish_progress_metadata(state, plan)

        # Record traces for all completed tasks
        for task in self._tasks_by_id.values():
            if task.status == "completed" and task.last_result:
                self.trace_emitter.record_execution_task(
                    task, task.last_result, execution_attempts=task.attempts
                )
                self._update_state_history(state, task, str(task.last_result.output.summary or ""))
            elif task.status == "failed" and task.last_result:
                self.trace_emitter.record_execution_task(
                    task, task.last_result, execution_attempts=task.attempts
                )

        # Map scheduler result to event tuple
        if result.status == "success":
            return ("completed",)
        elif result.status == "failed" and result.failed_task:
            return ("failed", result.failed_task, result.last_result)
        else:
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

        self._ensure_tasks(plan)
        self._publish_progress_metadata(state, plan)
        completed = self._completed_ids()
        if len(completed) == len(self._tasks_by_id):
            return self.finalize_run(state, plan, "success")

        ready = TaskScheduler.ready_tasks(self._tasks_by_id, completed)
        if not ready:
            out = self._failure_or_deadlock_when_starved()
            if out[0] == "failed":
                _, ft, res = out
                md = _metadata_dict(state)
                md["plan_executor_status"] = "failed_step"
                md["last_failed_step_id"] = ft.id
                return {
                    "status": "failed_step",
                    "failed_task": ft,
                    "result": res,
                    "state": state,
                }
            return self.finalize_run(state, plan, "failed")

        # For single-step execution, use the legacy retry logic with tracing
        task = self._snapshot_arguments(ready[0], state)
        result = self._execute_task_with_retries(task, state)
        self._publish_progress_metadata(state, plan)

        if not result.success:
            md = _metadata_dict(state)
            md["plan_executor_status"] = "failed_step"
            md["last_failed_step_id"] = task.id
            ft = self._tasks_by_id[task.id]
            return {
                "status": "failed_step",
                "failed_task": ft,
                "result": result,
                "state": state,
            }

        completed2 = self._completed_ids()
        if len(completed2) == len(self._tasks_by_id):
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
        # Check if arguments are already frozen (generated and finalized)
        if task.arguments_frozen:
            # Arguments already frozen - return as-is
            return task
        
        if task.tool == "finish":
            return task.model_copy(update={"arguments_frozen": True})
        
        # Generate arguments
        gen = self.argument_generator.generate(task, state)
        merged = _merge_args_hints(task, gen)
        
        # DEEP FREEZE: prevent nested mutation via JSON serialization
        try:
            frozen_args = json.loads(json.dumps(merged))
        except (TypeError, ValueError) as e:
            # Fallback to shallow copy if JSON serialization fails (e.g., non-serializable types)
            logging.warning(f"Task {task.id}: deep freeze failed, using shallow copy: {e}")
            frozen_args = dict(merged)
        
        return task.model_copy(update={"arguments": frozen_args, "arguments_frozen": True})

    def _snapshot_all_arguments(self, state: Any) -> list[ExecutionTask]:
        """Generate arguments for all tasks before execution."""
        tasks = list(self._tasks_by_id.values())
        with_args = []
        for task in tasks:
            if not task.arguments_frozen:
                task = self._snapshot_arguments(task, state)
            with_args.append(task)
        return with_args

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

        # Validate arguments frozen before execution
        if not task.arguments_frozen and task.tool != "finish":
            logging.warning(f"Task {task.id}: executing with unfrozen arguments")

        # Resolve model_key for this task
        # Priority: 1) task.model_key, 2) TASK_MODELS lookup by task_name, 3) default to REASONING
        if task.model_key:
            resolved_model_key = task.model_key
        elif task.task_name and task.task_name in TASK_MODELS:
            resolved_model_key = TASK_MODELS.get(task.task_name, "REASONING")
        else:
            resolved_model_key = "REASONING"

        # Immutable deep copy of arguments for this execution attempt
        start_time = time.time()

        try:
            merged = json.loads(json.dumps(task.arguments))
        except (TypeError, ValueError) as e:
            logging.warning(f"Task {task.id}: deep copy failed, using shallow copy: {e}")
            merged = dict(task.arguments)

        guard = self._plan_safe_guard(state, task, merged)
        if guard is not None:
            duration_ms = int((time.time() - start_time) * 1000)
            _ensure_execution_result_contract(guard, task.id)
            # Record execution attempt for debugging
            self.trace_emitter.record_execution_attempt(task, guard, task.attempts, duration_ms)
            return guard

        if task.tool == "shell":
            res = self._dispatch_shell(task, merged, state)
            duration_ms = int((time.time() - start_time) * 1000)
            _ensure_execution_result_contract(res, task.id)
            # Record execution attempt for debugging
            self.trace_emitter.record_execution_attempt(task, res, task.attempts, duration_ms)
            return res

        # Inject model_key into dispatch_dict
        dispatch_dict = _to_dispatch_step(task, merged)
        dispatch_dict["model_key"] = resolved_model_key
        res = self.dispatcher.execute(dispatch_dict, state)
        duration_ms = int((time.time() - start_time) * 1000)

        if isinstance(res, list):
            raise RuntimeError("DAG executor does not support list ExecutionResult from dispatcher here")

        _ensure_execution_result_contract(res, task.id)

        # Record execution attempt for debugging
        self.trace_emitter.record_execution_attempt(task, res, task.attempts, duration_ms)

        return res

    def _should_retry(self, result: ExecutionResult, task: ExecutionTask, state: Any) -> bool:
        """
        Context-aware retry decision.

        Factors:
        - Error type (base classification)
        - Tool type (tool-specific policies)
        - State context (resource constraints, partial success)
        - Attempt count (terminal condition)

        Returns True if retry is recommended, False otherwise.
        """
        # Terminal condition: exceeded max attempts
        if task.attempts >= task.max_attempts:
            return False

        # Already succeeded - no retry needed
        if result.success:
            return False

        # Error missing - conservative: retry
        if result.error is None:
            logging.debug(f"Task {task.id}: no error info, retry (attempt {task.attempts})")
            return True

        error_type = result.error.type
        tool = task.tool

        # Tool-specific retry policies
        tool_specific_policies = {
            # Read tools: retry I/O errors, fail on validation
            "read": lambda t, s: {
                ErrorType.tool_error: True,  # I/O errors retryable
                ErrorType.timeout: True,
                ErrorType.permission_error: False,  # Won't fix on retry
                ErrorType.not_found: False,  # Won't fix on retry
                ErrorType.validation_error: False,
                ErrorType.tests_failed: False,  # Not applicable
            }.get(t, True),
            # Write/edit tools: retry timeouts, fail on validation
            "edit": lambda t, s: {
                ErrorType.tool_error: True,
                ErrorType.timeout: True,
                ErrorType.permission_error: False,
                ErrorType.not_found: True,  # May be transient
                ErrorType.validation_error: False,  # Fix requires argument change
                ErrorType.tests_failed: False,
            }.get(t, True),
            # Shell: retry everything but specific failures
            "shell": lambda t, s: {
                ErrorType.tool_error: True,
                ErrorType.timeout: True,
                ErrorType.permission_error: True,  # May fix on retry (e.g., resource lock)
                ErrorType.not_found: False,  # Command won't appear
                ErrorType.validation_error: False,  # Command malformed
                ErrorType.tests_failed: False,
            }.get(t, True),
            # Search: retry I/O and timeouts
            "search": lambda t, s: {
                ErrorType.tool_error: True,
                ErrorType.timeout: True,
                ErrorType.permission_error: False,
                ErrorType.not_found: False,
                ErrorType.validation_error: False,
                ErrorType.tests_failed: False,
            }.get(t, True),
        }

        # Get tool-specific policy if available
        if tool in tool_specific_policies:
            should_retry_tool = tool_specific_policies[tool](error_type, state)
            logging.debug(f"Task {task.id}: tool '{tool}' error '{error_type}' -> retry={should_retry_tool}")
            return should_retry_tool

        # Default classification (conservative)
        retryable_types = {
            ErrorType.tool_error,
            ErrorType.timeout,
            ErrorType.unknown,
        }

        non_retryable_types = {
            ErrorType.validation_error,
            ErrorType.tests_failed,
        }

        if error_type in retryable_types:
            return True
        elif error_type in non_retryable_types:
            return False
        else:
            # Unknown error type - conservative: retry
            logging.debug(f"Task {task.id}: unknown error type '{error_type}', retry (attempt {task.attempts})")
            return True

    def _validate_consistency(self, task: ExecutionTask, result: ExecutionResult) -> None:
        """
        Validate execution result consistency (NOT actual replay).

        Lightweight validation checks:
        - Arguments were frozen
        - Result structure is complete
        - Error type matches context

        This does NOT re-execute the task. That would be a separate
        debugging mode not implemented here.
        """
        if not task.arguments:
            return

        # Skip validation for inherently non-deterministic tools
        # (no point validating can't change)
        non_deterministic_tools = {"shell", "run_tests", "search"}
        if task.tool in non_deterministic_tools:
            logging.debug(f"Task {task.id}: skip consistency validation for non-deterministic tool {task.tool}")
            return

        # Validate that arguments were frozen
        if not task.arguments_frozen:
            logging.warning(f"Task {task.id}: arguments not frozen, consistency validation incomplete")
            return

        # Validate result structure consistency
        if result.success:
            if not result.output:
                logging.warning(f"Task {task.id}: success but no output in result")
            if not result.output.summary:
                logging.warning(f"Task {task.id}: success but no summary in result")
            if result.error:
                logging.warning(f"Task {task.id}: success but error present in result")
        else:
            if not result.error:
                logging.warning(f"Task {task.id}: failure but no error in result")

        logging.debug(f"Task {task.id}: consistency validation passed (structure only)")

    def _execute_task_with_retries(self, task: ExecutionTask, state: Any) -> ExecutionResult:
        task = self._snapshot_arguments(task, state)
        now = _utc_now()
        task = task.model_copy(update={"status": "running", "started_at": now})
        self._tasks_by_id[task.id] = task

        max_attempts = max(1, int(task.max_attempts))
        result: ExecutionResult | None = None

        while task.attempts < max_attempts:
            # EXECUTOR increments attempts - single source of truth
            task = self._tasks_by_id[task.id]
            task = task.model_copy(update={"attempts": task.attempts + 1})
            self._tasks_by_id[task.id] = task

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
                task = self._tasks_by_id[task.id]
                task = task.model_copy(
                    update={
                        "status": "failed",
                        "completed_at": _utc_now(),
                        "last_result": result,
                    }
                )
                self._tasks_by_id[task.id] = task
                att = task.attempts
                self.trace_emitter.record_execution_task(task, result, execution_attempts=att)
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
                task = self._tasks_by_id[task.id]
                task = task.model_copy(
                    update={
                        "status": "completed",
                        "completed_at": _utc_now(),
                        "last_result": result,
                    }
                )
                self._tasks_by_id[task.id] = task
                self._persistent_completed_ids.add(task.id)
                att = task.attempts
                self.trace_emitter.record_execution_task(task, result, execution_attempts=att)
                self._update_state_history(state, task, str(result.output.summary or ""))
                return result

            # Normal tool dispatch
            result = self._dispatch_once(self._tasks_by_id[task.id], state)
            task = self._tasks_by_id[task.id]
            task = task.model_copy(update={"last_result": result})
            self._tasks_by_id[task.id] = task

            if result.success:
                self._validate_consistency(self._tasks_by_id[task.id], result)

                task = self._tasks_by_id[task.id]
                task = task.model_copy(
                    update={
                        "status": "completed",
                        "completed_at": _utc_now(),
                    }
                )
                self._tasks_by_id[task.id] = task
                self._persistent_completed_ids.add(task.id)
                att = task.attempts
                self.trace_emitter.record_execution_task(task, result, execution_attempts=att)
                self._update_state_history(state, task, str(result.output.summary or ""))
                return result

            # Context-aware retry decision
            if self._should_retry(result, task, state):
                # Log retry and continue loop - executor will increment on next iteration
                continue

            # Not retryable - fail terminal
            break

        assert result is not None
        task = task.model_copy(
            update={"status": "failed", "completed_at": _utc_now(), "last_result": result}
        )
        self._tasks_by_id[task.id] = task
        att = task.attempts
        self.trace_emitter.record_execution_task(task, result, execution_attempts=att)
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
