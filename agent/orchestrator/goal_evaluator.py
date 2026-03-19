"""Goal evaluator: deterministic check whether the user goal was satisfied after plan execution."""

from agent.memory.state import AgentState


def is_explain_like_instruction(instruction: str) -> bool:
    """
    Deterministic heuristic: True if the user is seeking explanation/understanding.

    Phase 6B: This replaces the brittle literal '"explain"' check.
    """
    if not instruction:
        return False
    i = instruction.strip().lower()
    if not i:
        return False
    patterns = (
        "explain",
        "how does",
        "how do",
        "how is",
        "where is",
        "where does",
        "what is",
        "what does",
        "trace",
        "walk through",
        "audit",
        "summarize",
        "describe",
        "show me how",
        "show where",
    )
    return any(p in i for p in patterns)


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

        decision, _reason, _signals = self.evaluate_with_reason(instruction, state)
        return decision

    def evaluate_with_reason(
        self,
        instruction: str,
        state: AgentState,
        *,
        phase_subgoal: str | None = None,
    ) -> tuple[bool, str, dict]:
        """
        Phase 6C: same deterministic evaluation, but returns a compact reason and signals
        for trace-proofing.

        Returns: (goal_met, reason, signals)
        """
        effective_instruction = phase_subgoal if phase_subgoal is not None else instruction
        instruction_lower = (effective_instruction or "").lower()
        explain_like = is_explain_like_instruction(instruction_lower)

        # Phase 6F: explicit terminal stall policy surfaced via state.context.
        termination_reason = None
        try:
            termination_reason = (state.context or {}).get("termination_reason") if hasattr(state, "context") else None
        except Exception:
            termination_reason = None
        if termination_reason == "stall_detected":
            return (
                False,
                "stall_detected",
                {
                    "explain_like": explain_like,
                    "outcome_code": "goal_not_satisfied",
                    "reason_code": "stall_detected",
                },
            )

        results = state.step_results
        if not results:
            return (
                False,
                "no_step_results",
                {
                    "explain_like": explain_like,
                    "outcome_code": "no_results",
                    "reason_code": "unknown",
                },
            )
        # Phase 6A: single-lane per task. Any lane violation makes the goal unmet.
        try:
            violations = (state.context or {}).get("lane_violations") if hasattr(state, "context") else None
            if violations and isinstance(violations, list) and len(violations) > 0:
                return (
                    False,
                    "lane_violation_present",
                    {
                        "explain_like": is_explain_like_instruction(instruction),
                        "outcome_code": "lane_violation",
                        "reason_code": "lane_violation",
                    },
                )
        except Exception:
            pass

        # Phase 7B.1: docs-lane success semantics.
        # Keep distinct from explain-like intent: if the task ran in dominant docs lane and an EXPLAIN
        # step succeeded, consider the goal satisfied (docs tasks are inherently informational).
        try:
            dom = (state.context or {}).get("dominant_artifact_mode") if hasattr(state, "context") else None
            if dom == "docs":
                for r in results:
                    if getattr(r, "action", "").upper() == "EXPLAIN" and getattr(r, "success", False):
                        return (
                            True,
                            "docs_lane_explain_succeeded",
                            {"explain_like": explain_like, "outcome_code": "success", "reason_code": None},
                        )
        except Exception:
            pass

        for r in results:
            if getattr(r, "action", "").upper() == "EDIT" and getattr(r, "success", False):
                return True, "edit_succeeded", {"explain_like": explain_like, "outcome_code": "success", "reason_code": None}

            pm = getattr(r, "patch_size", None)
            if isinstance(pm, int) and pm > 0:
                return (
                    True,
                    "patch_size_positive",
                    {"explain_like": explain_like, "outcome_code": "success", "reason_code": None},
                )
            if isinstance(pm, list) and len(pm) > 0:
                return (
                    True,
                    "patch_list_nonempty",
                    {"explain_like": explain_like, "outcome_code": "success", "reason_code": None},
                )

            fm = getattr(r, "files_modified", None)
            if fm and isinstance(fm, list) and len(fm) > 0:
                return (
                    True,
                    "files_modified_nonempty",
                    {"explain_like": explain_like, "outcome_code": "success", "reason_code": None},
                )

            if (
                explain_like
                and getattr(r, "action", "").upper() == "EXPLAIN"
                and getattr(r, "success", False)
            ):
                return (
                    True,
                    "explain_like_explain_succeeded",
                    {"explain_like": explain_like, "outcome_code": "success", "reason_code": None},
                )

        return (
            False,
            "no_success_signals",
            {"explain_like": explain_like, "outcome_code": "goal_not_satisfied", "reason_code": "goal_not_satisfied"},
        )
