"""
SEARCH Quality Audit — Production-ready evaluator for code-retrieval search queries.

Call after each SEARCH step to assess query quality. Does NOT improve or rewrite queries.
Uses LLM to score: grounding, specificity, implementation_bias, structural_intent, result_quality.
Output: strict JSON with verdict (excellent|acceptable|weak|bad) and red_flags.

Integration: minimal — call after SEARCH, log result. No control-flow changes.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from agent.models.model_client import call_small_model

logger = logging.getLogger(__name__)

# Env gate: set ENABLE_SEARCH_QUALITY_AUDIT=1 to run evaluator after each SEARCH
ENABLE_SEARCH_QUALITY_AUDIT = os.getenv("ENABLE_SEARCH_QUALITY_AUDIT", "0").lower() in ("1", "true", "yes")

SYSTEM_PROMPT = """You are a strict evaluator of search query quality for a code-retrieval system.

You are NOT allowed to improve or rewrite queries.

Your job is to:
- Analyze whether a SEARCH query is well-formed and useful
- Identify weaknesses precisely
- Score multiple dimensions independently

Be harsh, precise, and technical.

Do NOT be polite. Do NOT generalize."""

USER_PROMPT_TEMPLATE = """Instruction:
{instruction}

Search Query:
{search_description}

Retrieved Context Summary:
{retrieval_summary}

(Optional)
Previous Searches:
{previous_searches}

---

Evaluate the SEARCH query using the criteria below.

--------------------------------
1. INSTRUCTION GROUNDING
--------------------------------
Does the query directly reflect the instruction?

- 0 = unrelated or generic
- 1 = loosely related
- 2 = partially grounded
- 3 = strongly grounded

Explain briefly.

--------------------------------
2. SPECIFICITY
--------------------------------
Is the query concrete enough to retrieve implementation code?

- 0 = vague ("find logic", "how it works")
- 1 = partially specific
- 2 = specific but incomplete
- 3 = highly specific (symbols/files/behavior)

--------------------------------
3. IMPLEMENTATION BIAS
--------------------------------
Does it target real code (not docs/tests/high-level)?

- 0 = likely docs/tests/irrelevant
- 1 = mixed
- 2 = mostly implementation
- 3 = clearly implementation-focused

--------------------------------
4. STRUCTURAL INTENT
--------------------------------
Does the query aim to retrieve relationships (calls/imports/flow)?

- 0 = none
- 1 = weak
- 2 = moderate
- 3 = strong (explicit relationships)

--------------------------------
5. RESULT QUALITY (BASED ON RETRIEVAL)
--------------------------------
Did the query likely produce useful context?

- 0 = junk / irrelevant
- 1 = weak
- 2 = usable
- 3 = strong (implementation bodies, relevant files)

--------------------------------
6. RED FLAGS
--------------------------------
Mark all that apply:

- generic_template_used (e.g., entrypoint/main/cli unrelated to instruction)
- too_vague
- too_narrow
- missing_relationships
- test_bias
- duplicate_of_previous
- irrelevant_terms

--------------------------------
7. FINAL VERDICT
--------------------------------

Return one:

- excellent
- acceptable
- weak
- bad

--------------------------------
OUTPUT FORMAT (STRICT JSON)
--------------------------------

