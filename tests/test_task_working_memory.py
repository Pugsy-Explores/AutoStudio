from __future__ import annotations

from agent_v2.memory.task_working_memory import (
    TASK_WORKING_MEMORY_CONTEXT_KEY,
    TaskWorkingMemory,
    task_working_memory_from_state,
)


class _S:
    def __init__(self) -> None:
        self.context: dict = {}


def test_task_working_memory_from_state_creates():
    s = _S()
    wm = task_working_memory_from_state(s)
    assert isinstance(wm, TaskWorkingMemory)
    assert TASK_WORKING_MEMORY_CONTEXT_KEY in s.context


def test_record_exploration_tick_partial_streak():
    wm = TaskWorkingMemory()
    for _ in range(2):
        wm.record_exploration_tick(
            exploration_id="e1",
            query_hash="abc",
            confidence="medium",
            gaps_nonempty=True,
            understanding="partial",
        )
    assert wm.partial_streak == 2
    assert wm.partial_repeat_exhausted(max_streak=2)


def test_fingerprint_stable():
    wm = TaskWorkingMemory(current_goal="x", iteration_count=1)
    assert len(wm.fingerprint()) == 24
