"""Goal evaluator: deterministic check whether the user goal was satisfied after plan execution."""

from agent.memory.state import AgentState


class GoalEvaluator:
    """
    Phase-4 baseline: determine if the task goal has been satisfied.
    Uses only state (instruction, completed steps, step results, files modified, patches).
    Deterministic and lightweight; no external model calls.
    """

    def evaluate(self, instruction: str, state: AgentState) -> bool:
        """
        Return True if the goal is considered satisfied, False otherwise.

        Meaningful progress (deterministic, no external models):
        1. Any EDIT step succeeded → True.
        2. Any StepResult has patch_size > 0 → True.
        3. Any StepResult has non-empty files_modified → True.
        4. Instruction requests explanation ("explain") and an EXPLAIN step succeeded → True.
        Otherwise → False.
        """
        results = state.step_results
        if not results:
            return False

        # Phase 6A: single-lane per task. Any lane violation makes the goal unmet.
        try:
            violations = (state.context or {}).get("lane_violations") if hasattr(state, "context") else None
            if violations and isinstance(violations, list) and len(violations) > 0:
                return False
        except Exception:
            pass

        instruction_lower = (instruction or "").lower()

        for r in results:
            if getattr(r, "action", "").upper() == "EDIT" and getattr(r, "success", False):
                return True

            pm = getattr(r, "patch_size", None)
            if isinstance(pm, int) and pm > 0:
                return True
            if isinstance(pm, list) and len(pm) > 0:
                return True

            fm = getattr(r, "files_modified", None)
            if fm and isinstance(fm, list) and len(fm) > 0:
                return True

            if "explain" in instruction_lower and getattr(r, "action", "").upper() == "EXPLAIN" and getattr(r, "success", False):
                return True

        return False
