# ---- cursor prompt -----
You are a staff engineer implementing Phase 1: DAG Scheduler.

Do NOT modify planner or compiler.
Work ONLY on execution layer.

---

## GOAL

Replace the current execution loop with a proper scheduler:

* explicit task states
* ready queue
* clean transitions
* no implicit ordering

---

## STEP 1 — DEFINE STATES

Use strict state machine:

pending → ready → running → completed / failed

Rules:

* pending: waiting for dependencies
* ready: all dependencies completed
* running: currently executing
* completed/failed: terminal

---

## STEP 2 — BUILD READY QUEUE

Implement:

get_ready_tasks(tasks):

* return tasks where:

  * status == pending
  * all dependencies.status == completed

NO sorting by id or index for logic (only optional stable ordering for determinism)

---

## STEP 3 — SCHEDULER LOOP

Implement:

run_scheduler(tasks):

Loop:

1. find ready tasks
2. if none:

   * if all completed → exit success
   * else → deadlock/failure
3. pick one task (sequential for now)
4. mark running
5. execute
6. update:

   * attempts += 1
   * status = completed / failed
7. continue

---

## STEP 4 — FAILURE HANDLING (MINIMAL)

* if task fails:

  * if attempts < max_attempts → set back to pending
  * else → mark failed and stop execution

NO replanning yet

---

## STEP 5 — REMOVE ANY IMPLICIT ORDERING

* execution must NOT depend on:

  * task.id ordering
  * previous loop order
  * plan order

Dependencies ONLY

---

## STEP 6 — CLEAN STATE TRANSITIONS

Enforce:

* pending → ready only via scheduler
* ready → running only via scheduler
* no direct jumps

---

## STEP 7 — EXECUTION ISOLATION

Ensure:

* execution reads ONLY from:
  task.arguments

* execution writes ONLY to:
  task.status
  task.attempts
  task.last_result

---

## STEP 8 — OUTPUT

Provide:

1. Scheduler implementation
2. Updated execution loop
3. State transition diagram (text)
4. Removed legacy logic

---

Rules:

* keep it simple
* no parallelism yet
* no replan yet
* no model routing changes

Focus:
👉 correct scheduler, not features


## --- plan ----
Phase 1: DAG Scheduler Implementation

Overview

Replace the current implicit execution loop with a proper DAG scheduler that:





Uses explicit state machine transitions



Maintains a ready queue based only on dependencies (no implicit ordering by task.id)



Handles failures with retry logic (set pending if attempts < max, else failed)



Enforces clean state transitions (pending → running → completed/failed)



Scheduler controls retry loop: attempt → fail → pending → retry



Ensures execution isolation (reads from task.arguments only, writes to status/attempts/last_result)

Key Changes from Original Design:





❌ Remove double retry: only _dispatch_once, scheduler controls pending→running retry loop



❌ Remove explicit "ready" state: stored states are pending, running, completed, failed (ready is derived)



❌ Remove max_rounds: while True loop with deadlock detection only

Implementation Plan

Step 1: Enhance TaskScheduler with State Management

File: /Users/shang/my_work/AutoStudio/agent_v2/schemas/execution_task.py

Remove implicit ordering and add explicit state transition methods:

class TaskScheduler:
    """Dependency-based scheduler with explicit state transitions."""

    @staticmethod
    def ready_tasks(tasks_by_id: dict[str, ExecutionTask], completed_ids: set[str]) -> list[ExecutionTask]:
        """Return pending tasks whose dependencies are all completed. NO sorting for logic."""
        ready: list[ExecutionTask] = []
        for t in tasks_by_id.values():
            if t.status != "pending":
                continue
            if all(d in completed_ids for d in t.dependencies):
                ready.append(t)
        # Stable sort ONLY for determinism in logs (fine for now - single-threaded)
        ready.sort(key=lambda x: x.id)
        return ready

    @staticmethod
    def transition_to_running(task: ExecutionTask) -> ExecutionTask:
        """Pending → running transition."""
        if task.status != "pending":
            raise ValueError(f"Cannot transition {task.id} from {task.status} to running")
        return task.model_copy(update={"status": "running", "started_at": _utc_now()})

    @staticmethod
    def transition_to_completed(task: ExecutionTask, result: ExecutionResult) -> ExecutionTask:
        """Running → completed transition."""
        return task.model_copy(update={
            "status": "completed",
            "completed_at": _utc_now(),
            "last_result": result
        })

    @staticmethod
    def transition_to_pending_after_failure(task: ExecutionTask) -> ExecutionTask:
        """Running → pending retry transition."""
        if task.status != "running":
            raise ValueError(f"Cannot transition {task.id} from {task.status} to pending")
        if task.attempts >= task.max_attempts:
            raise ValueError(f"Task {task.id} has exceeded max attempts")
        return task.model_copy(update={"status": "pending"})

    @staticmethod
    def transition_to_failed(task: ExecutionTask, result: ExecutionResult) -> ExecutionTask:
        """Running → failed terminal transition."""
        return task.model_copy(update={
            "status": "failed",
            "completed_at": _utc_now(),
            "last_result": result
        })

