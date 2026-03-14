"""Agent state: instruction, plan, completed steps, results, context."""

from dataclasses import dataclass, field

from agent.memory.step_result import StepResult


@dataclass
class AgentState:
    instruction: str
    current_plan: dict
    completed_steps: list = field(default_factory=list)
    step_results: list[StepResult] = field(default_factory=list)
    context: dict = field(default_factory=dict)

    def is_finished(self) -> bool:
        """True when there is no next step to execute."""
        return self.next_step() is None

    def next_step(self):
        """Return the first step not in completed_steps, or None."""
        steps = self.current_plan.get("steps") or []
        completed_ids = {s.get("id") for s in self.completed_steps}
        for step in steps:
            if isinstance(step, dict) and step.get("id") not in completed_ids:
                return step
        return None

    def record(self, step: dict, result: StepResult) -> None:
        """Append result and step to step_results and completed_steps."""
        self.step_results.append(result)
        self.completed_steps.append(step)

    def update_plan(self, new_plan: dict) -> None:
        """Replace current_plan with new_plan (e.g. after replanning)."""
        self.current_plan = new_plan
