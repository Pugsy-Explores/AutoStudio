"""Test agent: run tests via INFRA dispatch (terminal_adapter), return PASS/FAIL/ERROR."""

from agent.memory.step_result import StepResult
from agent.roles.base_role_agent import BaseRoleAgent
from agent.roles.workspace import AgentWorkspace


class TestAgent(BaseRoleAgent):
    """Uses INFRA dispatch to run test command (e.g. pytest)."""

    @property
    def name(self) -> str:
        return "test"

    def run(self, workspace: AgentWorkspace) -> AgentWorkspace:
        self._emit_trace(workspace, "agent_started", {"agent": self.name})
        try:
            # Determine test command from plan or default
            steps = workspace.plan.get("steps") or []
            first_infra = next(
                (s for s in steps if isinstance(s, dict) and (s.get("action") or "").upper() == "INFRA"),
                None,
            )
            cmd = (first_infra.get("description") if first_infra else "") or "pytest -x -q"
            if not cmd.strip():
                cmd = "pytest -x -q"

            step = {"action": "INFRA", "description": cmd, "id": 3}
            step_id = len(workspace.state.completed_steps) + 1
            workspace.state.context["current_step_id"] = step_id
            result = self._dispatch(workspace, step)
            out = result.get("output") or {}
            run_out = out.get("run_command") or {}
            rc = run_out.get("returncode", -1) if isinstance(run_out, dict) else -1
            sr = StepResult(
                step_id=step_id,
                action="INFRA",
                success=result.get("success", False),
                output=out,
                latency_seconds=0,
                error=result.get("error"),
                classification=result.get("classification"),
            )
            workspace.state.record(step, sr)

            if isinstance(run_out, dict):
                stdout = run_out.get("stdout", "")
                stderr = run_out.get("stderr", "")
                rc = run_out.get("returncode", rc)
            else:
                stdout = ""
                stderr = ""

            if rc == 0:
                status = "PASS"
            elif rc < 0 or rc > 128:
                status = "ERROR"
            else:
                status = "FAIL"

            workspace.test_results = {
                "status": status,
                "stdout": stdout,
                "stderr": stderr,
                "returncode": rc,
            }
            self._emit_trace(workspace, "agent_completed", {"agent": self.name, "status": status})
        except Exception as e:
            self._emit_trace(workspace, "agent_failed", {"agent": self.name, "error": str(e)})
            workspace.test_results = {"status": "ERROR", "stdout": "", "stderr": str(e), "returncode": -1}
        return workspace
