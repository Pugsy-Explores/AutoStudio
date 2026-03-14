"""Query rewriter: optimize user query for code search."""

import json
import logging
import re
from typing import TypedDict

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

# More realistic filler words for engineering queries
_STOPWORDS = {
    "locate",
    "find",
    "where",
    "code",
    "implementation",
    "function",
    "logic",
    "please",
    "show",
    "tell",
    "the",
    "in",
    "this",
}

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


def _split_identifiers(text: str) -> str:
    """
    Split camelCase and snake_case identifiers into tokens.
    """
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    text = text.replace("_", " ")
    return text


def _tokenize(text: str):
    """
    Extract useful tokens for code search.
    """
    tokens = re.findall(r"[A-Za-z0-9_]+", text.lower())
    return tokens


def _remove_stopwords(tokens):
    return [t for t in tokens if t not in _STOPWORDS]


def _dedupe(tokens):
    seen = set()
    out = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _rewrite_with_regex(text: str) -> str:
    """
    Heuristic rewrite for code search queries.
    """
    if not text or not isinstance(text, str):
        return ""

    text = _split_identifiers(text)

    tokens = _tokenize(text)

    tokens = _remove_stopwords(tokens)

    tokens = _dedupe(tokens)

    # Keep query short for search tools
    tokens = tokens[:6]

    return " ".join(tokens)


def rewrite_query_with_context(
    planner_step: str,
    user_request: str = "",
    previous_attempts: list[SearchAttempt] | None = None,
    use_llm: bool = True,
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
        print("[workflow] rewriter (heuristic)")
        out = _rewrite_with_regex(planner_step)
        print("    [workflow] rewriter planner_step:", (planner_step[:80] + "..." if len(planner_step) > 80 else planner_step))
        print("    [workflow] rewriter output:", out)
        return out

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
        heuristic rewrite.

    For SEARCH steps with retry/feedback, use rewrite_query_with_context instead.
    """

    if not text or not isinstance(text, str):
        return ""

    if not use_llm:
        print("[workflow] rewriter (heuristic)")
        out = _rewrite_with_regex(text)
        print("    [workflow] rewriter query:", (text[:80] + "..." if len(text) > 80 else text))
        print("    [workflow] rewriter output:", out)
        return out

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