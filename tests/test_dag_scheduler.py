"""
Unit tests for DagScheduler - dependency-driven execution with simplified state machine.

Tests:
- Dependency-driven execution order (no implicit ordering by task.id)
- State transitions (pending → running → completed/failed, no explicit "ready" state)
- Scheduler-controlled retry loop (no double retry, only _dispatch_once)
- Deadlock detection (no max_rounds, starvation check only)
"""
from unittest.mock import MagicMock, Mock

import pytest

from agent_v2.schemas.execution import (
    ErrorType,
    ExecutionError,
    ExecutionMetadata,
    ExecutionOutput,
    ExecutionResult,
)
from agent_v2.schemas.execution_task import (
    ExecutionTask,
    SchedulerResult,
    TaskScheduler,
)
from agent_v2.runtime.dag_scheduler import DagScheduler


class TestTaskSchedulerStateTransitions:
    """Test TaskScheduler state transition methods."""

    def test_transition_to_running_from_pending(self):
        """Pending → running transition should work."""
        task = ExecutionTask(
            id="task1",
            tool="search",
            status="pending",
            attempts=0,
            max_attempts=2,
        )
        updated = TaskScheduler.transition_to_running(task)
        assert updated.status == "running"
        assert updated.started_at is not None

    def test_transition_to_running_fails_from_non_pending(self):
        """Cannot transition to running from non-pending status."""
        for status in ["running", "completed", "failed"]:
            task = ExecutionTask(
                id="task1",
                tool="search",
                status=status,
                attempts=1,
                max_attempts=2,
            )
            with pytest.raises(ValueError, match="Cannot transition.*from.*running"):
                TaskScheduler.transition_to_running(task)

    def test_transition_to_completed_from_running(self):
        """Running → completed transition should work."""
        task = ExecutionTask(
            id="task1",
            tool="search",
            status="running",
            attempts=1,
            max_attempts=2,
        )
        result = ExecutionResult(
            step_id="task1",
            success=True,
            status="success",
            output=ExecutionOutput(summary="Done", data={}),
            error=None,
            metadata=ExecutionMetadata(
                tool_name="search",
                duration_ms=100,
                timestamp="2024-01-01T00:00:00Z",
            ),
        )
        updated = TaskScheduler.transition_to_completed(task, result)
        assert updated.status == "completed"
        assert updated.completed_at is not None
        assert updated.last_result == result

    def test_transition_to_pending_after_failure_with_attempts_left(self):
        """Running → pending transition should work when attempts < max."""
        task = ExecutionTask(
            id="task1",
            tool="search",
            status="running",
            attempts=1,
            max_attempts=2,
        )
        result = ExecutionResult(
            step_id="task1",
            success=False,
            status="failure",
            output=ExecutionOutput(summary="Failed", data={}),
            error=ExecutionError(
                type=ErrorType.unknown,
                message="test error",
            ),
            metadata=ExecutionMetadata(
                tool_name="search",
                duration_ms=100,
                timestamp="2024-01-01T00:00:00Z",
            ),
        )
        updated = TaskScheduler.transition_to_pending_after_failure(task)
        assert updated.status == "pending"  # Back to pending for retry
        assert updated.started_at is not None  # Preserve started_at

    def test_transition_to_pending_after_failure_fails_when_attempts_exceeded(self):
        """Cannot transition to pending when attempts >= max."""
        task = ExecutionTask(
            id="task1",
            tool="search",
            status="running",
            attempts=2,
            max_attempts=2,
        )
        result = ExecutionResult(
            step_id="task1",
            success=False,
            status="failure",
            output=ExecutionOutput(summary="Failed", data={}),
            error=ExecutionError(
                type=ErrorType.unknown,
                message="test error",
            ),
            metadata=ExecutionMetadata(
                tool_name="search",
                duration_ms=100,
                timestamp="2024-01-01T00:00:00Z",
            ),
        )
        with pytest.raises(ValueError, match="exceeded max attempts"):
            TaskScheduler.transition_to_pending_after_failure(task)

    def test_transition_to_failed_from_running(self):
        """Running → failed terminal transition should work."""
        task = ExecutionTask(
            id="task1",
            tool="search",
            status="running",
            attempts=2,
            max_attempts=2,
        )
        result = ExecutionResult(
            step_id="task1",
            success=False,
            status="failure",
            output=ExecutionOutput(summary="Failed", data={}),
            error=ExecutionError(
                type=ErrorType.unknown,
                message="test error",
            ),
            metadata=ExecutionMetadata(
                tool_name="search",
                duration_ms=100,
                timestamp="2024-01-01T00:00:00Z",
            ),
        )
        updated = TaskScheduler.transition_to_failed(task, result)
        assert updated.status == "failed"
        assert updated.completed_at is not None
        assert updated.last_result == result