Step 2: Create Explicit DAGScheduler Class

New File: /Users/shang/my_work/AutoStudio/agent_v2/runtime/dag_scheduler.py

Create a proper scheduler class with the main run loop:

class DagScheduler:
    """DAG scheduler with explicit state machine and dependency-driven execution."""

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

Step 3: Implement Task Execution with State Machine

In: /Users/shang/my_work/AutoStudio/agent_v2/runtime/dag_scheduler.py

    def _execute_one_task(self, task: ExecutionTask, state: Any) -> ExecutionTask:
        """Execute one task with explicit state transitions. Scheduler controls retry loop."""
        # Step 1: pending → running
        task = TaskScheduler.transition_to_running(task)
        self._tasks_by_id[task.id] = task

        # Step 2: execute single dispatch (reads from task.arguments only)
        result = self._dispatch_once(task, state)

        # Step 3: transition based on result
        if result.success:
            task = TaskScheduler.transition_to_completed(task, result)
            self._tasks_by_id[task.id] = task
            self._completed_ids.add(task.id)
        else:
            # Handle failure: retry if attempts < max (scheduler controls retry loop)
            if task.attempts < task.max_attempts:
                task = TaskScheduler.transition_to_pending_after_failure(task)
                self._tasks_by_id[task.id] = task
                # Don't add to completed_ids so it can be retried
                # Continue loop: pending → running → retry
            else:
                task = TaskScheduler.transition_to_failed(task, result)
                self._tasks_by_id[task.id] = task

        return self._tasks_by_id[task.id]

    def _dispatch_once(self, task: ExecutionTask, state: Any) -> ExecutionResult:
        """Single dispatch (reads ONLY from task.arguments)."""
        # Increment attempts
        task = self._tasks_by_id[task.id]
        task = task.model_copy(update={"attempts": task.attempts + 1})
        self._tasks_by_id[task.id] = task

        # Execute using task.arguments (execution isolation)
        args = dict(task.arguments)

        # Build dispatch step
        dispatch_dict = self._to_dispatch_step(task, args)

        # Execute via dispatcher
        return self.dispatcher.execute(dispatch_dict, state)

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

Step 4: Update DagExecutor to Use DAGScheduler

File: /Users/shang/my_work/AutoStudio/agent_v2/runtime/dag_executor.py

Replace the implicit _run_graph_to_event loop with explicit scheduler call:

from agent_v2.runtime.dag_scheduler import DagScheduler, SchedulerResult

class DagExecutor:
    def __init__(self, dispatcher, argument_generator, replanner=None, policy=None, trace_emitter_factory=None):
        self.dispatcher = dispatcher
        self.argument_generator = argument_generator
        self.replanner = replanner
        self._policy = policy or _DEFAULT_POLICY
        self._trace_emitter_factory = trace_emitter_factory or TraceEmitter
        self.trace_emitter = self._trace_emitter_factory()
        self._tasks_by_id: dict[str, ExecutionTask] = {}
        self._active_plan_id: str | None = None
        self._persistent_completed_ids: set[str] = set()
        
        # Create explicit scheduler
        self._scheduler = DagScheduler(dispatcher, argument_generator, policy)
    
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
        self._publish_progress_metadata(state, plan)
        
        # Map scheduler result to event tuple
        if result.status == "success":
            return ("completed",)
        elif result.status == "failed" and result.failed_task:
            return ("failed", result.failed_task, result.last_result)
        else:
            return ("deadlock",)
    
    def _snapshot_all_arguments(self, state: Any) -> list[ExecutionTask]:
        """Generate arguments for all tasks before execution."""
        tasks = list(self._tasks_by_id.values())
        with_args = []
        for task in tasks:
            if not task.arguments:
                task = self._snapshot_arguments(task, state)
            with_args.append(task)
        return with_args

