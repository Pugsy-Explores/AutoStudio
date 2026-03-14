"""Query rewriter: optimize user query for code search."""

import json
import logging
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from agent.memory.state import AgentState

from agent.models.model_client import call_reasoning_model, call_small_model
from agent.models.model_router import get_model_for_task
from agent.models.model_types import ModelType
from agent.prompts import get_prompt

logger = logging.getLogger(__name__)

# How many previous search attempts to include in the rewrite prompt (keep small for focus)
MAX_ATTEMPT_HISTORY_FOR_REWRITE = 3


class SearchAttempt(TypedDict, total=False):
    """One search attempt: query used and a short result summary for the rewriter."""

    query: str
    result_count: int
    result_summary: str
    error: str


class RewriteResult(TypedDict, total=False):
    """Structured output from context rewrite: tool, query, reason."""

    tool: str
    query: str
    reason: str

# Load once at module import
_REWRITE_PROMPT = get_prompt("query_rewrite", "prompt")
_CTX_PROMPTS = get_prompt("query_rewrite_with_context")
_REWRITE_WITH_CONTEXT_MAIN = _CTX_PROMPTS["main"]
_REWRITE_WITH_CONTEXT_END = _CTX_PROMPTS["end"]


def _parse_rewrite_json(raw: str) -> RewriteResult:
    """Parse JSON output { tool, query, reason }; return structured result; fallback to query-only."""
    raw = raw.strip()
    # Strip markdown code fence if present
    if raw.startswith("```"):
        lines = raw.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines)
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            query = (obj.get("query") or "").strip() or raw
            return RewriteResult(
                tool=(obj.get("tool") or "").strip() or "",
                query=query,
                reason=(obj.get("reason") or "").strip() or "",
            )
    except (json.JSONDecodeError, TypeError):
        pass
    return RewriteResult(tool="", query=raw, reason="")


# Common filler words to strip in heuristic (no-LLM) mode for token-like queries
_HEURISTIC_FILLER_WORDS = frozenset(
    {"find", "where", "the", "is", "a", "an", "to", "of", "for", "in", "on", "at"}
)


def _heuristic_rewrite_no_llm(text: str) -> str:
    """Strip filler words to produce token-like query for code search (no LLM)."""
    if not text or not isinstance(text, str):
        return ""
    stripped = text.strip()
    if not stripped:
        return ""
    tokens = stripped.split()
    filtered = [t for t in tokens if t.lower() not in _HEURISTIC_FILLER_WORDS]
    return " ".join(filtered) if filtered else stripped


def _format_attempts_for_prompt(attempts: list[SearchAttempt]) -> str:
    """Format previous search attempts for the rewrite prompt (last N only)."""
    if not attempts:
        return "(none yet)"
    limited = attempts[-MAX_ATTEMPT_HISTORY_FOR_REWRITE:]
    lines = []
    for i, a in enumerate(limited, start=1):
        q = a.get("query", "")
        count = a.get("result_count", 0)
        summary = a.get("result_summary", "")
        err = a.get("error", "")
        if err:
            lines.append(f"  {i}. Query: \"{q}\" → Error: {err}")
        elif summary:
            lines.append(f"  {i}. Query: \"{q}\" → {summary}")
        else:
            lines.append(f"  {i}. Query: \"{q}\" → {count} result(s)")
    return "\n".join(lines) if lines else "(none yet)"


def rewrite_query_with_context(
    planner_step: str,
    user_request: str = "",
    previous_attempts: list[SearchAttempt] | None = None,
    use_llm: bool = True,
    state: "AgentState | None" = None,
) -> str:
    """
    Rewrite the planner step into a search query using full execution context.

    Receives: planner step description, original user request, and recent search
    attempts (query + result count/summary). Uses this to avoid repeating failed
    queries and to refine toward identifier-style queries.
    """
    if not planner_step or not isinstance(planner_step, str):
        return ""

    attempts = previous_attempts or []
    attempts_slice = attempts[-MAX_ATTEMPT_HISTORY_FOR_REWRITE:]

    if not use_llm:
        return _heuristic_rewrite_no_llm(planner_step)

    print("[workflow] rewriter (with context)")
    print("    [workflow] rewriter planner_step:", (planner_step[:80] + "..." if len(planner_step) > 80 else planner_step))
    print("    [workflow] rewriter user_request:", (user_request[:80] + "..." if len(user_request) > 80 else user_request) if user_request else "(none)")
    print("    [workflow] rewriter previous_attempts:", len(attempts_slice))
    try:
        model_type = get_model_for_task("query rewriting")
        model_name = "REASONING" if model_type == ModelType.REASONING else "SMALL"
        print("    [workflow] rewriter model:", model_name)

        prompt = _REWRITE_WITH_CONTEXT_MAIN.format(
            previous_attempts=_format_attempts_for_prompt(attempts_slice),
            planner_step=(planner_step or "").strip(),
        )
        prompt += _REWRITE_WITH_CONTEXT_END

        if model_type == ModelType.REASONING:
            out = call_reasoning_model(prompt, task_name="query rewriting")
        else:
            out = call_small_model(prompt, task_name="query rewriting")

        raw = (out or "").strip()
        if not raw:
            raise ValueError("Query rewrite returned empty response")
        result = _parse_rewrite_json(raw)
        query = (result.get("query") or "").strip() or raw
        tool = (result.get("tool") or "").strip()
        reason = (result.get("reason") or "").strip()
        print("    [workflow] rewriter tool:", tool or "(none)")
        print("    [workflow] rewriter query:", query)
        if reason:
            print("    [workflow] rewriter reason:", reason)
        logger.info(
            "rewriter result: tool=%s query=%s reason=%s",
            tool or "(none)",
            query[:80] + ("..." if len(query) > 80 else ""),
            reason[:80] + ("..." if len(reason) > 80 else "") if reason else "(none)",
        )
        # Wire rewriter tool choice to chosen_tool for retrieval order
        if state and tool and tool in ("retrieve_graph", "retrieve_vector", "retrieve_grep", "list_dir"):
            state.context["chosen_tool"] = tool
        return query

    except Exception as e:
        logger.warning("LLM query rewrite with context failed: %s", e)
        raise


def rewrite_query(text: str, use_llm: bool = False) -> str:
    """
    Rewrite user query for code search (no execution context).

    If use_llm=True:
        config task_models["query rewriting"] → chosen model rewrites query.

    Otherwise:
        return text stripped (passthrough).

    For SEARCH steps with retry/feedback, use rewrite_query_with_context instead.
    """

    if not text or not isinstance(text, str):
        return ""

    if not use_llm:
        return text.strip()

    print("[workflow] rewriter")
    print("    [workflow] rewriter query:", (text[:80] + "..." if len(text) > 80 else text))
    try:
        model_type = get_model_for_task("query rewriting")
        model_name = "REASONING" if model_type == ModelType.REASONING else "SMALL"
        print("    [workflow] rewriter model:", model_name)

        prompt = _REWRITE_PROMPT.format(text=text.strip())

        if model_type == ModelType.REASONING:
            out = call_reasoning_model(prompt, task_name="query rewriting")
        else:
            out = call_small_model(prompt, task_name="query rewriting")

        cleaned = (out or "").strip()
        if not cleaned:
            raise ValueError("Query rewrite returned empty response")
        print("    [workflow] rewriter output:", cleaned)
        return cleaned

    except Exception as e:
        logger.warning("LLM query rewrite failed: %s", e)
        raise