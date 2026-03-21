"""
Query quality audit for search retrieval (LLM-based, optional, structured).

evaluate_search_query(instruction, query) returns structured scores for offline analysis.
Does NOT rewrite the query.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from agent.models.model_client import call_small_model

logger = logging.getLogger(__name__)

_SYSTEM = """You are auditing search query quality for a code retrieval system."""

_USER_TEMPLATE = """Instruction:
{instruction}

Search query:
{query}

Evaluate:

1. SUBJECT CLARITY (0-2)
2. LOCATION SIGNAL (0-2)
3. RELATION INTENT (0-2)
4. GENERIC (true/false)
5. TOO NARROW (true/false)

Return JSON:
{{
  "subject": int,
  "location": int,
  "relation": int,
  "generic": bool,
  "narrow": bool,
  "diagnosis": "short explanation"
}}

Do NOT rewrite the query."""


def _parse_json(raw: str) -> dict[str, Any] | None:
    s = (raw or "").strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", s)
    if m:
        s = m.group(1).strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError as e:
        logger.debug("[search_query_audit] JSON parse failed: %s", e)
        return None


def evaluate_search_query(instruction: str, query: str) -> dict[str, Any]:
    """
    Use LLM (task=evaluation) to audit search query quality.
    Returns: subject, location, relation, generic, narrow, diagnosis.
    On failure, returns defaults with error.
    """
    user_prompt = _USER_TEMPLATE.format(
        instruction=(instruction or "")[:500],
        query=(query or "")[:300],
    )
    try:
        raw = call_small_model(
            user_prompt,
            system_prompt=_SYSTEM,
            task_name="evaluation",
            max_tokens=400,
        )
        parsed = _parse_json(raw or "")
        if parsed:
            out: dict[str, Any] = {
                "subject": max(0, min(2, int(parsed.get("subject", 0) or 0))),
                "location": max(0, min(2, int(parsed.get("location", 0) or 0))),
                "relation": max(0, min(2, int(parsed.get("relation", 0) or 0))),
                "generic": bool(parsed.get("generic", False)),
                "narrow": bool(parsed.get("narrow", False)),
                "diagnosis": str(parsed.get("diagnosis", ""))[:200],
            }
            out["query_score"] = out["subject"] + out["location"] + out["relation"]
            return out
    except Exception as e:
        logger.debug("[search_query_audit] evaluator failed: %s", e)
    return {
        "subject": 0,
        "location": 0,
        "relation": 0,
        "generic": False,
        "narrow": False,
        "diagnosis": "audit_failed",
        "query_score": 0,
        "error": "audit_failed",
    }
