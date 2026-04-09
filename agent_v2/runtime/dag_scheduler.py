"""
DAG scheduler: explicit state machine, dependency-driven execution, no implicit ordering.

Scheduler controls retry loop: attempt → fail → pending → retry.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Literal

from agent_v2.runtime.plan_compiler import tasks_by_id
from agent_v2.runtime.phase1_tool_exposure import PLAN_STEP_TO_LEGACY_REACT_ACTION
from agent_v2.schemas.execution import ExecutionResult
from agent_v2.schemas.execution_task import (
    ExecutionTask,
    SchedulerResult,
    TaskScheduler,
)
from agent_v2.schemas.policies import ExecutionPolicy


def _dispatch_numeric_id(task_id: str) -> int:
    h = hash(task_id)
    return abs(h) % (2**31 - 1) or 1


_DEFAULT_POLICY = ExecutionPolicy(max_steps=8, max_retries_per_step=2, max_replans=2)


class DagScheduler:
    """
    DAG scheduler owns dependency-based ordering:

    Responsibilities:
    - Task selection based on dependency graph
    - Lifecycle state transitions (pending -> running -> completed/failed)
    - Ready queue management
    - Calling dispatcher for single execution

    Executor is responsible for:
    - All execution semantics (retry, attempts, termination)
    - Context-aware retry decisions
    - Argument freezing and consistency

    No duplication: scheduler delegates retry to executor.
    """

    def __init__(self, dispatcher, argument_generator, policy=None):
        self.dispatcher = dispatcher
        self.argument_generator = argument_generator
        self._policy = policy or _DEFAULT_POLICY
        self._tasks_by_id: dict[str, ExecutionTask] = {}
        self._completed_ids: set[str] = set()

    def run_scheduler(self, tasks: list[ExecutionTask], state: Any) -> SchedulerResult:
        """
        Main scheduler loop:
        1. Find ready tasks (pending with all deps completed)
        2. If none: check if all completed (success) else deadlock/failure
        3. Pick one task (sequential for now)
        4. Execute with state machine transitions
        5. Continue until done
        """
        self._tasks_by_id = tasks_by_id(tasks)
        self._completed_ids = set()

        while True:
            # Check completion
            if len(self._completed_ids) == len(tasks):
                return SchedulerResult(status="success", completed_ids=self._completed_ids)

            # Find ready tasks (pending with all deps completed)
            ready = TaskScheduler.ready_tasks(self._tasks_by_id, self._completed_ids)

            if not ready:
                return self._handle_starvation(tasks)

            # Pick and execute one task
            task = self._execute_one_task(ready[0], state)

            if task.status == "failed":
                return SchedulerResult(status="failed", failed_task=task, last_result=task.last_result)

    def _execute_one_task(self, task: ExecutionTask, state: Any) -> ExecutionTask:
        """
        Execute a single task.

        SCHEDULER LIFECYCLE ONLY:
        - Transition to running
        - Execute single dispatch (stateless)
        - Delegate retry decision to caller (executor)
        """
        # Transition to running
        task = TaskScheduler.transition_to_running(task)
        self._tasks_by_id[task.id] = task

        # Execute single dispatch (stateless - no attempt management here)
        result = self._dispatch_once(task, state)

        # Transition based on result
        if result.success:
            task = TaskScheduler.transition_to_completed(task, result)
            self._tasks_by_id[task.id] = task
            self._completed_ids.add(task.id)
        else:
            # Scheduler delegates retry decision to executor via loop control
            # Simply transition to pending - caller decides if retry is needed
            if task.attempts < task.max_attempts:
                task = TaskScheduler.transition_to_pending_after_failure(task)
                self._tasks_by_id[task.id] = task
                # Callers (run_scheduler loop) will decide if to retry
            else:
                task = TaskScheduler.transition_to_failed(task, result)
                self._tasks_by_id[task.id] = task

        return self._tasks_by_id[task.id]

    def _dispatch_once(self, task: ExecutionTask, state: Any) -> ExecutionResult:
        """Single dispatch (reads ONLY from task.arguments).

        SCHEDULER IS STATELESS: no attempt management here.
        Executor owns all execution semantics including retry logic.
        """
        # Log the attempt number for debugging (set by executor)
        attempt_num = task.attempts
        logging.debug(f"Dispatching task {task.id}, attempt {attempt_num}/{task.max_attempts}")

        start_time = time.time()

        args = dict(task.arguments)
        dispatch_dict = self._to_dispatch_step(task, args)
        result = self.dispatcher.execute(dispatch_dict, state)

        duration_ms = int((time.time() - start_time) * 1000)

        # Record execution attempt for debugging
        if hasattr(state, "context") and "trace_emitter" in state.context:
            state.context["trace_emitter"].record_execution_attempt(
                task, result, attempt_num, duration_ms
            )

        return result

    def _to_dispatch_step(self, task: ExecutionTask, args: dict) -> dict:
        """Convert ExecutionTask to dispatcher step format."""
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

    def _handle_starvation(self, all_tasks: list[ExecutionTask]) -> SchedulerResult:
        """Handle case where no ready tasks but not all completed."""
        failed = [t for t in self._tasks_by_id.values() if t.status == "failed"]

        if failed:
            return SchedulerResult(
                status="failed",
                failed_task=failed[0],
                last_result=failed[0].last_result or None
            )

        # Has pending tasks but none ready = deadlock
        pending = [t for t in self._tasks_by_id.values() if t.status == "pending"]
        if pending:
            return SchedulerResult(status="deadlock")

        # Should not reach here, but for safety
        return SchedulerResult(status="deadlock")