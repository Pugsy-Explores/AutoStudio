"""Automatically create a clean PR description from workspace and patches."""

import logging

from agent.models.model_client import call_reasoning_model
from agent.observability.trace_logger import log_event

logger = logging.getLogger(__name__)


def generate_pr(
    workspace,
    patches: list,
    test_results: dict | None,
    trace_id: str | None = None,
) -> dict:
    """
    Generate PR title and description from workspace, patches, and test results.

    Args:
        workspace: AgentWorkspace with goal, plan, patches
        patches: List of patch dicts (or from workspace.patches)
        test_results: {"status": "PASS"|"FAIL", "stdout": str, ...}
        trace_id: Optional trace ID for log_event

    Returns:
        {title, description, files_modified, reasoning_summary, trace_ref}
    """
    goal = getattr(workspace, "goal", "") or ""
    plan = getattr(workspace, "plan", {}) or {}
    steps = plan.get("steps", [])[:10]
    patch_list = patches or getattr(workspace, "patches", []) or []
    files_modified = []
    for p in patch_list:
        if isinstance(p, dict) and p.get("path"):
            files_modified.append(p.get("path", ""))
        elif hasattr(p, "path"):
            files_modified.append(getattr(p, "path", ""))
    files_modified = list(dict.fromkeys(f)) if (f := files_modified) else []

    test_status = (test_results or {}).get("status", "UNKNOWN")
    test_stdout = (test_results or {}).get("stdout", "")[:500]

    prompt = f"""Generate a PR title and description for this change.

Goal: {goal}

Plan steps:
{chr(10).join(f"  - {s.get('action', '?')}: {(s.get('description') or '')[:80]}" for s in steps if isinstance(s, dict))}

Files modified: {', '.join(files_modified) or 'none'}
Test status: {test_status}
Test output (excerpt): {test_stdout[:300]}

Return JSON only:
{{"title": "short PR title", "description": "paragraph describing changes", "reasoning_summary": "brief reasoning"}}"""

    try:
        out = call_reasoning_model(prompt, task_name="pr_generation", max_tokens=1024)
        out = (out or "").strip()
        idx = out.find("{")
        if idx >= 0:
            end = out.rfind("}")
            if end > idx:
                import json

                obj = json.loads(out[idx : end + 1])
                result = {
                    "title": str(obj.get("title", "") or f"Fix: {goal[:60]}").strip(),
                    "description": str(obj.get("description", "") or "").strip(),
                    "files_modified": files_modified,
                    "reasoning_summary": str(obj.get("reasoning_summary", "") or "").strip(),
                    "trace_ref": trace_id or "",
                }
                if trace_id:
                    log_event(trace_id, "pr_generated", {"title": result["title"], "files_count": len(files_modified)})
                return result
    except Exception as e:
        logger.warning("[pr_generator] generate_pr failed: %s", e)

    result = {
        "title": f"Fix: {goal[:60]}" if goal else "PR",
        "description": f"Changes for: {goal}",
        "files_modified": files_modified,
        "reasoning_summary": "",
        "trace_ref": trace_id or "",
    }
    if trace_id:
        log_event(trace_id, "pr_generated", {"title": result["title"], "files_count": len(files_modified)})
    return result
