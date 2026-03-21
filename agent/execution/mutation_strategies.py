"""Mutation strategies for policy-engine retries. Phase 1: identifier variants; Phase 2/3 extensible later."""

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent.memory.state import AgentState


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


def get_initial_search_variants(base_query: str, max_total: int = 3) -> list[str]:
    """
    Stage 42: bounded deterministic variants for the first SEARCH policy attempt.
    Returns [base] + up to (max_total - 1) variants from generate_query_variants.
    Deduped by exact string, base first, blank strings ignored. Hard cap at max_total.
    """
    if not base_query or not str(base_query).strip():
        return []
    base = base_query.strip()
    if max_total < 1:
        return []
    seen: set[str] = {base}
    out: list[str] = [base]
    for v in generate_query_variants(base):
        if len(out) >= max_total:
            break
        if not v or not isinstance(v, str):
            continue
        s = v.strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _extract_symbol_from_description(description: str) -> str | None:
    """Extract likely symbol name from instruction (e.g. multiply from 'Repair ... multiply(2,3)')."""
    if not description or not isinstance(description, str):
        return None
    # Pattern: word before ( or .word(
    m = re.search(r"(\w+)\s*\(|\b(\w+)\s*\.\s*\w+\s*\(", description)
    if m:
        return (m.group(1) or m.group(2) or "").strip() or None
    # Pattern: "Fix X in" or "Add X()"
    m = re.search(r"(?:fix|add|implement|repair)\s+(\w+)\b", description, re.I)
    if m:
        return m.group(1).strip()
    return None


def _extract_file_hint_from_description(description: str) -> str | None:
    """Extract file path hint from instruction (e.g. src/calc/ops.py)."""
    if not description or not isinstance(description, str):
        return None
    m = re.search(r"[\w./\-]+\.(?:py|pyi|md)\b", description)
    return m.group(0).strip() if m else None


def symbol_retry(step: dict[str, Any], state: "AgentState | None" = None) -> list[dict[str, Any]]:
    """
    Produce deterministic EDIT retry variants. No repeated identical steps.
    Variants: original, file-level hint, symbol-short hint, alternate target from context.
    """
    base = dict(step)
    variants: list[dict[str, Any]] = [base]

    desc = (step.get("description") or "").strip()
    sym = _extract_symbol_from_description(desc)
    file_hint = _extract_file_hint_from_description(desc)

    # Variant 2: hint file-level (module_append) when symbol-level may fail
    if desc:
        v2 = dict(base)
        v2["edit_target_level"] = "file"
        v2["_retry_strategy"] = "file_level"
        variants.append(v2)

    # Variant 3: hint short symbol name
    if sym:
        v3 = dict(base)
        v3["edit_target_symbol_short"] = sym
        v3["_retry_strategy"] = "symbol_short"
        variants.append(v3)

    # Variant 4: alternate target from ranked_context (when state available)
    if state and isinstance(state.context, dict):
        rc = state.context.get("ranked_context") or []
        files = state.context.get("files") or []
        candidates = [c.get("file") for c in rc if isinstance(c, dict) and c.get("file")]
        candidates = [f for f in candidates if f and f != file_hint]
        if not candidates and files:
            candidates = [f for f in files if isinstance(f, str) and f and f != file_hint]
        if candidates:
            v4 = dict(base)
            v4["edit_target_file_override"] = candidates[0]
            v4["_retry_strategy"] = "alternate_target"
            variants.append(v4)

    # Deduplicate by meaningful content (avoid identical retries)
    seen: set[tuple[str, ...]] = set()
    out: list[dict[str, Any]] = []
    for v in variants:
        key = (
            v.get("description", ""),
            v.get("edit_target_level"),
            v.get("edit_target_symbol_short"),
            v.get("edit_target_file_override"),
        )
        if key not in seen:
            seen.add(key)
            out.append(v)
    return out if len(out) > 1 else [base]


def retry_same(step: dict[str, Any]) -> list[dict[str, Any]]:
    """Return [step] so INFRA simply retries the same parameters."""
    return [step]
