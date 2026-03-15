"""Convert GitHub/GitLab issues into structured tasks.

Pipeline: issue text -> intent classifier -> symbol detection -> structured task.
"""

import json
import logging

from agent.models.model_client import call_small_model
from agent.models.model_router import get_model_for_task
from agent.models.model_types import ModelType
from agent.observability.trace_logger import log_event

logger = logging.getLogger(__name__)

TASK_TYPES = frozenset({"bug_fix", "feature_request", "refactor", "documentation", "unknown"})
PRIORITIES = frozenset({"low", "medium", "high", "critical"})


def parse_issue(issue_text: str, trace_id: str | None = None) -> dict:
    """
    Convert issue text into a structured task.

    Args:
        issue_text: Raw issue body/title from GitHub/GitLab
        trace_id: Optional trace ID for log_event

    Returns:
        Structured task dict: {type, module, symbol, priority, description}
    """
    prompt = f"""Parse this issue into a structured task.

Issue:
{issue_text[:2000]}

Return JSON only:
{{"type": "bug_fix|feature_request|refactor|documentation|unknown", "module": "string or empty", "symbol": "string or empty", "priority": "low|medium|high|critical", "description": "brief summary"}}"""

    try:
        model_type = get_model_for_task("issue_parsing")
        if model_type == ModelType.SMALL:
            out = call_small_model(prompt, task_name="issue_parsing", max_tokens=512)
        else:
            from agent.models.model_client import call_reasoning_model

            out = call_reasoning_model(prompt, task_name="issue_parsing", max_tokens=512)
        out = (out or "").strip()
        idx = out.find("{")
        if idx >= 0:
            end = out.rfind("}")
            if end > idx:
                obj = json.loads(out[idx : end + 1])
                task_type = str(obj.get("type", "unknown")).strip().lower()
                if task_type not in TASK_TYPES:
                    task_type = "unknown"
                priority = str(obj.get("priority", "medium")).strip().lower()
                if priority not in PRIORITIES:
                    priority = "medium"
                result = {
                    "type": task_type,
                    "module": str(obj.get("module", "") or "").strip(),
                    "symbol": str(obj.get("symbol", "") or "").strip(),
                    "priority": priority,
                    "description": str(obj.get("description", "") or issue_text[:200]).strip(),
                }
                if trace_id:
                    log_event(trace_id, "issue_parsed", result)
                return result
    except Exception as e:
        logger.warning("[issue_parser] parse_issue failed: %s", e)

    result = {
        "type": "unknown",
        "module": "",
        "symbol": "",
        "priority": "medium",
        "description": issue_text[:200] if issue_text else "",
    }
    if trace_id:
        log_event(trace_id, "issue_parsed", result)
    return result
