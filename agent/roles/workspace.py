"""AgentWorkspace: shared state for Phase 9 multi-agent orchestration.

Wraps AgentState; carries orchestration-level fields: goal, plan, candidate_files,
patches, test_results. All runtime state (tool results, context) remains in AgentState.
"""

from dataclasses import dataclass, field

from agent.memory.state import AgentState


@dataclass
class AgentWorkspace:
    """Shared workspace passed between supervisor and role agents."""

    goal: str
    state: AgentState
    plan: dict = field(default_factory=lambda: {"steps": []})
    candidate_files: list[str] = field(default_factory=list)
    candidate_symbols: list[str] = field(default_factory=list)
    patches: list[dict] = field(default_factory=list)
    test_results: dict | None = None  # {"status": "PASS"|"FAIL"|"ERROR", "stdout": str, "stderr": str, "returncode": int}
    trace: list[dict] = field(default_factory=list)
    retry_instruction: str | None = None  # From critic when retrying

    @classmethod
    def from_goal(cls, goal: str, project_root: str, trace_id: str) -> "AgentWorkspace":
        """Create workspace from goal with fresh AgentState."""
        state = AgentState(
            instruction=goal,
            current_plan={"steps": []},
            context={
                "project_root": project_root,
                "trace_id": trace_id,
                "instruction": goal,
                "tool_node": "START",
                "retrieved_files": [],
                "retrieved_symbols": [],
                "retrieved_references": [],
                "context_snippets": [],
                "ranked_context": [],
                "context_candidates": [],
                "ranking_scores": [],
            },
        )
        return cls(goal=goal, state=state)
