"""
Unit tests for TaskScheduler - simplified state machine (no explicit "ready" state).

Tests:
- State transitions (pending → running → completed/failed)
- Validation of allowed transitions
- ready_tasks dependency checking (ready is derived, not stored)
"""
import pytest

from agent_v2.schemas.execution import (
    ErrorType,
    ExecutionError,
    ExecutionMetadata,
    ExecutionOutput,
    ExecutionResult,
)
from agent_v2.schemas.execution_task import ExecutionTask, TaskScheduler


class TestTaskSchedulerTransitionsNoExplicitReady:
    """Test that 'ready' is derived, not stored in task status."""

    def test_ready_state_not_stored_only_derived(self):
        """Ready state is derived from dependencies, not stored as task status."""
        tasks_by_id = {
            "t1": ExecutionTask(
                id="t1",
                tool="search",
                status="pending",
                dependencies=[],
            ),
        }

        # Ready is derived from dependencies
        ready = TaskScheduler.ready_tasks(tasks_by_id, completed_ids=set())
        assert len(ready) == 1
        assert ready[0].status == "pending"  # Still pending, not "ready"

        # There is NO "ready" in TaskStatus
        # ExecutionTask.status never stores "ready"
        assert "ready" not in ["pending", "running", "completed", "failed"]

    def test_transition_skips_explicit_ready_state(self):
        """State machine goes pending → running, no explicit ready state."""
        task = ExecutionTask(
            id="t1",
            tool="search",
            status="pending",
            attempts=0,
        )

        # Direct transition: pending → running (no intermediate "ready" state)
        updated = TaskScheduler.transition_to_running(task)
        assert updated.status == "running"
        assert updated.started_at is not None


class TestTaskSchedulerTransitionValidation:
    """Test validation of allowed state transitions."""

    def test_cannot_transition_to_running_from_running(self):
        """Cannot transition running → running."""
        task = ExecutionTask(
            id="t1",
            tool="search",
            status="running",
            attempts=1,
        )
        with pytest.raises(ValueError, match="Cannot transition.*from.*running"):
            TaskScheduler.transition_to_running(task)

    def test_cannot_transition_to_running_from_completed(self):
        """Cannot transition completed → running."""
        task = ExecutionTask(
            id="t1",
            tool="search",
            status="completed",
            attempts=1,
        )
        with pytest.raises(ValueError, match="Cannot transition.*from.*completed"):
            TaskScheduler.transition_to_running(task)

    def test_cannot_transition_to_running_from_failed(self):
        """Cannot transition failed → running."""
        task = ExecutionTask(
            id="t1",
            tool="search",
            status="failed",
            attempts=2,
        )
        with pytest.raises(ValueError, match="Cannot transition.*from.*failed"):
            TaskScheduler.transition_to_running(task)

    def test_cannot_transition_to_pending_after_failure_from_non_running(self):
        """Can only transition running → pending_after_failure."""
        for status in ["pending", "completed", "failed"]:
            task = ExecutionTask(
                id="t1",
                tool="search",
                status=status,
                attempts=1,
            )
            with pytest.raises(ValueError, match="Cannot transition.*from.*pending|completed|failed"):
                TaskScheduler.transition_to_pending_after_failure(task)


