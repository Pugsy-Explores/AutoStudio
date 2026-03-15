"""Supervisor agent: coordinates role agents, enforces safety limits."""

import logging
import time
import uuid

from agent.observability.trace_logger import finish_trace, log_event, start_trace
from agent.roles.edit_agent import EditAgent
from agent.roles.localization_agent import LocalizationAgent
from agent.roles.planner_agent import PlannerAgent
from agent.roles.test_agent import TestAgent
from agent.roles.workspace import AgentWorkspace

logger = logging.getLogger(__name__)

# Phase 9 safety limits (from roadmap)
MAX_AGENT_STEPS = 30
MAX_PATCH_ATTEMPTS = 3
MAX_RUNTIME_SECONDS = 120
MAX_FILE_EDITS = 10



def run_multi_agent(
    goal: str,
    project_root: str | None = None,
    *,
    success_criteria: str | None = None,
) -> dict:
    """
    Run Phase 9 hierarchical multi-agent flow.
    Supervisor -> Planner -> Localization -> Edit -> Test -> (Critic if fail) -> retry.
    Returns summary with goal_success, patches, test_results, agents_used, etc.
    """
    import os
    from pathlib import Path

    from agent.roles.critic_agent import CriticAgent

    root = Path(project_root or os.environ.get("SERENA_PROJECT_DIR", os.getcwd())).resolve()
    task_id = str(uuid.uuid4())
    trace_id = start_trace(task_id, str(root), query=goal)

    workspace = AgentWorkspace.from_goal(goal, str(root), trace_id)

    from config.repo_intelligence_config import (
        MAX_ARCHITECTURE_NODES,
        MAX_CONTEXT_TOKENS,
        MAX_IMPACT_DEPTH,
        MAX_REPO_SCAN_FILES,
    )

    log_event(
        trace_id,
        "multi_agent_start",
        {
            "goal": goal[:200],
            "limits": {
                "max_agent_steps": MAX_AGENT_STEPS,
                "max_patch_attempts": MAX_PATCH_ATTEMPTS,
                "max_runtime": MAX_RUNTIME_SECONDS,
                "max_file_edits": MAX_FILE_EDITS,
                "max_repo_scan_files": MAX_REPO_SCAN_FILES,
                "max_architecture_nodes": MAX_ARCHITECTURE_NODES,
                "max_context_tokens": MAX_CONTEXT_TOKENS,
                "max_impact_depth": MAX_IMPACT_DEPTH,
            },
        },
    )

    start_time = time.perf_counter()
    agent_steps = 0
    patch_attempts = 0
    file_edits = 0
    agents_used: list[str] = []

    planner_agent = PlannerAgent()
    localization_agent = LocalizationAgent()
    edit_agent = EditAgent()
    test_agent = TestAgent()
    critic_agent = CriticAgent()

    try:
        # Phase 10: Repo intelligence layer (before planner)
        try:
            from agent.repo_intelligence.architecture_map import build_architecture_map
            from agent.repo_intelligence.repo_summary_graph import build_repo_summary_graph

            repo_summary = build_repo_summary_graph(str(root))
            workspace.state.context["repo_summary"] = repo_summary
            arch_map = build_architecture_map(repo_summary)
            workspace.state.context["architecture_map"] = arch_map
            log_event(trace_id, "repo_intelligence", {"modules": len(repo_summary.get("modules", []))})
        except Exception as e:
            logger.warning("repo_intelligence failed: %s", e)
            workspace.state.context.setdefault("repo_summary", {})
            workspace.state.context.setdefault("architecture_map", {})

        # 1. Planner
        if agent_steps >= MAX_AGENT_STEPS:
            _finish_fail(trace_id, workspace, "max_agent_steps", start_time)
            return _result(workspace, False, agents_used, start_time)
        log_event(trace_id, "handoff", {"from": "supervisor", "to": "planner"})
        workspace = planner_agent.run(workspace)
        agent_steps += 1
        agents_used.append("planner")

        # 2. Localization
        if agent_steps >= MAX_AGENT_STEPS:
            _finish_fail(trace_id, workspace, "max_agent_steps", start_time)
            return _result(workspace, False, agents_used, start_time)
        log_event(trace_id, "handoff", {"from": "planner", "to": "localization"})
        workspace = localization_agent.run(workspace)
        agent_steps += 1
        agents_used.append("localization")

        # 3. Edit -> Test loop (with critic on failure)
        while patch_attempts < MAX_PATCH_ATTEMPTS:
            if time.perf_counter() - start_time > MAX_RUNTIME_SECONDS:
                log_event(trace_id, "multi_agent_stop", {"reason": "max_runtime"})
                break
            if agent_steps >= MAX_AGENT_STEPS:
                break

            # Edit
            log_event(trace_id, "handoff", {"from": "localization" if patch_attempts == 0 else "critic", "to": "edit"})
            workspace = edit_agent.run(workspace)
            agent_steps += 1
            agents_used.append("edit")
            patch_attempts += 1

            # Phase 10: Impact analysis after edit
            edited_files: list[str] = []
            for sr in workspace.state.step_results:
                if sr.files_modified:
                    fm = sr.files_modified
                    edited_files.extend(fm if isinstance(fm, list) else [fm])
            if edited_files:
                try:
                    from agent.repo_intelligence.impact_analyzer import analyze_impact

                    impact = analyze_impact(edited_files[0], str(root))
                    workspace.state.context["impact_result"] = impact
                    log_event(trace_id, "impact_analysis", {"affected_files": len(impact.get("affected_files", []))})
                except Exception as e:
                    logger.warning("impact_analyzer failed: %s", e)

            # Count edits from state
            for sr in workspace.state.step_results:
                if sr.files_modified:
                    file_edits += len(sr.files_modified) if isinstance(sr.files_modified, list) else 1
            if file_edits >= MAX_FILE_EDITS:
                log_event(trace_id, "multi_agent_stop", {"reason": "max_file_edits"})
                break

            # Test
            if agent_steps >= MAX_AGENT_STEPS:
                break
            log_event(trace_id, "handoff", {"from": "edit", "to": "test"})
            workspace = test_agent.run(workspace)
            agent_steps += 1
            agents_used.append("test")

            tr = workspace.test_results
            if tr and tr.get("status") == "PASS":
                log_event(trace_id, "multi_agent_success", {"agents_used": agents_used})
                return _result(workspace, True, agents_used, start_time)

            # Failure: run critic for retry instruction
            if patch_attempts < MAX_PATCH_ATTEMPTS and agent_steps < MAX_AGENT_STEPS:
                log_event(trace_id, "handoff", {"from": "test", "to": "critic"})
                workspace = critic_agent.run(workspace)
                agent_steps += 1
                agents_used.append("critic")

        _finish_fail(trace_id, workspace, "max_patch_attempts_or_steps", start_time)
        return _result(workspace, False, agents_used, start_time)

    except Exception as e:
        logger.exception("run_multi_agent failed: %s", e)
        log_event(trace_id, "agent_failed", {"agent": "supervisor", "error": str(e)})
        return _result(workspace, False, agents_used, start_time, error=str(e))
    finally:
        try:
            finish_trace(trace_id)
        except Exception:
            pass


def _finish_fail(trace_id: str, workspace: AgentWorkspace, reason: str, start_time: float) -> None:
    log_event(trace_id, "multi_agent_stop", {"reason": reason, "latency": time.perf_counter() - start_time})


def _result(
    workspace: AgentWorkspace,
    success: bool,
    agents_used: list[str],
    start_time: float,
    error: str | None = None,
) -> dict:
    out = {
        "goal": workspace.goal,
        "goal_success": success,
        "plan": workspace.plan,
        "candidate_files": workspace.candidate_files,
        "patches": workspace.patches,
        "test_results": workspace.test_results,
        "agents_used": agents_used,
        "agent_delegations": len(agents_used),
        "latency": time.perf_counter() - start_time,
        "error": error,
    }
    if workspace.state.context.get("impact_result"):
        out["impact_result"] = workspace.state.context["impact_result"]
    if workspace.state.context.get("context_compression_ratio") is not None:
        out["context_compression_ratio"] = workspace.state.context["context_compression_ratio"]
    return out
