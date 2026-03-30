"""
Deterministic replan recovery: error normalization, recovery classification,
query refinement, duplicate detection, and post-LLM plan repair.

Pure helpers (no I/O) for testability.
"""

from __future__ import annotations

import json
import re
from typing import Any

from agent.contracts.error_codes import (
    REASON_CODE_INSUFFICIENT_GROUNDING,
    REASON_CODE_INSUFFICIENT_SUBSTANTIVE_CONTEXT,
    REASON_CODE_WEAK_RETRIEVAL_GROUNDING,
)
from agent.memory.state import AgentState
from agent.memory.step_result import StepResult

# Canonical normalized error signals (string tokens)
SIGNAL_EMPTY_CONTEXT = "empty_context"
SIGNAL_INSUFFICIENT_SUBSTANTIVE = "insufficient_substantive_context"
SIGNAL_INSUFFICIENT_GROUNDING = "insufficient_grounding"
SIGNAL_TEST_ONLY = "test_only_context"
SIGNAL_NO_USEFUL_CANDIDATES = "no_useful_candidates"
SIGNAL_SEARCH_WEAK = "search_weak_results"
SIGNAL_UNKNOWN = "unknown"

# Recovery modes (classifier output)
RECOVERY_NEED_SEARCH_BEFORE_EXPLAIN = "need_search_before_explain"
RECOVERY_NEED_IMPLEMENTATION_SEARCH = "need_implementation_search"
RECOVERY_NEED_NON_TEST_CODE = "need_non_test_code"
RECOVERY_NEED_SPECIFIC_SYMBOL_SEARCH = "need_specific_symbol_search"
RECOVERY_NEED_BETTER_BUILD_CONTEXT = "need_better_build_context"
RECOVERY_GENERIC_FAILURE = "generic_failure"

_STOPWORDS = frozenset(
    {
        "search",
        "find",
        "locate",
        "code",
        "for",
        "the",
        "in",
        "a",
        "an",
        "to",
        "of",
        "and",
        "or",
        "is",
        "it",
        "on",
        "at",
        "be",
        "as",
        "by",
    }
)

def normalize_replan_error_signal(error: str | None, reason_code: str | None) -> str:
    """
    Map raw error text and optional reason_code to a canonical signal.
    """
    rc = (reason_code or "").strip().lower()
    if rc == REASON_CODE_INSUFFICIENT_SUBSTANTIVE_CONTEXT:
        return SIGNAL_INSUFFICIENT_SUBSTANTIVE
    if rc == REASON_CODE_INSUFFICIENT_GROUNDING:
        return SIGNAL_INSUFFICIENT_GROUNDING
    if rc == REASON_CODE_WEAK_RETRIEVAL_GROUNDING:
        return SIGNAL_SEARCH_WEAK

    e = (error or "").lower()
    if "non-substantive" in e or "insufficient substantive" in e:
        return SIGNAL_INSUFFICIENT_SUBSTANTIVE
    if "i cannot answer without relevant code context" in e or "empty context" in e:
        return SIGNAL_EMPTY_CONTEXT
    if "no context for explain" in e or "run search first" in e:
        return SIGNAL_EMPTY_CONTEXT
    if "insufficient grounding" in e or "blocked: insufficient grounding" in e:
        return SIGNAL_INSUFFICIENT_GROUNDING
    if "only test files" in e or ("test files" in e and "next step is explain" in e):
        return SIGNAL_TEST_ONLY
    if "no useful" in e or "no candidates" in e or "empty or invalid results" in e:
        return SIGNAL_NO_USEFUL_CANDIDATES
    return SIGNAL_UNKNOWN


def _tokenize_for_dup(s: str) -> list[str]:
    s = (s or "").lower()
    s = re.sub(r"[^\w\s]", " ", s)
    parts = [p for p in s.split() if p and p not in _STOPWORDS]
    return parts


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _bigrams(tokens: list[str]) -> list[tuple[str, str]]:
    if len(tokens) < 2:
        return []
    return list(zip(tokens, tokens[1:]))


