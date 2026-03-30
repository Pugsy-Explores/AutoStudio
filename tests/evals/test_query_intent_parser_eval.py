"""
Live LLM evaluation for QueryIntentParser (semantic retrieval-intent quality).

Not a unit test: calls the real reasoning model via ``call_reasoning_model`` / messages.

Run (repo root, credentials configured):

  export QUERY_INTENT_PARSER_EVAL_LIVE=1
  pytest tests/evals/test_query_intent_parser_eval.py -v -s -m query_intent_parser_eval

Omit ``-s`` to hide the printed report (stdout captured).

CI default: skipped unless QUERY_INTENT_PARSER_EVAL_LIVE=1.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Literal

import pytest
import yaml

from agent.models.model_client import call_reasoning_model, call_reasoning_model_messages
from agent.models.model_config import get_prompt_model_name_for_task
from agent_v2.exploration.exploration_task_names import EXPLORATION_TASK_QUERY_INTENT
from agent_v2.exploration.query_intent_parser import QueryIntentParser
from agent_v2.schemas.exploration import QueryIntent

from tests.evals.edge_case_coverage import (
    compute_category_metrics,
    count_categories_from_records,
    print_edge_case_coverage_report,
    print_edge_failure_patterns_diagnostic,
    print_trace_log,
)

Tier = Literal["easy", "medium", "hard", "edge"]

_EVAL_DIR = Path(__file__).resolve().parent / "query_intent_parser"
_CASE_FILES: dict[Tier, str] = {
    "easy": "easy_cases.yaml",
    "medium": "medium_cases.yaml",
    "hard": "hard_cases.yaml",
    "edge": "edge_cases.yaml",
}

_ALLOWED_INTENT = frozenset({"explanation", "debugging", "navigation", "modification"})
_ALLOWED_SCOPE = frozenset({"narrow", "component", "system"})
_ALLOWED_FOCUS = frozenset({"internal_logic", "relationships", "usage"})

SCOPE_ORDER = {
    "narrow": 0,
    "component": 1,
    "system": 2,
}

_LOG = logging.getLogger(__name__)


def check_scope(expected: Any, actual: Any) -> tuple[bool, str | None]:
    if actual is None:
        return False, "scope is null"
    if expected is None:
        return True, None
    if SCOPE_ORDER[actual] <= SCOPE_ORDER[expected]:
        return True, None
    return True, f"scope mismatch: expected {expected}, got {actual}"


def check_intent(expected: Any, actual: Any) -> tuple[bool, str | None]:
    if actual is None:
        return False, "intent_type is null"
    if expected is None:
        return True, None
    if actual == expected:
        return True, None
    return True, f"intent mismatch: expected {expected}, got {actual}"


def check_focus(expected: Any, actual: Any) -> tuple[bool, str | None]:
    if actual is None:
        return False, "focus is null"
    if expected is None:
        return True, None
    if actual == expected:
        return True, None
    return True, f"focus mismatch: expected {expected}, got {actual}"

# Soft: keywords are trivial if every token is in this small generic set (structural check only).
_GENERIC_TOKENS = frozenset(
    {
        "a",
        "an",
        "the",
        "to",
        "of",
        "in",
        "is",
        "it",
        "for",
        "on",
        "and",
        "or",
        "how",
        "what",
        "where",
        "why",
        "when",
        "does",
        "do",
        "code",
        "file",
        "files",
        "function",
        "class",
        "method",
        "work",
        "system",
        "get",
        "use",
    }
)


def _load_cases(tier: Tier) -> list[dict[str, Any]]:
    path = _EVAL_DIR / _CASE_FILES[tier]
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(raw, list)
    return raw


def _make_parser() -> QueryIntentParser:
    return QueryIntentParser(
        llm_generate=lambda p: call_reasoning_model(p, task_name=EXPLORATION_TASK_QUERY_INTENT),
        llm_generate_messages=lambda m: call_reasoning_model_messages(
            m, task_name=EXPLORATION_TASK_QUERY_INTENT
        ),
        model_name=get_prompt_model_name_for_task(EXPLORATION_TASK_QUERY_INTENT),
    )


def _hard_validate_enums(qi: QueryIntent) -> None:
    if qi.intent_type is not None and qi.intent_type not in _ALLOWED_INTENT:
        pytest.fail(f"intent_type not in allowed set: {qi.intent_type!r}")
    if qi.scope is not None and qi.scope not in _ALLOWED_SCOPE:
        pytest.fail(f"scope not in allowed set: {qi.scope!r}")
    if qi.focus is not None and qi.focus not in _ALLOWED_FOCUS:
        pytest.fail(f"focus not in allowed set: {qi.focus!r}")


def _symbols_identifier_like(symbols: list[str]) -> bool:
    if not symbols:
        return True
    pat = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
    return all(pat.match(str(s).strip()) for s in symbols if str(s).strip())


def _keywords_trivial(keywords: list[str]) -> bool:
    if not keywords:
        return True
    for kw in keywords:
        parts = re.split(r"[^\w]+", str(kw).lower())
        tokens = [p for p in parts if len(p) >= 2]
        if not tokens:
            continue
        if any(t not in _GENERIC_TOKENS for t in tokens):
            return False
    return True


def _assessed_symbol_quality(qi: QueryIntent, tier: Tier) -> Literal["high", "medium", "low"]:
    syms = [str(s).strip() for s in (qi.symbols or []) if str(s).strip()]
    if syms and _symbols_identifier_like(syms):
        return "high"
    if qi.regex_patterns and any(str(x).strip() for x in qi.regex_patterns):
        return "medium"
    if qi.has_meaningful_queries() and not _keywords_trivial(list(qi.keywords or [])):
        return "medium"
    if tier == "hard":
        return "low"
    if syms and not _symbols_identifier_like(syms):
        return "low"
    return "low"


def _run_one(
    parser: QueryIntentParser,
    tier: Tier,
    case: dict[str, Any],
) -> dict[str, Any]:
    cid = case["id"]
    instruction = case["instruction"]
    exp = case.get("expected") or {}

    qi = parser.parse(instruction)
    assert isinstance(qi, QueryIntent)
    _hard_validate_enums(qi)

    intent_ok, intent_warn = check_intent(exp.get("intent_type"), qi.intent_type)
    scope_ok, scope_warn = check_scope(exp.get("scope"), qi.scope)
    focus_ok, focus_warn = check_focus(exp.get("focus"), qi.focus)
    warnings = [w for w in [intent_warn, scope_warn, focus_warn] if w]
    if warnings:
        for w in warnings:
            _LOG.warning("[query_intent_parser_eval] case %s: %s", cid, w)

    assessed_sq = _assessed_symbol_quality(qi, tier)
    expected_sq = exp.get("symbol_quality")
    symbol_quality_match = expected_sq is None or assessed_sq == expected_sq

    soft_symbol_spaces = not _symbols_identifier_like([s for s in (qi.symbols or []) if str(s).strip()])
    soft_keywords_generic = _keywords_trivial(list(qi.keywords or [])) and bool(qi.keywords)
    soft_empty_grounding = (
        tier in ("easy", "medium")
        and not (qi.symbols or qi.regex_patterns)
        and not qi.has_meaningful_queries()
    )

    dims = [intent_ok, scope_ok, focus_ok]
    case_accuracy = sum(1 for w in [intent_warn, scope_warn, focus_warn] if w is None) / 3.0

    trace_id = str(case.get("trace_id") or cid)
    row_hard_fail = not (intent_ok and scope_ok and focus_ok) and not case.get("skip_null_guard")

    return {
        "id": cid,
        "trace_id": trace_id,
        "tier": tier,
        "intent_correct": intent_ok,
        "scope_correct": scope_ok,
        "focus_correct": focus_ok,
        "intent_warn": intent_warn,
        "scope_warn": scope_warn,
        "focus_warn": focus_warn,
        "warnings": warnings,
        "row_hard_fail": row_hard_fail,
        "symbol_quality": assessed_sq,
        "symbol_quality_match": symbol_quality_match,
        "case_accuracy": case_accuracy,
        "soft_symbol_spaces": soft_symbol_spaces,
        "soft_keywords_generic": soft_keywords_generic,
        "soft_empty_grounding": soft_empty_grounding,
        "query_intent_dump": qi.model_dump(),
    }


def _fmt_val(v: Any) -> str:
    if v is None:
        return "(none)"
    return str(v)


def _compact_query_intent_json(dump: dict[str, Any], *, max_len: int = 600) -> str:
    """Single-line JSON for prompt/debug inspection; truncated if oversized."""
    s = json.dumps(dump, ensure_ascii=False, separators=(",", ":"), default=str)
    if len(s) > max_len:
        return s[: max_len - 3] + "..."
    return s


def _print_diagnostic_report(
    records: list[tuple[Tier, dict[str, Any], dict[str, Any]]],
) -> None:
    """
    LLM-ready plain-text report: failures first, optional good cases, copy markers.
    Formatting only; does not alter scores computed in _run_one.
    """
    n = len(records)
    if n == 0:
        return

    intent_ok = sum(1 for _, _, r in records if r.get("intent_warn") is None)
    scope_ok = sum(1 for _, _, r in records if r.get("scope_warn") is None)
    focus_ok = sum(1 for _, _, r in records if r.get("focus_warn") is None)

    failed: list[tuple[Tier, dict[str, Any], dict[str, Any]]] = []
    passed: list[tuple[Tier, dict[str, Any], dict[str, Any]]] = []
    for tier in ("easy", "medium", "hard", "edge"):
        tier_recs = [(t, c, r) for t, c, r in records if t == tier]
        tier_recs.sort(key=lambda x: x[1]["id"])
        for item in tier_recs:
            _, _, r = item
            if r.get("warnings"):
                failed.append(item)
            else:
                passed.append(item)

    patterns_seen: set[str] = set()
    for _, _, r in records:
        if r.get("intent_warn"):
            patterns_seen.add("incorrect_intent")
        if r.get("scope_warn") or r.get("focus_warn"):
            patterns_seen.add("wrong_abstraction")
        if r.get("soft_empty_grounding"):
            patterns_seen.add("missing_symbol_grounding")
        if r.get("soft_keywords_generic"):
            patterns_seen.add("overly_generic_keywords")

    # Fixed display order; only labels for keys observed at least once.
    pattern_labels: list[tuple[str, str]] = [
        ("missing_symbol_grounding", "Missing symbol grounding"),
        ("wrong_abstraction", "Wrong abstraction level (scope/focus mismatch vs expected)"),
        ("incorrect_intent", "Intent mismatch vs expected"),
        ("overly_generic_keywords", "Overly generic keywords"),
    ]
    pattern_lines = [f"- {label}" for key, label in pattern_labels if key in patterns_seen]

    lines: list[str] = [
        "=== COPY INTO LLM BELOW ===",
        "",
        "=== QUERY INTENT PARSER DIAGNOSTIC REPORT ===",
        "",
        "[SUMMARY]",
        f"Total cases: {n}",
        f"Intent accuracy (no mismatch warning): {intent_ok}/{n}",
        f"Scope accuracy (no mismatch warning): {scope_ok}/{n}",
        f"Focus accuracy (no mismatch warning): {focus_ok}/{n}",
        "",
        "[WARNINGS — DIMENSION MISMATCHES]",
        "",
    ]

    if not failed:
        lines.append("(none — no intent/scope/focus mismatch warnings.)")
        lines.append("")
    else:
        for tier, case, r in failed:
            cid = case["id"]
            instruction = case["instruction"]
            exp = case.get("expected") or {}
            dump = r["query_intent_dump"]
            lines.append("--- CASE ---")
            lines.append(f"ID: {cid}")
            lines.append(f"Tier: {tier}")
            lines.append(f"Instruction:")
            lines.append(instruction)
            lines.append("")
            lines.append("Expected:")
            lines.append(f"- intent_type: {_fmt_val(exp.get('intent_type'))}")
            lines.append(f"- scope: {_fmt_val(exp.get('scope'))}")
            lines.append(f"- focus: {_fmt_val(exp.get('focus'))}")
            lines.append("")
            lines.append("Actual:")
            lines.append(f"- intent_type: {_fmt_val(dump.get('intent_type'))}")
            lines.append(f"- scope: {_fmt_val(dump.get('scope'))}")
            lines.append(f"- focus: {_fmt_val(dump.get('focus'))}")
            lines.append(f"- symbols: {dump.get('symbols') or []}")
            lines.append(f"- keywords: {dump.get('keywords') or []}")
            lines.append(f"- raw_query_intent: {_compact_query_intent_json(dump)}")
            ws = r.get("warnings") or []
            if ws:
                lines.append("")
                lines.append("Warnings:")
                for w in ws:
                    lines.append(f"- {w}")
            lines.append("")

    lines.append("[FAILURE PATTERNS]")
    lines.append("")
    if pattern_lines:
        lines.extend(pattern_lines)
    else:
        lines.append("(none — no failure patterns observed in this run.)")
    lines.append("")

    lines.append("[GOOD CASES (OPTIONAL, MAX 3)]")
    lines.append("")
    good = passed[:3]
    if not good:
        lines.append("(none — no cases without dimension warnings.)")
    else:
        for tier, case, r in good:
            dump = r["query_intent_dump"]
            why = (
                "No dimension mismatch warnings; "
                f"symbols={dump.get('symbols') or []}, keywords={len(dump.get('keywords') or [])} keyword(s)."
            )
            lines.append("--- CASE ---")
            lines.append(f"ID: {case['id']}")
            lines.append(f"Tier: {tier}")
            lines.append(f"Why it worked: {why}")
            lines.append("")

    lines.append("=== END REPORT ===")
    lines.append("")
    lines.append("=== END COPY ===")
    lines.append("")
    print("\n".join(lines))


@pytest.mark.slow
@pytest.mark.query_intent_parser_eval
@pytest.mark.skipif(
    os.environ.get("QUERY_INTENT_PARSER_EVAL_LIVE") != "1",
    reason="Set QUERY_INTENT_PARSER_EVAL_LIVE=1 to run live QueryIntentParser eval (real LLM).",
)
def test_query_intent_parser_eval_suite() -> None:
    parser = _make_parser()
    records: list[tuple[Tier, dict[str, Any], dict[str, Any]]] = []
    null_field_failures: list[str] = []

    for tier in ("easy", "medium", "hard", "edge"):
        for case in _load_cases(tier):
            row = _run_one(parser, tier, case)
            records.append((tier, case, row))
            if not row["intent_correct"] or not row["scope_correct"] or not row["focus_correct"]:
                if case.get("skip_null_guard"):
                    continue
                dump = row["query_intent_dump"]
                null_field_failures.append(
                    f"{case['id']!r}: intent_type={dump.get('intent_type')!r} "
                    f"scope={dump.get('scope')!r} focus={dump.get('focus')!r}"
                )

    if not records:
        pytest.fail("No eval cases loaded.")

    _print_diagnostic_report(records)

    cov = count_categories_from_records(records)
    metrics = compute_category_metrics(
        records,
        row_hard_fail=lambda r: bool(r.get("row_hard_fail")),
    )
    print_edge_case_coverage_report("QueryIntentParser", cov, metrics=metrics)
    print_trace_log(
        "QueryIntentParser",
        records,
        row_hard_fail=lambda r: bool(r.get("row_hard_fail")),
    )
    print_edge_failure_patterns_diagnostic(
        "QueryIntentParser",
        [w for _, _, r in records for w in (r.get("warnings") or [])],
    )

    if null_field_failures:
        pytest.fail(
            "Null intent_type, scope, and/or focus in case(s):\n"
            + "\n".join(f"  - {line}" for line in null_field_failures)
        )