{{
  "grounding": 0-3,
  "specificity": 0-3,
  "implementation_bias": 0-3,
  "structural_intent": 0-3,
  "result_quality": 0-3,
  "red_flags": [...],
  "verdict": "...",
  "explanation": "short technical explanation"
}}"""

RED_FLAGS_WHITELIST = frozenset({
    "generic_template_used",
    "too_vague",
    "too_narrow",
    "missing_relationships",
    "test_bias",
    "duplicate_of_previous",
    "irrelevant_terms",
})

VERDICT_WHITELIST = frozenset({"excellent", "acceptable", "weak", "bad"})


def _build_retrieval_summary(ranked_context: list[dict], results_count: int, top_files: list[str]) -> str:
    """Build a compact retrieval summary for the evaluator."""
    parts = []
    parts.append(f"results_count={results_count}")
    if top_files:
        parts.append(f"top_files={top_files[:5]}")
    rc = ranked_context or []
    if rc:
        impl_count = sum(1 for r in rc if isinstance(r, dict) and r.get("implementation_body_present"))
        linked_count = sum(1 for r in rc if isinstance(r, dict) and r.get("relations"))
        parts.append(f"ranked_context_count={len(rc)}, impl_bodies={impl_count}, linked={linked_count}")
    return "\n".join(parts) if parts else "(empty)"


def _build_previous_searches(step_results: list[Any]) -> str:
    """Extract previous SEARCH queries from step_results."""
    prev = []
    for sr in (step_results or []):
        action = getattr(sr, "action", "") or ""
        if str(action).upper() != "SEARCH":
            continue
        out = getattr(sr, "output", None)
        if isinstance(out, dict) and out.get("query"):
            prev.append(out["query"])
        elif isinstance(out, str) and out.strip():
            prev.append(out.strip())
    return "\n".join(prev) if prev else "(none)"


def _parse_audit_json(raw: str) -> dict[str, Any] | None:
    """Parse JSON from model output, handling markdown code blocks."""
    s = (raw or "").strip()
    # Strip ```json ... ``` if present
    m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", s)
    if m:
        s = m.group(1).strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError as e:
        logger.debug("[search_quality_audit] JSON parse failed: %s", e)
        return None


def _validate_and_normalize(parsed: dict[str, Any]) -> dict[str, Any]:
    """Ensure scores are 0-3, red_flags and verdict are valid."""
    out = dict(parsed)
    for key in ("grounding", "specificity", "implementation_bias", "structural_intent", "result_quality"):
        v = out.get(key)
        if isinstance(v, (int, float)):
            out[key] = max(0, min(3, int(v)))
        else:
            out[key] = 0
    rfs = out.get("red_flags")
    if isinstance(rfs, list):
        out["red_flags"] = [str(x) for x in rfs if str(x) in RED_FLAGS_WHITELIST]
    else:
        out["red_flags"] = []
    v = out.get("verdict", "")
    out["verdict"] = str(v).lower() if str(v).lower() in VERDICT_WHITELIST else "acceptable"
    if "explanation" not in out:
        out["explanation"] = ""
    return out


def effective_search_score(parsed: dict[str, Any]) -> int:
    """Derived score: grounding + specificity + implementation_bias. Range 0-9."""
    g = int(parsed.get("grounding", 0) or 0)
    s = int(parsed.get("specificity", 0) or 0)
    i = int(parsed.get("implementation_bias", 0) or 0)
    return g + s + i


def is_weak_or_bad(parsed: dict[str, Any]) -> bool:
    """True when verdict is weak or bad."""
    return str(parsed.get("verdict", "")).lower() in ("weak", "bad")


def evaluate_search_quality(
    instruction: str,
    search_description: str,
    retrieval_summary: str,
    previous_searches: str = "(none)",
) -> dict[str, Any]:
    """
    Call LLM evaluator. Returns parsed JSON or error dict.

    Does NOT raise. On model/parse failure, returns:
      {"error": "...", "verdict": "acceptable", "explanation": "audit failed"}
    """
    user_prompt = USER_PROMPT_TEMPLATE.format(
        instruction=instruction[:500],
        search_description=search_description[:300],
        retrieval_summary=retrieval_summary[:800],
        previous_searches=previous_searches[:400],
    )
    try:
        raw = call_small_model(
            user_prompt,
            system_prompt=SYSTEM_PROMPT,
            task_name="evaluation",  # classification task; use evaluation from models_config
            max_tokens=600,
        )
        parsed = _parse_audit_json(raw or "")
        if parsed:
            out = _validate_and_normalize(parsed)
            out["effective_search"] = effective_search_score(out)
            out["is_weak_or_bad"] = is_weak_or_bad(out)
            return out
    except Exception as e:
        logger.debug("[search_quality_audit] evaluator failed: %s", e)
    return {
        "error": "audit_failed",
        "grounding": 0,
        "specificity": 0,
        "implementation_bias": 0,
        "structural_intent": 0,
        "result_quality": 0,
        "red_flags": [],
        "verdict": "acceptable",
        "explanation": "audit failed",
        "effective_search": 0,
        "is_weak_or_bad": False,
    }


def run_audit_after_search(
    instruction: str,
    search_description: str,
    ranked_context: list[dict],
    results_count: int,
    top_files: list[str],
    step_results: list[Any],
    trace_id: str | None = None,
) -> dict[str, Any] | None:
    """
    Convenience: run audit with state-derived inputs. Returns None when disabled.
    Call from step_dispatcher after SEARCH success.
    """
    if not ENABLE_SEARCH_QUALITY_AUDIT:
        return None
    retrieval_summary = _build_retrieval_summary(ranked_context, results_count, top_files)
    previous_searches = _build_previous_searches(step_results or [])
    result = evaluate_search_quality(
        instruction=instruction,
        search_description=search_description,
        retrieval_summary=retrieval_summary,
        previous_searches=previous_searches,
    )
    if trace_id:
        try:
            from agent.observability.trace_logger import log_event

            log_event(trace_id, "search_quality_audit", result)
        except Exception:
            pass
    return result


def aggregate_audit_results(records: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Aggregate audit results from multiple SEARCH steps.
    Returns: bad_or_weak_rate, effective_search_avg, red_flag_counts, sample_bad.
    """
    if not records:
        return {
            "total_searches": 0,
            "bad_or_weak_rate": 0.0,
            "effective_search_avg": 0.0,
            "red_flag_counts": {},
            "sample_bad": [],
        }
    weak_bad = sum(1 for r in records if r.get("is_weak_or_bad"))
    eff_scores = [r.get("effective_search", 0) for r in records if r.get("effective_search") is not None]
    red_counts: dict[str, int] = {}
    for r in records:
        for flag in r.get("red_flags") or []:
            red_counts[flag] = red_counts.get(flag, 0) + 1
    sample_bad = [r for r in records if r.get("is_weak_or_bad")][:3]
    return {
        "total_searches": len(records),
        "bad_or_weak_rate": weak_bad / len(records),
        "effective_search_avg": sum(eff_scores) / len(eff_scores) if eff_scores else 0.0,
        "red_flag_counts": red_counts,
        "sample_bad": sample_bad,
    }
