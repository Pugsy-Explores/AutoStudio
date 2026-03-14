"""Query rewriter: optimize user query for code search."""

import logging
import re
from typing import TypedDict

from agent.models.model_client import call_reasoning_model, call_small_model
from agent.models.model_router import get_model_for_task
from agent.models.model_types import ModelType

logger = logging.getLogger(__name__)

# How many previous search attempts to include in the rewrite prompt (keep small for focus)
MAX_ATTEMPT_HISTORY_FOR_REWRITE = 3


class SearchAttempt(TypedDict, total=False):
    """One search attempt: query used and a short result summary for the rewriter."""

    query: str
    result_count: int
    result_summary: str
    error: str

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

_REWRITE_PROMPT = """
You are writing/rewriting a user query for a code search system.

Available tools:
- find_symbol: searches for classes, functions, and variables
- search_for_pattern: searches for text inside files

Your task is to convert the natural language request into a query that
resembles real code identifiers or filenames.

Rules:
1. Extract the main technical concepts from the request.
2. Remove filler words such as: find, locate, show, where, code, implementation.
3. Prefer identifiers over natural language phrases.
4. Generate identifiers using common naming conventions:
   - PascalCase for classes
   - snake_case for functions or modules
5. If the query refers to logic or functionality, prefer snake_case.
6. If the query refers to a component or class, prefer PascalCase.
7. If appropriate, generate a likely file name (example: task_scheduler.py).
8. Identifiers should contain 1-3 meaningful words only.

Valid identifier formats include:
- PascalCase: ^[A-Z][a-zA-Z0-9]*$
- snake_case: ^[a-z]+(_[a-z0-9]+)+$
- filenames: ^[a-z0-9_]+\\.(py|ts|js|go|java|cpp|rs)$

Do not output natural language phrases.

Query:
{text}

Return only the rewritten search query.
"""

_REWRITE_WITH_CONTEXT_PROMPT = """
You are rewriting a search query for a code search system. You have context from the planner step, the user's request, and previous search attempts.

Available tools:
- find_symbol: searches for classes, functions, and variables
- search_for_pattern: searches for text inside files

Your task: produce a single search query (identifier-style) that is likely to find the right code. If previous attempts returned no or poor results, try a different formulation—e.g. different casing (StepExecutor vs step_executor), different terms, or a filename.

Rules:
1. Prefer code identifiers: PascalCase for classes, snake_case for functions/modules.
2. Do not repeat a query that already returned 0 results; vary the wording or try a related identifier.
3. Use 1-3 meaningful words only; no natural language sentences.
4. If the step asks for a "class", try PascalCase; if for "function" or "logic", try snake_case or a filename like module.py.

Context:

Planner step (what we are trying to find):
{planner_step}

User request (original goal):
{user_request}
"""

_REWRITE_PREVIOUS_ATTEMPTS_BLOCK = """
Previous search attempts (do not repeat failed queries; refine instead):
{previous_attempts}
"""

_REWRITE_WITH_CONTEXT_END = """
Return only the new search query, nothing else.
"""

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

        prompt = _REWRITE_WITH_CONTEXT_PROMPT.format(
            planner_step=(planner_step or "").strip(),
            user_request=(user_request or "").strip() or "(not provided)",
        )
        if attempts_slice:
            prompt += _REWRITE_PREVIOUS_ATTEMPTS_BLOCK.format(
                previous_attempts=_format_attempts_for_prompt(attempts_slice),
            )
        prompt += _REWRITE_WITH_CONTEXT_END

        if model_type == ModelType.REASONING:
            out = call_reasoning_model(prompt, max_tokens=64)
        else:
            out = call_small_model(prompt, max_tokens=64)

        cleaned = (out or "").strip()
        if not cleaned:
            raise ValueError("Query rewrite returned empty response")
        print("    [workflow] rewriter output:", cleaned)
        return cleaned

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
            out = call_reasoning_model(prompt, max_tokens=64)
        else:
            out = call_small_model(prompt, max_tokens=64)

        cleaned = (out or "").strip()
        if not cleaned:
            raise ValueError("Query rewrite returned empty response")
        print("    [workflow] rewriter output:", cleaned)
        return cleaned

    except Exception as e:
        logger.warning("LLM query rewrite failed: %s", e)
        raise