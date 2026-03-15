"""Query rewriter: optimize user query for code search."""

import json
import logging
import re
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from agent.memory.state import AgentState

from agent.models.model_client import call_reasoning_model, call_small_model
from agent.models.model_router import get_model_for_task
from agent.models.model_types import ModelType
from agent.prompt_system import get_registry

logger = logging.getLogger(__name__)

# How many previous search attempts to include in the rewrite prompt (keep small for focus)
MAX_ATTEMPT_HISTORY_FOR_REWRITE = 3


class SearchAttempt(TypedDict, total=False):
    """One search attempt: tool, argument (query), and outcome for the rewriter."""

    tool: str
    query: str
    result_count: int
    result_summary: str
    error: str


class RewriteResult(TypedDict, total=False):
    """Structured output from context rewrite: tool, query, reason, optional queries."""

    tool: str
    query: str
    reason: str
    queries: list[str]

# Load once at module import
def _get_rewrite_prompt(text: str) -> str:
    return get_registry().get_instructions("query_rewrite", variables={"text": text})


def _get_ctx_prompts() -> tuple[str, str]:
    t = get_registry().get("query_rewrite_with_context")
    if t.extra:
        return t.extra.get("main", ""), t.extra.get("end", "")
    return t.instructions, ""


def _extract_json_from_text(text: str) -> dict | None:
    """Extract first valid JSON object from text (handles reasoning-before-JSON output)."""
    if not text or not text.strip():
        return None
    text = text.strip()
    # Try direct parse first
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except (json.JSONDecodeError, TypeError):
        pass
    # Strip markdown code fence
    if "```" in text:
        match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
        if match:
            try:
                obj = json.loads(match.group(1).strip())
                return obj if isinstance(obj, dict) else None
            except (json.JSONDecodeError, TypeError):
                pass
    # Find last {...} (model often outputs reasoning then JSON at end)
    start = text.rfind("{")
    if start >= 0:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start : i + 1])
                        return obj if isinstance(obj, dict) else None
                    except (json.JSONDecodeError, TypeError):
                        break
    return None


def _parse_rewrite_json(raw: str) -> RewriteResult:
    """Parse JSON output { tool, query, reason, queries? }; return structured result; fallback to query-only."""
    raw = raw.strip()
    obj = _extract_json_from_text(raw)
    if obj:
        query = (obj.get("query") or "").strip() or raw
        queries_raw = obj.get("queries")
        queries: list[str] = []
        if isinstance(queries_raw, list):
            queries = [(q or "").strip() for q in queries_raw if isinstance(q, str) and (q or "").strip()]
        result: RewriteResult = RewriteResult(
            tool=(obj.get("tool") or "").strip() or "",
            query=query,
            reason=(obj.get("reason") or "").strip() or "",
        )
        if queries:
            result["queries"] = queries
        return result
    return RewriteResult(tool="", query=raw, reason="")


# Common filler words to strip in heuristic (no-LLM) mode for token-like queries
# Aligns with prompt rules: "Extract main technical concepts; remove filler"
_HEURISTIC_FILLER_WORDS = frozenset(
    {
        "find", "where", "the", "is", "a", "an", "to", "of", "for", "in", "on", "at",
        "locate", "show", "get", "code", "please", "can", "you", "me",
    }
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
    """Format previous attempts as tool(arg) → outcome for compact rewriter context."""
    if not attempts:
        return "(none yet)"
    limited = attempts[-MAX_ATTEMPT_HISTORY_FOR_REWRITE:]
    lines = []
    for a in limited:
        tool = (a.get("tool") or "").strip() or "?"
        arg = (a.get("query") or "").strip()[:120]
        err = (a.get("error") or "").strip()[:100]
        summary = (a.get("result_summary") or "").strip()[:80]
        if err:
            outcome = f"Error: {err}"
        elif summary:
            outcome = summary
        else:
            outcome = f"{a.get('result_count', 0)} result(s)"
        lines.append(f'{tool}({arg!r}) → {outcome}')
    return "\n".join(lines) if lines else "(none yet)"


def rewrite_query_with_context(
    planner_step: str,
    user_request: str = "",
    previous_attempts: list[SearchAttempt] | None = None,
    use_llm: bool = True,
    state: "AgentState | None" = None,
) -> str | list[str]:
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

        # Truncate long inputs to avoid token overflow; escape braces in user content
        # (format() substitutes values as-is; braces in values are safe)
        planner_step_safe = (planner_step or "").strip()[:2000]
        previous_attempts_safe = _format_attempts_for_prompt(attempts_slice)[:1500]
        user_request_safe = (user_request or "").strip()[:500]
        main, end = _get_ctx_prompts()
        prompt = main.format(
            user_request=user_request_safe or "(none)",
            previous_attempts=previous_attempts_safe,
            planner_step=planner_step_safe,
        )
        prompt += end

        _REWRITE_SYSTEM = get_registry().get_instructions("query_rewrite_system")

        if model_type == ModelType.REASONING:
            out = call_reasoning_model(
                prompt,
                system_prompt=_REWRITE_SYSTEM,
                task_name="query rewriting",
                prompt_name="query_rewrite_with_context",
            )
        else:
            out = call_small_model(
                prompt,
                task_name="query rewriting",
                system_prompt=_REWRITE_SYSTEM,
                prompt_name="query_rewrite_with_context",
            )

        raw = (out or "").strip()
        if not raw:
            raise ValueError("Query rewrite returned empty response")
        result = _parse_rewrite_json(raw)
        query = (result.get("query") or "").strip() or raw
        queries = result.get("queries") or []
        tool = (result.get("tool") or "").strip()
        reason = (result.get("reason") or "").strip()
        print("    [workflow] rewriter tool:", tool or "(none)")
        print("    [workflow] rewriter query:", query)
        if queries:
            print("    [workflow] rewriter queries:", queries[:5], "..." if len(queries) > 5 else "")
        if reason:
            print("    [workflow] rewriter reason:", reason)
        logger.info(
            "rewriter result: tool=%s query=%s queries=%s reason=%s",
            tool or "(none)",
            query[:80] + ("..." if len(query) > 80 else ""),
            len(queries) if queries else 0,
            reason[:80] + ("..." if len(reason) > 80 else "") if reason else "(none)",
        )
        # Wire rewriter tool choice to chosen_tool for retrieval order
        if state and tool and tool in ("retrieve_graph", "retrieve_vector", "retrieve_grep", "list_dir"):
            state.context["chosen_tool"] = tool
        # Return query variants when present; policy engine will try each until success
        if queries:
            return queries
        return query

    except Exception as e:
        logger.warning("LLM query rewrite with context failed: %s", e)
        # Fallback to heuristic so SEARCH can proceed and produce results for replanner
        fallback = _heuristic_rewrite_no_llm(planner_step)
        if fallback:
            return fallback
        # Last resort: return stripped planner_step so policy engine can use description
        return (planner_step or "").strip()


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

        prompt = _get_rewrite_prompt(text.strip())

        # No prompt_name: query_rewrite returns plain text, not JSON; output_schema_guard would fail
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
        # Fallback: return stripped text so caller can proceed
        return text.strip()