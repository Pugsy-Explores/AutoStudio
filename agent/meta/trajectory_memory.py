"""
Trajectory memory: in-memory store of attempt-level execution data for retry learning.

Phase 5: each attempt records plan, step_results, errors, patches_applied, files_modified, goal_met.
Used by the attempt loop to pass previous attempts and critic feedback into the planner.
"""


class TrajectoryMemory:
    """Stores attempt-level data for the current task. No persistence; used within a single run."""

    def __init__(self) -> None:
        self.attempts: list[dict] = []

    def record_attempt(self, attempt_data: dict) -> None:
        """Append one attempt. attempt_data must include: plan, step_results, errors, patches_applied, files_modified, goal_met."""
        self.attempts.append(attempt_data)

    def last_attempt(self) -> dict | None:
        """Return the most recent attempt, or None if none recorded."""
        if not self.attempts:
            return None
        return self.attempts[-1]

    def all_attempts(self) -> list[dict]:
        """Return all recorded attempts in order."""
        return self.attempts
