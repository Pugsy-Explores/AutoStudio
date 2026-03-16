"""Agent state: instruction, plan, completed steps, results, context.

Phase 4: Step identity is (plan_id, step_id). completed_steps stores tuples so
completed steps only apply to the current plan after replan.
"""

from dataclasses import dataclass, field

from agent.memory.step_result import StepResult


@dataclass
class AgentState:
    instruction: str
    current_plan: dict
    # Phase 4: (plan_id, step_id) tuples; list preserves order for undo_last_step and debugging.
    completed_steps: list = field(default_factory=list)  # list[tuple[str, int]]
    step_results: list[StepResult] = field(default_factory=list)
    # context: tool_node, retrieved_files, retrieved_symbols, retrieved_references,
    # context_snippets (list of {"file": str, "symbol": str, "snippet": str})
    context: dict = field(default_factory=dict)

    # O(1) lookup for record(); kept in sync with completed_steps.
    _completed_steps_set: set = field(default_factory=set, init=False)  # set[tuple[str, int]]

    def __post_init__(self) -> None:
        """Sync internal set from initial completed_steps (e.g. when state is created with existing list)."""
        self._completed_steps_set = set(self.completed_steps)

    @property
    def current_plan_id(self) -> str | None:
        """Plan ID of current plan (Phase 4 — plan-scoped step identity)."""
        return self.current_plan.get("plan_id")

    def is_finished(self) -> bool:
        """True when there is no next step to execute."""
        return self.next_step() is None

    def next_step(self):
        """Return the first step not in completed_steps for the current plan, or None."""
        steps = self.current_plan.get("steps") or []
        current_plan_id = self.current_plan_id
        completed_ids = {
            step_id
            for (plan_id, step_id) in self._completed_steps_set
            if plan_id == current_plan_id
        }
        for step in steps:
            if isinstance(step, dict) and step.get("id") not in completed_ids:
                return step
        return None

    def record(self, step: dict, result: StepResult) -> None:
        """Append result and (plan_id, step_id) to step_results and completed_steps."""
        self.step_results.append(result)
        key = (self.current_plan_id, step.get("id"))
        if key not in self._completed_steps_set:
            self._completed_steps_set.add(key)
            self.completed_steps.append(key)

    def undo_last_step(self) -> None:
        """Remove the last recorded step (used when validation fails and replan is triggered)."""
        if self.step_results:
            self.step_results.pop()
        if self.completed_steps:
            key = self.completed_steps.pop()
            self._completed_steps_set.discard(key)

    def update_plan(self, new_plan: dict) -> None:
        """Replace current_plan with new_plan (e.g. after replanning)."""
        self.current_plan = new_plan
