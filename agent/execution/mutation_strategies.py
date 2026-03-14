"""Mutation strategies for policy-engine retries. Phase 1: identifier variants; Phase 2/3 extensible later."""

import re
from typing import Any


def generate_query_variants(query: str) -> list[str]:
    """
    Phase 1 — identifier variants: underscorify, strip digits, shorten.
    Input e.g. "router eval2" -> e.g. ["router_eval_v2", "router_eval2", "router_eval", "router"].
    """
    if not query or not query.strip():
        return []
    q = query.strip()
    seen: set[str] = set()
    out: list[str] = []

    def add(s: str) -> None:
        if s and s not in seen:
            seen.add(s)
            out.append(s)

    words = re.split(r"[\s_\-\.]+", q)
    words = [w for w in words if w]
    if not words:
        add(q.replace(" ", "_"))
        return out

    # 1) With _v before trailing digits: router eval2 -> router_eval_v2
    if words[-1] and re.search(r"\d+$", words[-1]):
        base = re.sub(r"\d+$", "", words[-1]) or words[-1]
        num = re.search(r"\d+$", words[-1])
        suffix = ("_v" + num.group()) if num else ""
        variant = "_".join(words[:-1] + [base + suffix]) if len(words) > 1 else (base + suffix)
        add(variant)
    # 2) Underscore join as-is: router_eval2
    add("_".join(words))
    # 3) Without trailing digits: router_eval
    if words[-1] and re.search(r"\d", words[-1]):
        no_digits = re.sub(r"\d+", "", words[-1]) or words[-1]
        rest = words[:-1] + [no_digits] if no_digits else words[:-1]
        if rest:
            add("_".join(rest))
    # 4) Shortened: drop last word(s) down to single token
    for i in range(len(words) - 1, 0, -1):
        add("_".join(words[:i]))
    return out


def symbol_retry(step: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Placeholder: return a list of mutated step representations for EDIT retries.
    No LLM; deterministic. Caller may use these for retry attempts.
    """
    # For now return the step once; future: copy with small symbol/path variants
    return [dict(step)]


def retry_same(step: dict[str, Any]) -> list[dict[str, Any]]:
    """Return [step] so INFRA simply retries the same parameters."""
    return [step]
