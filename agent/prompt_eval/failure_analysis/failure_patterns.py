"""Classify failure from response patterns."""

import json

from agent.prompt_eval.failure_analysis.failure_logger import FailureRecord


def classify_failure(record: FailureRecord) -> str:
    """
    Map response/context patterns to error_type.
    Returns: bad_retrieval | invalid_json | wrong_tool | bad_patch | unknown
    """
    response = (record.response or "").lower()
    context = (record.context or "").lower()

    # Invalid JSON
    if not response.strip():
        return "invalid_json"
    try:
        if "{" in response:
            start = response.find("{")
            depth = 0
            for i, c in enumerate(response[start:], start):
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        json.loads(response[start : i + 1])
                        break
    except json.JSONDecodeError:
        return "invalid_json"

    # Wrong tool (e.g. used EDIT when SEARCH was needed)
    wrong_tool_phrases = [
        "wrong tool",
        "incorrect tool",
        "should have used search",
        "should have used edit",
        "used edit instead of search",
        "used search instead of edit",
    ]
    if any(p in response or p in context for p in wrong_tool_phrases):
        return "wrong_tool"

    # Bad patch (syntax error, wrong location)
    bad_patch_phrases = [
        "syntax error",
        "patch failed",
        "could not apply",
        "conflict",
        "invalid patch",
        "parse error",
    ]
    if any(p in response or p in context for p in bad_patch_phrases):
        return "bad_patch"

    # Bad retrieval (wrong file, irrelevant results)
    bad_retrieval_phrases = [
        "wrong file",
        "irrelevant",
        "not found",
        "no results",
        "retrieval_miss",
        "missing_dependency",
    ]
    if any(p in response or p in context for p in bad_retrieval_phrases):
        return "bad_retrieval"

    return "unknown"
