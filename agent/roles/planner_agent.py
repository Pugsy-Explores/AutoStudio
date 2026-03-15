"""Planner agent: convert goal -> structured plan via model_router (REASONING)."""

from planner.planner import plan

from agent.roles.base_role_agent import BaseRoleAgent
from agent.roles.workspace import AgentWorkspace


def _get_plan_for_goal(goal: str, context: dict) -> dict:
    """Use long-horizon planner when architecture_map available (Phase 10), else standard planner."""
    arch = context.get("architecture_map")
    if arch:
        from agent.repo_intelligence.long_horizon_planner import plan_long_horizon
        return plan_long_horizon(goal, arch)
    return plan(goal)


class PlannerAgent(BaseRoleAgent):
    """Converts goal to task plan with steps, acceptance criteria, checkpoints."""

    @property
    def name(self) -> str:
        return "planner"

    def run(self, workspace: AgentWorkspace) -> AgentWorkspace:
        self._emit_trace(workspace, "agent_started", {"agent": self.name})
        try:
            plan_dict = _get_plan_for_goal(workspace.goal, workspace.state.context)
            steps = plan_dict.get("steps") or []
            workspace.plan = {"steps": steps, "acceptance_criteria": plan_dict.get("acceptance_criteria")}
            workspace.state.current_plan = workspace.plan
            self._emit_trace(workspace, "agent_completed", {"agent": self.name, "steps_count": len(steps)})
        except Exception as e:
            self._emit_trace(workspace, "agent_failed", {"agent": self.name, "error": str(e)})
            workspace.plan = {"steps": [{"id": 1, "action": "SEARCH", "description": workspace.goal[:200]}]}
            workspace.state.current_plan = workspace.plan
        return workspace