Step 5: Add SchedulerResult Schema

File: /Users/shang/my_work/AutoStudio/agent_v2/schemas/execution_task.py

class SchedulerResult(BaseModel):
    """Result of a scheduler run."""
    status: Literal["success", "failed", "deadlock"]
    completed_ids: set[str] = Field(default_factory=set)
    failed_task: ExecutionTask | None = None
    last_result: ExecutionResult | None = None

State Transition Diagram

stateDiagram-v2
    [*] --> pending: Create from compile_plan

    pending --> running: All dependencies completed, scheduler picks task
    running --> completed: Execution success
    running --> pending: Execution fails, attempts < max
    running --> failed: Execution fails, attempts >= max

    completed --> [*]
    failed --> [*]
    pending --> running: Retry cycle

Key Design Decisions





No Implicit Ordering: Execution depends ONLY on dependencies, not task.id





ready_tasks has stable sort for log determinism (fine for now - single-threaded)



Scheduler picks ready[0] only for serial execution, not for logic



"ready" state is derived (not stored) - stored states: pending, running, completed, failed



Scheduler Controls Retry Loop: No double retry





_dispatch_once executes once and increments attempts



Scheduler loop handles: attempt → fail → pending → retry



Single retry source of truth in scheduler



Explicit State Transitions: All state changes go through scheduler methods





transition_to_running(), transition_to_completed(), etc.



No direct status mutation bypassing scheduler



No explicit "ready" state stored (derived from dependencies)



No max_rounds: Deadlock detection is sufficient





Loop continues until all tasks complete or starvation detected



Starvation handled by _handle_starvation()



Execution Isolation:





Execution reads ONLY from task.arguments



Arguments are generated BEFORE execution loop (not during)



Execution writes ONLY to: task.status, task.attempts, task.last_result



Minimal Failure Handling:





If task fails and attempts < max_attempts: set back to pending



Else: mark failed and stop execution



No replanning logic in scheduler (replan handled by DagExecutor wrapper)



Clean Separation:





DagScheduler: Pure scheduling logic (no replanning, no tracing)



DagExecutor: Orchestration layer (replanning, tracing, metadata)



TaskScheduler: State machine transitions only

Files to Modify





/Users/shang/my_work/AutoStudio/agent_v2/schemas/execution_task.py





Enhance TaskScheduler with transition methods (simplified: no explicit "ready" state)



Add SchedulerResult schema



/Users/shang/my_work/AutoStudio/agent_v2/runtime/dag_executor.py





Replace _run_graph_to_event with explicit scheduler



Add _snapshot_all_arguments for pre-execution argument generation



Remove _execute_task_with_retries (scheduler now handles retry loop)



NEW: /Users/shang/my_work/AutoStudio/agent_v2/runtime/dag_scheduler.py





Create DagScheduler class with run_scheduler method



Implement _execute_one_task (controls pending → running retry loop)



Implement _dispatch_once (single execution, no internal retry)



Implement _handle_starvation for deadlock/failure detection

Testing Strategy

Add tests in /Users/shang/my_work/AutoStudio/tests/:





test_dag_scheduler.py: Unit tests for DagScheduler





Test dependency-driven execution order



Test state transitions (pending → running → completed/failed)



Test scheduler-controlled retry loop (no double retry)



Test deadlock detection (no max_rounds, starvation check only)



test_task_scheduler.py: Unit tests for TaskScheduler





Test transition_to_running, transition_to_completed, etc.



Test validation of allowed transitions (no explicit "ready" state)



Test ready_tasks dependency checking (ready is derived, not stored)



Update existing tests:





test_replanner.py: Should still work (replan logic unchanged)



test_act_tool_execution_dispatch.py: Should still work (end-to-end)



test_langfuse_phase11.py: Should still work (tracing unchanged)