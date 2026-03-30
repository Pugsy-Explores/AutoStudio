"""
Live LLM evaluation for UnderstandingAnalyzer (understanding correctness, gap quality, grounding).

Calls ``UnderstandingAnalyzer.analyze`` with the real analyzer model (EXPLORATION_ANALYZER).

Run:

  export ANALYZER_EVAL_LIVE=1
  pytest tests/evals/test_analyzer_eval.py -v -s -m analyzer_eval

CI: skipped unless ANALYZER_EVAL_LIVE=1.

YAML: ``instruction``, ``intent``, ``context`` (list of ContextBlock-shaped dicts or raw strings),
``expected.understanding`` ∈ {sufficient, partial, insufficient}, ``expected.gap_quality`` ∈
{high, medium, low}. For ``gap_quality: high``, gaps must include an **actionable target**
(file/module/class/function anchor), not only length/non-generic wording. Optional:
``terms_absent_from_context`` for grounding checks.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Literal

import pytest
import yaml

from agent.models.model_client import call_reasoning_model
from agent.models.model_config import get_prompt_model_name_for_task
from agent_v2.exploration.exploration_task_names import EXPLORATION_TASK_ANALYZER
from agent_v2.exploration.understanding_analyzer import UnderstandingAnalyzer
from agent_v2.schemas.exploration import ContextBlock, UnderstandingResult

from tests.evals.edge_case_coverage import (
    compute_category_metrics,
    count_categories_from_records,
    print_edge_case_coverage_report,
    print_edge_failure_patterns_diagnostic,
    print_trace_log,
)

Tier = Literal["easy", "medium", "hard", "edge"]

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_EVAL_DIR = Path(__file__).resolve().parent / "analyzer"
_CASE_FILES: dict[Tier, str] = {
    "easy": "easy_cases.yaml",
    "medium": "medium_cases.yaml",
    "hard": "hard_cases.yaml",
    "edge": "edge_cases.yaml",
}

_LOG = logging.getLogger(__name__)

_GENERIC_GAP_WORDS = frozenset(
    {
        "need",
        "more",
        "information",
        "context",
        "code",
        "see",
        "also",
        "should",
        "could",
        "would",
        "must",
        "the",
        "and",
        "for",
        "to",
        "a",
        "an",
        "or",
        "not",
        "provided",
        "additional",
        "further",
    }
)


def _load_cases(tier: Tier) -> list[dict[str, Any]]:
    path = _EVAL_DIR / _CASE_FILES[tier]
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(raw, list)
    return raw


def _resolve_path(rel_or_abs: str) -> str:
    p = Path(rel_or_abs)
    if p.is_absolute():
        return str(p)
    return str((_PROJECT_ROOT / rel_or_abs).resolve())


def _context_blocks_from_yaml(raw: list[Any]) -> list[ContextBlock]:
    blocks: list[ContextBlock] = []
    for i, item in enumerate(raw or []):
        if isinstance(item, str):
            blocks.append(
                ContextBlock(
                    file_path=f"eval_inline_{i}.txt",
                    start=1,
                    end=max(2, len(item)),
                    content=item,
                )
            )
            continue
        if not isinstance(item, dict):
            continue
        fp = str(item.get("file_path") or f"eval_context_{i}.py")
        if not Path(fp).is_absolute() and not fp.startswith("eval_"):
            fp = _resolve_path(fp)
        start = int(item.get("start") or 1)
        end = int(item.get("end") or max(start + 1, start))
        content = str(item.get("content") or "")
        blocks.append(
            ContextBlock(
                file_path=fp,
                start=start,
                end=end,
                content=content,
                symbol=item.get("symbol") if isinstance(item.get("symbol"), str) else None,
            )
        )
    return blocks


def _context_blob(blocks: list[ContextBlock]) -> str:
    parts = [b.content for b in blocks]
    paths = [b.file_path for b in blocks]
    return "\n".join(parts).lower() + "\n" + "\n".join(paths).lower()


def _context_paths(blocks: list[ContextBlock]) -> set[str]:
    return {b.file_path for b in blocks}


def _all_gaps(ur: UnderstandingResult) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for g in list(ur.knowledge_gaps) + list(ur.gaps_relevant_to_intent):
        s = (g or "").strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _actual_tier(ur: UnderstandingResult) -> str:
    if ur.effective_sufficient:
        return "sufficient"
    ev = ur.evidence_sufficiency
    if ev == "insufficient":
        return "insufficient"
    if ev == "partial":
        return "partial"
    if ev == "sufficient":
        return "sufficient"
    return "partial"


def _gap_is_vague(g: str) -> bool:
    g = g.strip()
    if len(g) < 14:
        return True
    words = set(re.findall(r"[a-zA-Z]+", g.lower()))
    if not words:
        return True
    if words <= _GENERIC_GAP_WORDS:
        return True
    return False


def _gap_has_actionable_target(g: str) -> bool:
    """
    True if the gap names a concrete anchor: file, module path, class, function, or qualified name.
    Filters out long-but-fluffy text that lacks any entity to act on.
    """
    g = g.strip()
    if not g:
        return False
    patterns = [
        r"\.py\b",
        r"\b(?:def|class)\s+[A-Za-z_][\w]*",
        r"\b[A-Z][a-zA-Z0-9_]{2,}\b(?:\.[A-Za-z_][\w]*)?",  # Class or Class.method
        r"\b[a-z][a-z0-9_]{1,}(?:\.[a-z][a-z0-9_]*)+\b",  # module.sub or a.b.c
        r"/[\w./-]+\.(?:py|ts|js|tsx|jsx)\b",
        r"`[^`]+\.(?:py|ts|js)`",
    ]
    if any(re.search(p, g) for p in patterns):
        return True
    return False


def _ungrounded_py_paths(text: str, paths: set[str]) -> list[str]:
    found: list[str] = []
    for m in re.finditer(r"[\w\-./]+\.py\b", text):
        p = m.group(0).strip()
        if not p or ("/" not in p and not p.startswith("agent") and not p.startswith("tests")):
            continue
        ok = any(p in cp or cp.endswith(p) or p.endswith(Path(cp).name) for cp in paths)
        if not ok:
            found.append(p)
    return found[:5]


def _is_invalid_output(ur: UnderstandingResult) -> bool:
    joined = " ".join(ur.knowledge_gaps)
    if "non-object JSON" in joined or "invalid JSON" in joined.lower():
        return True
    if "invalid JSON object" in (ur.summary or "").lower():
        return True
    return False


def _make_analyzer() -> UnderstandingAnalyzer:
    return UnderstandingAnalyzer(
        llm_generate=lambda prompt: call_reasoning_model(prompt, task_name=EXPLORATION_TASK_ANALYZER),
        model_name=get_prompt_model_name_for_task(EXPLORATION_TASK_ANALYZER),
    )


def _run_one(
    analyzer: UnderstandingAnalyzer,
    tier: Tier,
    case: dict[str, Any],
) -> dict[str, Any]:
    cid = case["id"]
    trace_id = str(case.get("trace_id") or cid)
    instruction = case["instruction"]
    intent = str(case.get("intent") or "explanation")
    raw_ctx = case.get("context")
    blocks = _context_blocks_from_yaml(raw_ctx or [])
    if not blocks and case.get("allow_empty_context"):
        blocks = [
            ContextBlock(
                file_path="eval_empty_context.py",
                start=1,
                end=2,
                content="",
            )
        ]
    elif not blocks:
        pytest.fail(f"Case {cid}: context must be non-empty.")

    exp = case.get("expected") or {}
    exp_tier = str(exp.get("understanding") or "partial").strip().lower()
    if exp_tier not in ("sufficient", "partial", "insufficient"):
        exp_tier = "partial"
    exp_gap_q = str(exp.get("gap_quality") or "medium").strip().lower()
    if exp_gap_q not in ("high", "medium", "low"):
        exp_gap_q = "medium"
    absent_terms = [str(x).strip() for x in (exp.get("terms_absent_from_context") or []) if str(x).strip()]

    try:
        ur = analyzer.analyze(
            instruction,
            intent=intent,
            context_blocks=blocks,
            task_intent_summary="",
            symbol_relationships_block="",
        )
    except Exception as e:
        return {
            "id": cid,
            "trace_id": trace_id,
            "tier": tier,
            "invalid_failure": True,
            "error": repr(e),
            "warnings": [f"analyzer raised: {e!r}"],
            "result_dump": None,
        }

    if _is_invalid_output(ur):
        return {
            "id": cid,
            "trace_id": trace_id,
            "tier": tier,
            "invalid_failure": True,
            "error": "coerced invalid JSON / placeholder UnderstandingResult",
            "warnings": ["invalid output from analyzer pipeline"],
            "result_dump": ur.model_dump(),
        }

    warnings: list[str] = []
    act_tier = _actual_tier(ur)
    blob = _context_blob(blocks)
    paths = _context_paths(blocks)
    gaps = _all_gaps(ur)
    combined_text = (ur.semantic_understanding + " " + ur.summary).lower()

    tier_order = {"insufficient": 0, "partial": 1, "sufficient": 2}
    eo = tier_order.get(exp_tier, 1)
    ao = tier_order.get(act_tier, 1)
    if ao > eo:
        if exp_tier == "insufficient" and act_tier == "sufficient":
            warnings.append("overconfidence (sufficient vs expected insufficient)")
        elif exp_tier == "partial" and act_tier == "sufficient":
            warnings.append("tier mismatch (sufficient vs expected partial)")
    if eo > ao and exp_tier == "sufficient":
        warnings.append("missed sufficiency (expected sufficient, got lower tier)")

    missing_core = [t for t in absent_terms if t.lower() not in blob]
    if missing_core and (act_tier == "sufficient" or ur.effective_sufficient):
        warnings.append(f"says sufficient when missing core logic (terms not in context: {missing_core!r})")
    for term in absent_terms:
        tl = term.lower()
        if tl in blob:
            continue
        if tl in combined_text:
            warnings.append(f"hallucinated conclusions (term not evidenced in context: {term!r})")

    if exp_gap_q == "high" and gaps:
        if all(_gap_is_vague(g) for g in gaps):
            warnings.append("vague gaps (expected higher specificity)")
        lacking_target = [g for g in gaps if not _gap_has_actionable_target(g)]
        if lacking_target:
            warnings.append(
                "gap lacks actionable target (no function/class/module/file anchor in "
                f"{len(lacking_target)} gap(s))"
            )

    if act_tier == "partial" and not gaps:
        warnings.append("missing gaps (partial evidence but no gaps listed)")

    if act_tier == "insufficient" and not gaps:
        warnings.append("missing gaps (insufficient tier with empty gap list)")

    for py_path in _ungrounded_py_paths(ur.semantic_understanding + " " + ur.summary, paths):
        warnings.append(f"possible hallucinated file reference: {py_path}")

    warnings = list(dict.fromkeys(warnings))

    return {
        "id": cid,
        "trace_id": trace_id,
        "tier": tier,
        "invalid_failure": False,
        "error": None,
        "expected_tier": exp_tier,
        "actual_tier": act_tier,
        "gaps": gaps,
        "warnings": warnings,
        "result_dump": ur.model_dump(),
    }


def _print_report(records: list[tuple[Tier, dict[str, Any], dict[str, Any]]]) -> None:
    n = len(records)
    fails = [(t, c, r) for t, c, r in records if r.get("invalid_failure")]
    warned = [(t, c, r) for t, c, r in records if r.get("warnings")]

    patterns: set[str] = set()
    for _, _, r in records:
        for w in r.get("warnings") or []:
            wl = w.lower()
            if "vague" in wl:
                patterns.add("weak gap specificity")
            if "overconfidence" in wl or "sufficient when missing" in wl:
                patterns.add("overconfidence")
            if "hallucinat" in wl or "not evidenced" in wl:
                patterns.add("ungrounded claims")
            if "missing gap" in wl:
                patterns.add("missing gaps")
            if "missed sufficiency" in wl:
                patterns.add("poor ranking / undershoot")
            if "actionable target" in wl:
                patterns.add("gaps without concrete anchor")

    lines: list[str] = [
        "=== ANALYZER DIAGNOSTIC REPORT ===",
        "",
        "[SUMMARY]",
        f"Total cases: {n}",
        f"Failures (invalid output / exception): {len(fails)}",
        f"Cases with warnings: {len(warned)}",
        "",
        "[FAILURES]",
        "",
    ]
    if not fails:
        lines.append("(none)")
        lines.append("")
    else:
        for tier, case, r in fails:
            lines.append("--- CASE ---")
            lines.append(f"ID: {case['id']}")
            lines.append(f"Tier: {tier}")
            lines.append("Instruction:")
            lines.append(case["instruction"])
            lines.append("")
            lines.append(f"Understanding: expected={case.get('expected', {}).get('understanding')!r} actual=(n/a)")
            lines.append(f"Error: {r.get('error')}")
            lines.append("Warnings:")
            for w in r.get("warnings") or []:
                lines.append(f"- {w}")
            lines.append("")

    lines.append("[WARNINGS — DETAIL]")
    lines.append("")
    soft = [(t, c, r) for t, c, r in warned if not r.get("invalid_failure")]
    if not soft:
        lines.append("(none)")
        lines.append("")
    else:
        for tier, case, r in soft:
            lines.append("--- CASE ---")
            lines.append(f"ID: {case['id']}")
            lines.append(f"Tier: {tier}")
            lines.append("Instruction:")
            lines.append(case["instruction"])
            lines.append("")
            exp = case.get("expected") or {}
            lines.append(
                f"Understanding: expected={exp.get('understanding')!r} "
                f"actual={r.get('actual_tier')!r}"
            )
            lines.append("Gaps:")
            for g in r.get("gaps") or []:
                lines.append(f"  - {g}")
            if not r.get("gaps"):
                lines.append("  (none)")
            lines.append("Warnings:")
            for w in r.get("warnings") or []:
                lines.append(f"- {w}")
            lines.append("")

    lines.append("[FAILURE PATTERNS]")
    lines.append("")
    if patterns:
        for p in sorted(patterns):
            lines.append(f"- {p}")
    else:
        lines.append("(none)")
    lines.append("")
    lines.append("=== END REPORT ===")
    lines.append("")
    print("\n".join(lines))


def _print_edge_reports(records: list[tuple[Tier, dict[str, Any], dict[str, Any]]]) -> None:
    cov = count_categories_from_records(records)
    metrics = compute_category_metrics(
        records,
        row_hard_fail=lambda r: bool(r.get("invalid_failure")),
    )
    print_edge_case_coverage_report("Analyzer", cov, metrics=metrics)
    print_trace_log(
        "Analyzer",
        records,
        row_hard_fail=lambda r: bool(r.get("invalid_failure")),
    )
    wtxt: list[str] = []
    for _, _, r in records:
        wtxt.extend(r.get("warnings") or [])
    print_edge_failure_patterns_diagnostic("Analyzer", wtxt)


@pytest.mark.slow
@pytest.mark.analyzer_eval
@pytest.mark.skipif(
    os.environ.get("ANALYZER_EVAL_LIVE") != "1",
    reason="Set ANALYZER_EVAL_LIVE=1 to run live UnderstandingAnalyzer eval.",
)
def test_analyzer_eval_suite() -> None:
    analyzer = _make_analyzer()
    records: list[tuple[Tier, dict[str, Any], dict[str, Any]]] = []

    for tier in ("easy", "medium", "hard", "edge"):
        for case in _load_cases(tier):
            row = _run_one(analyzer, tier, case)
            records.append((tier, case, row))

    if not records:
        pytest.fail("No eval cases loaded.")

    _print_report(records)
    _print_edge_reports(records)

    bad = [r for _, _, r in records if r.get("invalid_failure")]
    if bad:
        detail = "; ".join(f"{r['id']}(err={r.get('error')!r})" for r in bad)
        pytest.fail(f"Analyzer eval hard failure(s) — invalid output or exception: {detail}")