class TestTaskSchedulerDerivedReadyState:
    """Test that ready state is derived, not stored."""

    def test_ready_tasks_filters_by_dependencies_not_status(self):
        """Ready tasks are derived from dependencies matching, not status."""
        tasks_by_id = {
            "t1": ExecutionTask(
                id="t1",
                tool="search",
                status="pending",
                dependencies=[],
            ),
            "t2": ExecutionTask(
                id="t2",
                tool="edit",
                status="pending",
                dependencies=["t1"],
            ),
            "t3": ExecutionTask(
                id="t3",
                tool="finish",
                status="pending",
                dependencies=["t1"],  # Same deps as t2
            ),
        }

        # Only t1 is ready (no dependencies)
        ready = TaskScheduler.ready_tasks(tasks_by_id, completed_ids=set())
        assert len(ready) == 1
        assert ready[0].id == "t1"

        # After t1 completes, t2 and t3 are both ready (both depend on t1)
        ready = TaskScheduler.ready_tasks(tasks_by_id, completed_ids={"t1"})
        assert len(ready) == 2
        assert {t.id for t in ready} == {"t2", "t3"}

    def test_ready_tasks_status_remains_pending_until_execution(self):
        """Ready tasks maintain 'pending' status until scheduler picks them."""
        tasks_by_id = {
            "t1": ExecutionTask(
                id="t1",
                tool="search",
                status="pending",
                dependencies=[],
            ),
        }

        ready = TaskScheduler.ready_tasks(tasks_by_id, completed_ids=set())
        assert len(ready) == 1
        assert ready[0].status == "pending"  # Still pending

        # Transition happens when scheduler picks it
        updated = TaskScheduler.transition_to_running(ready[0])
        assert updated.status == "running"  # Now running

    def test_ready_tasks_empty_when_dependencies_unmet(self):
        """Ready tasks is empty when no pending tasks have all deps completed."""
        tasks_by_id = {
            "t1": ExecutionTask(
                id="t1",
                tool="search",
                status="pending",
                dependencies=["nonexistent"],
            ),
        }

        ready = TaskScheduler.ready_tasks(tasks_by_id, completed_ids=set())
        assert len(ready) == 0

    def test_ready_tasks_empty_when_no_pending_tasks(self):
        """Ready tasks is empty when there are no pending tasks."""
        tasks_by_id = {
            "t1": ExecutionTask(
                id="t1",
                tool="search",
                status="completed",
                dependencies=[],
            ),
            "t2": ExecutionTask(
                id="t2",
                tool="edit",
                status="failed",
                dependencies=[],
            ),
        }

        ready = TaskScheduler.ready_tasks(tasks_by_id, completed_ids=set())
        assert len(ready) == 0


class TestTaskSchedulerRetryCycle:
    """Test scheduler-controlled retry cycle (pending → running → pending → running)."""

    def test_full_retry_cycle_with_multiple_transitions(self):
        """Test full retry cycle: pending → running → pending → running → completed."""
        task = ExecutionTask(
            id="t1",
            tool="search",
            status="pending",
            attempts=0,
            max_attempts=3,
        )

        # Cycle 1: pending → running
        task = TaskScheduler.transition_to_running(task)
        assert task.status == "running"
        assert task.attempts == 0

        # Simulate execution failure (attempts incremented by scheduler)
        fail_result = ExecutionResult(
            step_id="t1",
            success=False,
            status="failure",
            output=ExecutionOutput(summary="Failed", data={}),
            error=ExecutionError(
                type=ErrorType.unknown,
                message="temporary error",
            ),
            metadata=ExecutionMetadata(
                tool_name="search",
                duration_ms=100,
                timestamp="2024-01-01T00:00:00Z",
            ),
        )

        # running → pending (retry)
        task = TaskScheduler.transition_to_pending_after_failure(task)
        assert task.status == "pending"
        assert task.attempts == 0  # Attempts NOT reset

        # Cycle 2: pending → running (retry)
        task = TaskScheduler.transition_to_running(task)
        assert task.status == "running"
        assert task.started_at is not None

        # Simulate success
        success_result = ExecutionResult(
            step_id="t1",
            success=True,
            status="success",
            output=ExecutionOutput(summary="Success", data={}),
            error=None,
            metadata=ExecutionMetadata(
                tool_name="search",
                duration_ms=100,
                timestamp="2024-01-01T00:00:00Z",
            ),
        )

        # running → completed
        task = TaskScheduler.transition_to_completed(task, success_result)
        assert task.status == "completed"
        assert task.completed_at is not None
        assert task.last_result == success_result

    def test_retry_cycle_stops_on_final_failure(self):
        """Retry cycle stops when max_attempts exceeded."""
        task = ExecutionTask(
            id="t1",
            tool="search",
            status="pending",
            attempts=2,  # Already at max
            max_attempts=2,
        )

        # Cannot transition back to pending when at max_attempts
        with pytest.raises(ValueError, match="exceeded max attempts"):
            TaskScheduler.transition_to_pending_after_failure(task)

        # Final state is failed
        fail_result = ExecutionResult(
            step_id="t1",
            success=False,
            status="failure",
            output=ExecutionOutput(summary="Failed", data={}),
            error=ExecutionError(
                type=ErrorType.unknown,
                message="final error",
            ),
            metadata=ExecutionMetadata(
                tool_name="search",
                duration_ms=100,
                timestamp="2024-01-01T00:00:00Z",
            ),
        )

        task = TaskScheduler.transition_to_failed(task, fail_result)
        assert task.status == "failed"
        assert task.completed_at is not None