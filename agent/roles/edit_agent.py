"""Edit agent: generate and apply patches via editing pipeline dispatch."""

from agent.memory.step_result import StepResult
from agent.roles.base_role_agent import BaseRoleAgent
from agent.roles.workspace import AgentWorkspace


class EditAgent(BaseRoleAgent):
    """Uses EDIT dispatch (diff_planner -> patch_executor) to apply edits."""

    @property
    def name(self) -> str:
        return "edit"

    def run(self, workspace: AgentWorkspace) -> AgentWorkspace:
        self._emit_trace(workspace, "agent_started", {"agent": self.name})
        try:
            # Build edit instruction from goal, plan, retry_instruction
            steps = workspace.plan.get("steps") or []
            first_edit = next(
                (s for s in steps if isinstance(s, dict) and (s.get("action") or "").upper() == "EDIT"),
                None,
            )
            base_instruction = (first_edit.get("description") if first_edit else "") or workspace.goal[:500]
            if workspace.retry_instruction:
                instruction = f"{base_instruction}\n\nRetry hint: {workspace.retry_instruction}"
            else:
                instruction = base_instruction

            # Set edit_path from candidate_files for context
            if workspace.candidate_files:
                workspace.state.context["edit_path"] = workspace.candidate_files[0]

            step = {"action": "EDIT", "description": instruction, "id": 2}
            step_id = len(workspace.state.completed_steps) + 1
            workspace.state.context["current_step_id"] = step_id
            result = self._dispatch(workspace, step)
            out = result.get("output") or {}
            sr = StepResult(
                step_id=step_id,
                action="EDIT",
                success=result.get("success", False),
                output=out,
                latency_seconds=0,
                error=result.get("error"),
                classification=result.get("classification"),
                files_modified=out.get("files_modified") if isinstance(out, dict) else None,
                patch_size=out.get("patches_applied") if isinstance(out, dict) else None,
            )
            workspace.state.record(step, sr)

            if result.get("success") and result.get("output"):
                out = result.get("output") or {}
                if isinstance(out, dict):
                    workspace.patches.append(out)
            self._emit_trace(workspace, "agent_completed", {"agent": self.name, "success": result.get("success")})
        except Exception as e:
            self._emit_trace(workspace, "agent_failed", {"agent": self.name, "error": str(e)})
        return workspace
