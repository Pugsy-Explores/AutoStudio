"""Goal manager: tracks goal completion, evaluates exit conditions, enforces safety limits."""

import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Safety limits (per Phase 7 roadmap and Rule 21)
DEFAULT_MAX_STEPS = 20
DEFAULT_MAX_TOOL_CALLS = 50
DEFAULT_MAX_RUNTIME_SECONDS = 60
DEFAULT_MAX_EDITS = 10


@dataclass
class GoalManagerState:
    """Tracks counts and timestamps for limit enforcement."""

    goal: str
    steps_completed: int = 0
    tool_calls: int = 0
    edits_count: int = 0
    start_time: float = field(default_factory=time.perf_counter)
    goal_achieved: bool = False
    limit_hit: str | None = None  # e.g. "max_steps", "max_tool_calls", "max_runtime", "max_edits"


class GoalManager:
    """
    Accepts goal string, tracks completion state, checks exit conditions.
    Signals the loop to stop when: goal achieved OR any safety limit hit.
    Does NOT call the LLM.
    """

    def __init__(
        self,
        goal: str,
        *,
        max_steps: int = DEFAULT_MAX_STEPS,
        max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS,
        max_runtime_seconds: float = DEFAULT_MAX_RUNTIME_SECONDS,
        max_edits: int = DEFAULT_MAX_EDITS,
    ):
        self.goal = goal
        self.max_steps = max_steps
        self.max_tool_calls = max_tool_calls
        self.max_runtime_seconds = max_runtime_seconds
        self.max_edits = max_edits
        self._state = GoalManagerState(goal=goal)

    def record_step(self, action: str, success: bool) -> None:
        """Record one completed step. Increment edits_count for EDIT actions."""
        self._state.steps_completed += 1
        if (action or "").upper() == "EDIT":
            self._state.edits_count += 1

    def record_tool_call(self) -> None:
        """Record one tool invocation."""
        self._state.tool_calls += 1

    def set_goal_achieved(self, achieved: bool = True) -> None:
        """Mark goal as achieved (e.g. from external evaluation)."""
        self._state.goal_achieved = achieved

    def should_stop(self) -> tuple[bool, str | None]:
        """
        Returns (should_stop, reason).
        reason is None when continuing; otherwise e.g. "goal_achieved", "max_steps", etc.
        """
        if self._state.goal_achieved:
            return True, "goal_achieved"
        if self._state.steps_completed >= self.max_steps:
            self._state.limit_hit = "max_steps"
            return True, "max_steps"
        if self._state.tool_calls >= self.max_tool_calls:
            self._state.limit_hit = "max_tool_calls"
            return True, "max_tool_calls"
        elapsed = time.perf_counter() - self._state.start_time
        if elapsed >= self.max_runtime_seconds:
            self._state.limit_hit = "max_runtime"
            return True, "max_runtime"
        if self._state.edits_count >= self.max_edits:
            self._state.limit_hit = "max_edits"
            return True, "max_edits"
        return False, None

    def get_limits_dict(self) -> dict:
        """Return limits for trace logging."""
        return {
            "max_steps": self.max_steps,
            "max_tool_calls": self.max_tool_calls,
            "max_runtime_seconds": self.max_runtime_seconds,
            "max_edits": self.max_edits,
        }

    def get_counts_dict(self) -> dict:
        """Return current counts for trace logging."""
        return {
            "steps_completed": self._state.steps_completed,
            "tool_calls": self._state.tool_calls,
            "edits_count": self._state.edits_count,
            "elapsed_seconds": time.perf_counter() - self._state.start_time,
        }

    def get_stop_reason(self) -> str | None:
        """Return limit_hit or None."""
        return self._state.limit_hit

    def reset_for_retry(self) -> None:
        """Reset counters for a new attempt. Preserves limits."""
        self._state.steps_completed = 0
        self._state.tool_calls = 0
        self._state.edits_count = 0
        self._state.start_time = time.perf_counter()
        self._state.limit_hit = None
        self._state.goal_achieved = False