class TestTaskSchedulerReadyTasks:
    """Test TaskScheduler.ready_tasks dependency checking."""

    def test_ready_tasks_finds_tasks_with_completed_dependencies(self):
        """Ready tasks are those with all dependencies completed."""
        tasks_by_id = {
            "t1": ExecutionTask(id="t1", tool="search", status="pending", dependencies=[]),
            "t2": ExecutionTask(id="t2", tool="edit", status="pending", dependencies=["t1"]),
            "t3": ExecutionTask(id="t3", tool="finish", status="pending", dependencies=["t2"]),
        }

        # Initially only t1 is ready (no dependencies)
        ready = TaskScheduler.ready_tasks(tasks_by_id, set())
        assert len(ready) == 1
        assert ready[0].id == "t1"

        # After t1 completes, t2 is ready
        ready = TaskScheduler.ready_tasks(tasks_by_id, {"t1"})
        assert len(ready) == 1
        assert ready[0].id == "t2"

        # After t2 completes, t3 is ready
        ready = TaskScheduler.ready_tasks(tasks_by_id, {"t1", "t2"})
        assert len(ready) == 1
        assert ready[0].id == "t3"

    def test_ready_tasks_ignores_non_pending_status(self):
        """All tasks with all deps completed but non-pending status are not ready."""
        tasks_by_id = {
            "t1": ExecutionTask(id="t1", tool="search", status="completed", dependencies=[]),
            "t2": ExecutionTask(id="t2", tool="edit", status="running", dependencies=["t1"]),
            "t3": ExecutionTask(id="t3", tool="finish", status="failed", dependencies=["t1"]),
        }

        ready = TaskScheduler.ready_tasks(tasks_by_id, {"t1"})
        assert len(ready) == 0  # None are pending

    def test_ready_tasks_uses_stable_sort_for_determinism(self):
        """Ready tasks are sorted by id for log determinism (not for execution semantics)."""
        tasks_by_id = {
            "t3": ExecutionTask(id="t3", tool="search", status="pending", dependencies=[]),
            "t1": ExecutionTask(id="t1", tool="edit", status="pending", dependencies=[]),
            "t2": ExecutionTask(id="t2", tool="finish", status="pending", dependencies=[]),
        }

        ready = TaskScheduler.ready_tasks(tasks_by_id, set())
        assert len(ready) == 3
        assert [t.id for t in ready] == ["t1", "t2", "t3"]  # Sorted by id


