"""Add a review layer before PR submission.

Checks: style violations, security risks, large diffs, missing tests.
"""

import json
import logging

from config.agent_config import MAX_PATCH_LINES
from agent.models.model_client import call_reasoning_model, call_small_model
from agent.models.model_router import get_model_for_task
from agent.models.model_types import ModelType
from agent.observability.trace_logger import log_event

logger = logging.getLogger(__name__)


def _count_patch_lines(patches: list) -> int:
    """Count total lines across all patches."""
    total = 0
    for p in patches or []:
        if isinstance(p, dict):
            diff = p.get("diff", "") or p.get("patch", "") or ""
        elif hasattr(p, "diff"):
            diff = getattr(p, "diff", "") or ""
        else:
            diff = str(p)
        total += len(diff.splitlines())
    return total


def review_patch(
    patches: list,
    test_results: dict | None,
    trace_id: str | None = None,
) -> dict:
    """
    Review patch for style, security, large diffs, missing tests.

    Args:
        patches: List of patch dicts
        test_results: {"status": "PASS"|"FAIL", ...}
        trace_id: Optional trace ID for log_event

    Returns:
        {valid: bool, issues: list, summary: str}
    """
    patch_lines = _count_patch_lines(patches)
    has_tests = (test_results or {}).get("status") == "PASS" or bool((test_results or {}).get("stdout"))

    prompt = f"""Review this code change.

Patch summary: {len(patches or [])} file(s), ~{patch_lines} lines
Tests: {"PASS" if has_tests else "FAIL/unknown"}

Check for: style violations, security risks, large diffs (> {MAX_PATCH_LINES} lines), missing tests.

Return JSON only:
{{"valid": true|false, "issues": ["issue1", "issue2"], "summary": "brief review summary"}}"""

    try:
        model_type = get_model_for_task("code_review")
        if model_type == ModelType.SMALL:
            out = call_small_model(prompt, task_name="code_review", max_tokens=512)
        else:
            out = call_reasoning_model(prompt, task_name="code_review", max_tokens=512)
        out = (out or "").strip()
        idx = out.find("{")
        if idx >= 0:
            end = out.rfind("}")
            if end > idx:
                obj = json.loads(out[idx : end + 1])
                issues = obj.get("issues") or []
                if patch_lines > MAX_PATCH_LINES:
                    issues.insert(0, f"Large diff: {patch_lines} lines (max {MAX_PATCH_LINES})")
                if not has_tests and patches:
                    issues.append("No tests or tests failed")
                valid = obj.get("valid", True) and len(issues) == 0
                result = {
                    "valid": valid,
                    "issues": issues[:10],
                    "summary": str(obj.get("summary", "") or "").strip(),
                }
                if trace_id:
                    log_event(trace_id, "review_completed", {"valid": valid, "issues_count": len(issues)})
                return result
    except Exception as e:
        logger.warning("[code_review_agent] review_patch failed: %s", e)

    issues = []
    if patch_lines > MAX_PATCH_LINES:
        issues.append(f"Large diff: {patch_lines} lines")
    if not has_tests and patches:
        issues.append("No tests or tests failed")
    result = {"valid": len(issues) == 0, "issues": issues, "summary": "Review could not be completed."}
    if trace_id:
        log_event(trace_id, "review_completed", {"valid": result["valid"], "issues_count": len(issues)})
    return result