def searches_are_near_duplicates(desc_a: str, desc_b: str) -> bool:
    """
    Near-duplicate if exact normalized match, or Jaccard>=0.7 with >=4 shared
    content tokens; also uses ordered bigram overlap. Short queries (<3 tokens)
    require exact normalized string match only.
    """
    ta = _tokenize_for_dup(desc_a)
    tb = _tokenize_for_dup(desc_b)
    sa, sb = set(ta), set(tb)
    if len(ta) < 3 or len(tb) < 3:
        return " ".join(ta) == " ".join(tb) and len(ta) > 0 and len(tb) > 0

    if " ".join(ta) == " ".join(tb):
        return True

    jac = _jaccard(sa, sb)
    inter_sz = len(sa & sb)
    if jac >= 0.7 and inter_sz >= 4:
        return True

    ba, bb = set(_bigrams(ta)), set(_bigrams(tb))
    if ba and bb and len(ba & bb) >= max(2, min(len(ba), len(bb)) // 2):
        return True

    return False


def _scan_step_results(state: AgentState) -> dict[str, Any]:
    """Backward scan: failed match, last SEARCH, BUILD_CONTEXT, EXPLAIN."""
    results = list(state.step_results or [])
    out: dict[str, Any] = {
        "last_search": None,
        "last_build_context": None,
        "last_explain": None,
    }
    seen_search = False
    seen_bc = False
    seen_explain = False
    for sr in reversed(results):
        if not isinstance(sr, StepResult):
            continue
        a = (sr.action or "").upper()
        if not seen_search and a in ("SEARCH", "SEARCH_CANDIDATES"):
            out["last_search"] = sr
            seen_search = True
        if not seen_bc and a == "BUILD_CONTEXT":
            out["last_build_context"] = sr
            seen_bc = True
        if not seen_explain and a == "EXPLAIN":
            out["last_explain"] = sr
            seen_explain = True
    return out


def _find_result_for_step_id(state: AgentState, step_id: int | None) -> StepResult | None:
    if step_id is None:
        return None
    for sr in reversed(state.step_results or []):
        if isinstance(sr, StepResult) and sr.step_id == step_id:
            return sr
    return None


def _extract_search_description(sr: StepResult | None) -> str:
    if sr is None:
        return ""
    out = sr.output
    if isinstance(out, dict):
        q = out.get("query") or out.get("description")
        if isinstance(q, str) and q.strip():
            return q.strip()[:500]
    return ""


def _build_context_was_empty(sr: StepResult | None) -> bool:
    if sr is None:
        return True
    out = sr.output
    if not isinstance(out, dict):
        return True
    blocks = out.get("context_blocks")
    if blocks is None:
        return True
    if isinstance(blocks, list) and len(blocks) == 0:
        return True
    return False


def _count_tail_matches(history: list[dict[str, Any]], predicate) -> int:
    n = 0
    for i in range(len(history) - 1, -1, -1):
        if predicate(history[i]):
            n += 1
        else:
            break
    return n


def build_replan_failure_context(
    state: AgentState,
    failed_step: dict | None,
    error: str | None,
) -> dict[str, Any]:
    """
    Build compact structured context for replanner prompt + repair pass.
    """
    ctx = state.context if isinstance(getattr(state, "context", None), dict) else {}
    dominant = ctx.get("dominant_artifact_mode") if isinstance(ctx, dict) else None
    if dominant not in ("code", "docs"):
        dominant = "code"

    failed_action = ((failed_step or {}).get("action") or "EXPLAIN").upper()
    failed_step_id = failed_step.get("id") if isinstance(failed_step, dict) else None

    scanned = _scan_step_results(state)
    failed_sr = _find_result_for_step_id(state, failed_step_id)
    reason_code = None
    if isinstance(failed_sr, StepResult) and failed_sr.reason_code:
        reason_code = failed_sr.reason_code
    if not reason_code and isinstance(ctx, dict):
        reason_code = ctx.get("last_dispatch_reason_code")

    err_text = (error or "").strip() or None
    error_signal = normalize_replan_error_signal(err_text, reason_code)

    search_quality = ctx.get("search_quality") if isinstance(ctx, dict) else None
    if isinstance(search_quality, str):
        sq = search_quality.lower().strip()
    else:
        sq = None
    if sq == "weak" and error_signal == SIGNAL_UNKNOWN:
        error_signal = SIGNAL_SEARCH_WEAK

    history = list(ctx.get("replan_recovery_history") or []) if isinstance(ctx, dict) else []

    same_failure_count = _count_tail_matches(
        history,
        lambda e: e.get("failed_action") == failed_action and e.get("error_signal") == error_signal,
    )

    recent_searches: list[str] = []
    for sr in reversed(state.step_results or []):
        if not isinstance(sr, StepResult):
            continue
        if (sr.action or "").upper() not in ("SEARCH", "SEARCH_CANDIDATES"):
            continue
        desc = _extract_search_description(sr)
        if not desc:
            desc = ""  # fall back: could use step from plan if we had it
        if desc:
            recent_searches.append(desc[:240])
        if len(recent_searches) >= 3:
            break
    recent_searches.reverse()

    build_empty = _build_context_was_empty(scanned.get("last_build_context"))

    # Provisional recovery for counting same_recovery_mode (needs classify first pass)
    provisional: dict[str, Any] = {
        "failed_action": failed_action,
        "reason_code": reason_code,
        "error_signal": error_signal,
        "search_quality": sq,
        "recent_searches": recent_searches,
        "dominant_artifact_mode": dominant,
        "build_context_empty": build_empty,
        "failed_step_description": (failed_step.get("description") or "")[:500] if isinstance(failed_step, dict) else "",
        "same_failure_count": same_failure_count,
    }

    recovery_mode = classify_replan_recovery_mode(provisional)

    same_recovery_mode_count = _count_tail_matches(
        history,
        lambda e: e.get("recovery_mode") == recovery_mode,
    )

    provisional["same_recovery_mode_count"] = same_recovery_mode_count
    provisional["recovery_mode"] = recovery_mode

    prior_descs = list(recent_searches)
    attempt_n = max(same_failure_count, same_recovery_mode_count) + 1
    recovery_hint = refine_search_description_for_recovery(
        original_instruction=(getattr(state, "instruction", "") or "")[:1500],
        failed_step_desc=provisional.get("failed_step_description") or "",
        recovery_mode=recovery_mode,
        prior_search_descs=prior_descs,
        attempt_n=attempt_n,
    )

    provisional["recovery_hint"] = recovery_hint
    return provisional


def classify_replan_recovery_mode(failure_context: dict[str, Any]) -> str:
    """
    Return one of the RECOVERY_* constants.
    """
    dom = failure_context.get("dominant_artifact_mode") or "code"
    if dom == "docs":
        return RECOVERY_GENERIC_FAILURE

    failed_action = (failure_context.get("failed_action") or "").upper()
    es = failure_context.get("error_signal") or SIGNAL_UNKNOWN
    sq = (failure_context.get("search_quality") or "").lower()
    build_empty = bool(failure_context.get("build_context_empty"))

    if failed_action == "BUILD_CONTEXT" and build_empty:
        return RECOVERY_NEED_BETTER_BUILD_CONTEXT

    if failed_action in ("SEARCH_CANDIDATES",) and build_empty and not failure_context.get("recent_searches"):
        return RECOVERY_NEED_BETTER_BUILD_CONTEXT

    if es == SIGNAL_TEST_ONLY:
        return RECOVERY_NEED_NON_TEST_CODE

    if es == SIGNAL_SEARCH_WEAK or sq == "weak":
        return RECOVERY_NEED_IMPLEMENTATION_SEARCH

    if es in (SIGNAL_INSUFFICIENT_SUBSTANTIVE,):
        return RECOVERY_NEED_IMPLEMENTATION_SEARCH

    if es == SIGNAL_INSUFFICIENT_GROUNDING:
        return RECOVERY_NEED_SEARCH_BEFORE_EXPLAIN

    if es == SIGNAL_EMPTY_CONTEXT:
        if failed_action == "EXPLAIN":
            return RECOVERY_NEED_SEARCH_BEFORE_EXPLAIN
        return RECOVERY_NEED_SPECIFIC_SYMBOL_SEARCH

    if es == SIGNAL_NO_USEFUL_CANDIDATES:
        return RECOVERY_NEED_BETTER_BUILD_CONTEXT

    return RECOVERY_GENERIC_FAILURE


def refine_search_description_for_recovery(
    *,
    original_instruction: str,
    failed_step_desc: str,
    recovery_mode: str,
    prior_search_descs: list[str],
    attempt_n: int,
) -> str:
    """
    Instruction-first refinement; use failed_step_desc as secondary hint only.
    Does not invent repo-specific symbols beyond instruction, prior searches, or generic artifact terms.
    """
    base_parts: list[str] = []
    ins = (original_instruction or "").strip()
    if ins:
        base_parts.append(ins[:400])
    fd = (failed_step_desc or "").strip()
    if fd and fd.lower() not in ins.lower():
        base_parts.append(fd[:200])

    base = ". ".join(base_parts) if base_parts else "Repository implementation"

    suffix_by_mode: dict[str, str] = {
        RECOVERY_NEED_SEARCH_BEFORE_EXPLAIN: (
            "Target implementation source: function/class body, callsites, and imports in non-test modules."
        ),
        RECOVERY_NEED_IMPLEMENTATION_SEARCH: (
            "Search implementation code (not tests): modules under src/, definitions, bodies, and wiring."
        ),
        RECOVERY_NEED_NON_TEST_CODE: (
            "Exclude test files; prefer production modules, src tree, and non-test paths."
        ),
        RECOVERY_NEED_SPECIFIC_SYMBOL_SEARCH: (
            "Locate concrete symbols, class/function definitions, and import chains."
        ),
        RECOVERY_NEED_BETTER_BUILD_CONTEXT: (
            "Discover candidate files/symbols first, then narrow to implementation files."
        ),
        RECOVERY_GENERIC_FAILURE: "Refine query using distinct keywords from the instruction.",
    }
    suffix = suffix_by_mode.get(recovery_mode, suffix_by_mode[RECOVERY_GENERIC_FAILURE])

    if attempt_n >= 2:
        suffix = (
            f"{suffix} Prioritize entrypoints: __main__, package __init__, cli/main, "
            "settings load or initialization, constructors, and import chains."
        )

    hint = f"Search: {base[:350]}. {suffix}"

    if recovery_mode in (
        RECOVERY_NEED_IMPLEMENTATION_SEARCH,
        RECOVERY_NEED_SEARCH_BEFORE_EXPLAIN,
        RECOVERY_NEED_NON_TEST_CODE,
        RECOVERY_NEED_SPECIFIC_SYMBOL_SEARCH,
    ):
        hint += (
            " Code anchors: typical entry files (__main__.py, cli.py, main.py); "
            "symbols and call targets (main, run, app, execute); patterns such as "
            "`if __name__ == '__main__'` and import/call chains."
        )

    return hint[:900]


def _renumber_steps(steps: list[dict]) -> list[dict]:
    out = []
    for i, s in enumerate(steps):
        if not isinstance(s, dict):
            continue
        c = dict(s)
        c["id"] = i + 1
        out.append(c)
    return out


def _is_grounding_recovery(mode: str) -> bool:
    return mode in (
        RECOVERY_NEED_SEARCH_BEFORE_EXPLAIN,
        RECOVERY_NEED_IMPLEMENTATION_SEARCH,
        RECOVERY_NEED_NON_TEST_CODE,
        RECOVERY_NEED_SPECIFIC_SYMBOL_SEARCH,
    )


def repair_replan_steps_for_recovery(
    steps: list[dict],
    failure_context: dict[str, Any],
    recovery_mode: str,
) -> tuple[list[dict], bool]:
    """
    Post-process replanned steps: dedupe/collapse SEARCH, insert SEARCH before EXPLAIN when needed.
    Returns (new_steps, mutated).
    """
    if not isinstance(steps, list) or not steps:
        return steps, False

    mutated = False
    dom = failure_context.get("dominant_artifact_mode") or "code"
    def _sig(plan_steps: list[dict]) -> str:
        return json.dumps(plan_steps, sort_keys=True, default=str)

    if dom == "docs":
        cleaned = [dict(s) for s in steps if isinstance(s, dict)]
        out = _collapse_duplicate_searches(cleaned, failure_context, docs_lane=True)
        mutated = _sig(out) != _sig(cleaned)
        return _renumber_steps(out), mutated

    cleaned = [dict(s) for s in steps if isinstance(s, dict)]
    out = _collapse_duplicate_searches(cleaned, failure_context, docs_lane=False)
    mutated = _sig(out) != _sig(cleaned)

    recovery_hint = (failure_context.get("recovery_hint") or "").strip()
    if _is_grounding_recovery(recovery_mode) or recovery_mode == RECOVERY_NEED_BETTER_BUILD_CONTEXT:
        out2, ins = _ensure_search_before_explain(out, recovery_hint)
        if ins:
            mutated = True
            out = out2

    out = _renumber_steps(out)
    return out, mutated


def _collapse_duplicate_searches(
    steps: list[dict],
    failure_context: dict[str, Any],
    *,
    docs_lane: bool,
) -> list[dict]:
    """Collapse near-duplicate SEARCH/SEARCH_CANDIDATES; refine with recovery_hint when dup."""
    hint = (failure_context.get("recovery_hint") or "").strip()
    seen_descs: list[str] = [
        d for d in (failure_context.get("recent_searches") or []) if isinstance(d, str) and d.strip()
    ]
    out: list[dict] = []
    for s in steps:
        if not isinstance(s, dict):
            continue
        a = (s.get("action") or "").upper()
        if a not in ("SEARCH", "SEARCH_CANDIDATES"):
            out.append(dict(s))
            continue
        desc = (s.get("description") or s.get("query") or "").strip()
        dup_with_prior = False
        for prev in seen_descs:
            if prev and desc and searches_are_near_duplicates(desc, prev):
                dup_with_prior = True
                break
        dup_with_last = False
        if out:
            la = (out[-1].get("action") or "").upper()
            if la in ("SEARCH", "SEARCH_CANDIDATES"):
                ldesc = (out[-1].get("description") or out[-1].get("query") or "").strip()
                if desc and ldesc and searches_are_near_duplicates(desc, ldesc):
                    dup_with_last = True
        dup = dup_with_prior or dup_with_last
        if dup:
            if docs_lane:
                continue
            if hint:
                replacement = dict(s)
                replacement["description"] = hint[:500]
                replacement.setdefault("reason", "Recovery repair: refined duplicate SEARCH")
                if out and (out[-1].get("action") or "").upper() in ("SEARCH", "SEARCH_CANDIDATES") and dup_with_last:
                    out[-1] = replacement
                else:
                    out.append(replacement)
                seen_descs.append(hint[:240])
            continue
        seen_descs.append(desc)
        out.append(dict(s))
    return out


def _ensure_search_before_explain(
    steps: list[dict],
    recovery_hint: str,
) -> tuple[list[dict], bool]:
    """Insert SEARCH before first EXPLAIN if no retrieval step precedes it."""
    idx_explain = None
    for i, s in enumerate(steps):
        if (s.get("action") or "").upper() == "EXPLAIN":
            idx_explain = i
            break
    if idx_explain is None:
        return steps, False

    has_prior = False
    for s in steps[:idx_explain]:
        a = (s.get("action") or "").upper()
        if a in ("SEARCH", "SEARCH_CANDIDATES", "BUILD_CONTEXT"):
            has_prior = True
            break
    if has_prior:
        return steps, False

    desc = recovery_hint or "Search implementation code for definitions, bodies, and callsites in non-test files."
    insert: dict[str, Any] = {
        "id": 0,
        "action": "SEARCH",
        "description": desc[:500],
        "reason": "Recovery repair: SEARCH required before EXPLAIN after grounding failure",
    }
    new_steps = [insert] + steps
    return new_steps, True


def record_replan_recovery_event(state: AgentState, snapshot: dict[str, Any]) -> None:
    """Append one recovery event for escalation counting on subsequent replans."""
    if not isinstance(state.context, dict):
        return
    hist = state.context.setdefault("replan_recovery_history", [])
    if not isinstance(hist, list):
        hist = []
        state.context["replan_recovery_history"] = hist
    hist.append(
        {
            "failed_action": snapshot.get("failed_action"),
            "error_signal": snapshot.get("error_signal"),
            "recovery_mode": snapshot.get("recovery_mode"),
        }
    )
    # Bound history size
    if len(hist) > 32:
        del hist[:-32]


def format_failure_context_json(failure_context: dict[str, Any]) -> str:
    """Compact JSON string for the replanner user prompt.

    recovery_hint is excluded so it can be presented separately with downgrade
    framing (optional, low priority) to control signal hierarchy.
    """
    payload = {
        "failed_action": failure_context.get("failed_action"),
        "reason_code": failure_context.get("reason_code"),
        "error_signal": failure_context.get("error_signal"),
        "search_quality": failure_context.get("search_quality"),
        "recent_searches": failure_context.get("recent_searches") or [],
        "recovery_mode": failure_context.get("recovery_mode"),
        "same_failure_count": failure_context.get("same_failure_count", 0),
        "same_recovery_mode_count": failure_context.get("same_recovery_mode_count", 0),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