class TestDagScheduler:
    """Test DagScheduler with scheduler-controlled retry loop."""

    def test_scheduler_controls_retry_loop_no_double_retry(self):
        """Scheduler controls pending → running retry loop, no double retry in dispatch."""
        # Create tasks: t1 (success), t2 (fails twice then succeeds)
        tasks = [
            ExecutionTask(
                id="t1",
                tool="search",
                status="pending",
                dependencies=[],
                arguments={"query": "test"},
                attempts=0,
                max_attempts=2,
            ),
            ExecutionTask(
                id="t2",
                tool="edit",
                status="pending",
                dependencies=["t1"],
                arguments={"path": "test.txt"},
                attempts=0,
                max_attempts=3,
            ),
        ]

        # Mock dispatcher
        dispatcher = Mock()
        state = Mock()

        # t1 succeeds on first attempt
        t1_success = ExecutionResult(
            step_id="t1",
            success=True,
            status="success",
            output=ExecutionOutput(summary="Found results", data={}),
            error=None,
            metadata=Mock(duration_ms=100, timestamp="2024-01-01T00:00:00Z"),
        )

        # t2 fails twice then succeeds
        t2_fail1 = ExecutionResult(
            step_id="t2",
            success=False,
            status="failure",
            output=ExecutionOutput(summary="Edit failed", data={}),
            error=Mock(type="error", message="temporary error"),
            metadata=Mock(duration_ms=100, timestamp="2024-01-01T00:00:01Z"),
        )
        t2_fail2 = ExecutionResult(
            step_id="t2",
            success=False,
            status="failure",
            output=ExecutionOutput(summary="Edit failed", data={}),
            error=Mock(type="error", message="temporary error"),
            metadata=Mock(duration_ms=100, timestamp="2024-01-01T00:00:02Z"),
        )
        t2_success = ExecutionResult(
            step_id="t2",
            success=True,
            status="success",
            output=ExecutionOutput(summary="Edit applied", data={}),
            error=None,
            metadata=Mock(duration_ms=100, timestamp="2024-01-01T00:00:03Z"),
        )

        dispatcher.execute.side_effect = [t1_success, t2_fail1, t2_fail2, t2_success]

        # Mock argument generator
        argument_generator = Mock()

        # Create and run scheduler
        scheduler = DagScheduler(dispatcher, argument_generator)
        result = scheduler.run_scheduler(tasks, state)

        # Should succeed after t2 retry loop
        assert result.status == "success"
        assert result.completed_ids == {"t1", "t2"}

        # Verify dispatch was called correct number of times:
        # t1: 1 call (success)
        # t2: 3 calls (fail, fail, success)
        assert dispatcher.execute.call_count == 4

    def test_scheduler_stops_on_final_failure(self):
        """Scheduler stops and returns failed when task exhausts max_attempts."""
        tasks = [
            ExecutionTask(
                id="t1",
                tool="search",
                status="pending",
                dependencies=[],
                arguments={"query": "test"},
                attempts=0,
                max_attempts=2,
            ),
        ]

        dispatcher = Mock()
        state = Mock()

        # t1 fails on all attempts
        t1_fail = ExecutionResult(
            step_id="t1",
            success=False,
            status="failure",
            output=ExecutionOutput(summary="Search failed", data={}),
            error=Mock(type="error", message="permanent error"),
            metadata=Mock(duration_ms=100, timestamp="2024-01-01T00:00:01Z"),
        )

        dispatcher.execute.return_value = t1_fail

        argument_generator = Mock()

        scheduler = DagScheduler(dispatcher, argument_generator)
        result = scheduler.run_scheduler(tasks, state)

        # Should fail after exhausting retries
        assert result.status == "failed"
        assert result.failed_task.id == "t1"
        assert result.failed_task.attempts == 2  # Max attempts reached
        assert result.failed_task.status == "failed"

        # Only 2 calls (max_attempts)
        assert dispatcher.execute.call_count == 2

    def test_scheduler_detects_deadlock(self):
        """Scheduler detects deadlock when no ready tasks but not all completed."""
        # Create tasks with circular dependency
        tasks = [
            ExecutionTask(
                id="t1",
                tool="search",
                status="pending",
                dependencies=["t2"],  # Depends on t2
                arguments={},
            ),
            ExecutionTask(
                id="t2",
                tool="edit",
                status="pending",
                dependencies=["t1"],  # Depends on t1 (circular)
                arguments={},
            ),
        ]

        dispatcher = Mock()
        state = Mock()
        argument_generator = Mock()

        scheduler = DagScheduler(dispatcher, argument_generator)
        result = scheduler.run_scheduler(tasks, state)

        # Should detect deadlock
        assert result.status == "deadlock"
        assert dispatcher.execute.call_count == 0

    def test_scheduler_dependency_driven_execution_order(self):
        """Tasks execute in dependency order, not by id."""
        # Create tasks out-of-order by id
        tasks = [
            ExecutionTask(
                id="t3",
                tool="finish",
                status="pending",
                dependencies=["t2"],
                arguments={},
            ),
            ExecutionTask(
                id="t1",
                tool="search",
                status="pending",
                dependencies=[],
                arguments={"query": "test"},
            ),
            ExecutionTask(
                id="t2",
                tool="edit",
                status="pending",
                dependencies=["t1"],
                arguments={"path": "test.txt"},
            ),
        ]

        dispatcher = Mock()
        state = Mock()

        # All tasks succeed
        success = ExecutionResult(
            step_id="",
            success=True,
            status="success",
            output=ExecutionOutput(summary="Success", data={}),
            error=None,
            metadata=Mock(duration_ms=100, timestamp="2024-01-01T00:00:00Z"),
        )

        # Return success with correct step_id
        def execute_side_effect(step, _state):
            step_id = step.get("step_id", "")
            return ExecutionResult(
                step_id=step_id,
                success=True,
                status="success",
                output=ExecutionOutput(summary="Success", data={}),
                error=None,
                metadata=Mock(duration_ms=100, timestamp="2024-01-01T00:00:00Z"),
            )

        dispatcher.execute.side_effect = execute_side_effect

        argument_generator = Mock()

        scheduler = DagScheduler(dispatcher, argument_generator)
        result = scheduler.run_scheduler(tasks, state)

        assert result.status == "success"
        assert result.completed_ids == {"t1", "t2", "t3"}

        # Verify execution order: t1 → t2 → t3 (dependency order, not id order)
        call_order = [call[0][0].get("step_id") for call in dispatcher.execute.call_args_list]
        assert call_order == ["t1", "t2", "t3"]

    def test_scheduler_no_max_rounds(self):
        """Scheduler uses while True loop with deadlock detection, not max_rounds."""
        # Create tasks with many retries to test no max_rounds limit
        tasks = [
            ExecutionTask(
                id="t1",
                tool="search",
                status="pending",
                dependencies=[],
                arguments={"query": "test"},
                attempts=0,
                max_attempts=10,  # Many retries
            ),
        ]

        dispatcher = Mock()
        state = Mock()

        # t1 fails many times then succeeds
        fail_count = 0

        def execute_side_effect(_, _state):
            nonlocal fail_count
            fail_count += 1
            if fail_count < 8:
                return ExecutionResult(
                    step_id="t1",
                    success=False,
                    status="failure",
                    output=ExecutionOutput(summary="Retry", data={}),
                    error=Mock(type="error", message="temporary"),
                    metadata=Mock(duration_ms=100, timestamp="2024-01-01T00:00:00Z"),
                )
            return ExecutionResult(
                step_id="t1",
                success=True,
                status="success",
                output=ExecutionOutput(summary="Success", data={}),
                error=None,
                metadata=Mock(duration_ms=100, timestamp="2024-01-01T00:00:00Z"),
            )

        dispatcher.execute.side_effect = execute_side_effect

        argument_generator = Mock()

        scheduler = DagScheduler(dispatcher, argument_generator)
        result = scheduler.run_scheduler(tasks, state)

        # Should succeed after 8 attempts (no max_rounds limit)
        assert result.status == "success"
        assert dispatcher.execute.call_count == 8

    def test_scheduler_execution_isolation(self):
        """Execution reads only from task.arguments, no additional generation during execution."""
        tasks = [
            ExecutionTask(
                id="t1",
                tool="search",
                status="pending",
                dependencies=[],
                arguments={"query": "pre-generated query"},  # Arguments pre-generated
                attempts=0,
                max_attempts=1,
            ),
        ]

        dispatcher = Mock()
        state = Mock()

        t1_success = ExecutionResult(
            step_id="t1",
            success=True,
            status="success",
            output=ExecutionOutput(summary="Results", data={}),
            error=None,
            metadata=Mock(duration_ms=100, timestamp="2024-01-01T00:00:00Z"),
        )

        dispatcher.execute.return_value = t1_success

        argument_generator = Mock()

        scheduler = DagScheduler(dispatcher, argument_generator)
        result = scheduler.run_scheduler(tasks, state)

        assert result.status == "success"

        # Verify argument_generator was NOT called during execution
        # (arguments were pre-generated before scheduler run)
        argument_generator.generate.assert_not_called()

        # Verify dispatcher received the pre-generated arguments
        call_args = dispatcher.execute.call_args[0][0]
        assert call_args["_react_args"] == {"query": "pre-generated query"}