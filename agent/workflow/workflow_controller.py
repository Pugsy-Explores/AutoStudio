"""Orchestrate full workflow: issue -> task -> agent solve -> PR -> CI -> review."""

import json
import logging
import uuid
from pathlib import Path

from config.agent_config import MAX_FILES_PER_PR, MAX_PATCH_LINES
from agent.observability.trace_logger import finish_trace, log_event, start_trace
from agent.roles.supervisor_agent import run_multi_agent
from agent.workflow.ci_runner import run_ci
from agent.workflow.code_review_agent import review_patch
from agent.workflow.developer_feedback import apply_feedback
from agent.workflow.issue_parser import parse_issue
from agent.workflow.pr_generator import generate_pr

logger = logging.getLogger(__name__)


def run_workflow(
    issue_text: str,
    project_root: str,
    feedback: str | None = None,
) -> dict:
    """
    Full orchestration: issue -> parse -> agent solve -> PR -> CI -> review.

    Optionally applies developer feedback and re-runs agent.

    Args:
        issue_text: Raw issue body/title
        project_root: Project root path
        feedback: Optional developer feedback to apply (triggers re-run)

    Returns:
        Summary dict with task, pr, ci, review, goal_success, etc.
    """
    task_id = str(uuid.uuid4())
    trace_id = start_trace(task_id, project_root, query=issue_text)

    pr_data: dict = {}
    ci_result: dict = {"passed": False, "failures": [], "runtime_sec": 0}
    review_result: dict = {"valid": False, "issues": [], "summary": ""}
    patches: list = []
    result: dict = {}

    try:
        task = parse_issue(issue_text, trace_id)
        goal = task.get("description", "") or issue_text

        for attempt in range(2):
            result = run_multi_agent(goal, project_root=project_root)
            workspace = _workspace_from_result(result, goal, project_root, trace_id)

            patches = workspace.patches or []
            files_count = len(set(p.get("path", "") for p in patches if isinstance(p, dict) and p.get("path")))
            if files_count > MAX_FILES_PER_PR:
                log_event(
                    trace_id,
                    "workflow_safety",
                    {"reason": "max_files_exceeded", "files": files_count, "limit": MAX_FILES_PER_PR},
                )
                break

            total_lines = sum(
                len((p.get("diff", "") or p.get("patch", "") or "").splitlines())
                for p in patches
                if isinstance(p, dict)
            )
            if total_lines > MAX_PATCH_LINES:
                log_event(
                    trace_id,
                    "workflow_safety",
                    {"reason": "max_patch_lines_exceeded", "lines": total_lines, "limit": MAX_PATCH_LINES},
                )
                break

            pr_data = generate_pr(workspace, patches, workspace.test_results, trace_id)
            ci_result = run_ci(project_root, trace_id)
            review_result = review_patch(patches, workspace.test_results, trace_id)

            if feedback and attempt == 0:
                workspace = apply_feedback(feedback, workspace, trace_id)
                goal = workspace.retry_instruction or goal
                continue
            break

        out = {
            "task_id": task_id,
            "trace_id": trace_id,
            "task": task,
            "goal_success": result.get("goal_success", False),
            "pr": pr_data,
            "ci": ci_result,
            "review": review_result,
            "patches": patches,
            "agents_used": result.get("agents_used", []),
        }
        _save_last_workflow(out, project_root)
        return out
    except Exception as e:
        logger.exception("run_workflow failed: %s", e)
        log_event(trace_id, "workflow_failed", {"error": str(e)})
        return {
            "task_id": task_id,
            "trace_id": trace_id,
            "task": {},
            "goal_success": False,
            "pr": {},
            "ci": {"passed": False, "failures": [str(e)], "runtime_sec": 0},
            "review": {"valid": False, "issues": [str(e)], "summary": ""},
            "patches": [],
            "agents_used": [],
            "error": str(e),
        }
    finally:
        try:
            finish_trace(trace_id)
        except Exception:
            pass


def _save_last_workflow(result: dict, project_root: str) -> None:
    """Persist last workflow result for autostudio pr/review commands."""
    try:
        mem_dir = Path(project_root) / ".agent_memory"
        mem_dir.mkdir(parents=True, exist_ok=True)
        path = mem_dir / "last_workflow.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, default=str)
    except Exception as e:
        logger.debug("[workflow_controller] save_last_workflow failed: %s", e)


def load_last_workflow(project_root: str) -> dict | None:
    """Load last workflow result. Returns None if not found."""
    try:
        path = Path(project_root) / ".agent_memory" / "last_workflow.json"
        if path.exists():
            with open(path, encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.debug("[workflow_controller] load_last_workflow failed: %s", e)
    return None


def _workspace_from_result(result: dict, goal: str, project_root: str, trace_id: str):
    """Build a minimal workspace-like object from run_multi_agent result."""
    from agent.roles.workspace import AgentWorkspace

    ws = AgentWorkspace.from_goal(goal, project_root, trace_id)
    ws.goal = goal
    ws.plan = result.get("plan", {}) or {"steps": []}
    ws.patches = result.get("patches", []) or []
    ws.test_results = result.get("test_results")
    return ws
