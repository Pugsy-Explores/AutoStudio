"""Localization agent: identify relevant files/symbols via retrieval pipeline + dispatch."""

from agent.roles.base_role_agent import BaseRoleAgent
from agent.roles.workspace import AgentWorkspace


class LocalizationAgent(BaseRoleAgent):
    """Uses SEARCH dispatch to find candidate files and symbols."""

    @property
    def name(self) -> str:
        return "localization"

    def run(self, workspace: AgentWorkspace) -> AgentWorkspace:
        self._emit_trace(workspace, "agent_started", {"agent": self.name})
        try:
            # Build search query from goal and plan
            goal = workspace.goal
            steps = workspace.plan.get("steps") or []
            first_search = next(
                (s for s in steps if isinstance(s, dict) and (s.get("action") or "").upper() == "SEARCH"),
                None,
            )
            query = (first_search.get("description") if first_search else "") or goal[:300]

            step = {"action": "SEARCH", "description": query, "id": 1}
            workspace.state.context["current_step_id"] = 1
            result = self._dispatch(workspace, step)

            if result.get("success") and result.get("output"):
                out = result.get("output") or {}
                results = out.get("results") or []
                workspace.candidate_files = list(
                    {r.get("file") or r.get("path") for r in results if r and (r.get("file") or r.get("path"))}
                )
                workspace.candidate_symbols = [
                    r.get("symbol") for r in results if r and r.get("symbol")
                ]
                workspace.state.context["search_memory"] = {"query": query, "results": results}
            self._emit_trace(
                workspace,
                "agent_completed",
                {"agent": self.name, "candidate_files": len(workspace.candidate_files)},
            )
        except Exception as e:
            self._emit_trace(workspace, "agent_failed", {"agent": self.name, "error": str(e)})
        return workspace
