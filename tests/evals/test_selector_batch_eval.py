"""
Live LLM evaluation for CandidateSelector.select_batch (precision, relevance, complementarity).

Not a unit test: calls ``select_batch`` with the real selector batch model via
``call_reasoning_model`` (task EXPLORATION_SELECTOR_BATCH).

Run (repo root, credentials configured):

  export SELECTOR_BATCH_EVAL_LIVE=1
  pytest tests/evals/test_selector_batch_eval.py -v -s -m selector_batch_eval

CI default: skipped unless SELECTOR_BATCH_EVAL_LIVE=1.

YAML cases include ``candidates`` (same shape as scoper eval), ``instruction``, ``intent``,
``limit``, and ``expected`` with ``must_select`` / ``should_select``. String tokens match
**file_path / symbol first** (ordering-robust); integer indices are a fallback. Optional:
``complementarity``, ``soft_max_selections``, ``must_select_first`` (rank check).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Literal

import pytest
import yaml

from agent.models.model_client import call_reasoning_model
from agent.models.model_config import get_prompt_model_name_for_task
from agent_v2.config import EXPLORATION_SELECTOR_TOP_K
from agent_v2.exploration.candidate_selector import CandidateSelector
from agent_v2.exploration.exploration_task_names import EXPLORATION_TASK_SELECTOR_BATCH
from agent_v2.schemas.exploration import ExplorationCandidate, SelectorBatchResult

from tests.evals.edge_case_coverage import (
    compute_category_metrics,
    count_categories_from_records,
    print_edge_case_coverage_report,
    print_edge_failure_patterns_diagnostic,
    print_trace_log,
)

Tier = Literal["easy", "medium", "hard", "edge"]

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_EVAL_DIR = Path(__file__).resolve().parent / "selector_batch"
_CASE_FILES: dict[Tier, str] = {
    "easy": "easy_cases.yaml",
    "medium": "medium_cases.yaml",
    "hard": "hard_cases.yaml",
    "edge": "edge_cases.yaml",
}

_LOG = logging.getLogger(__name__)


def _load_cases(tier: Tier) -> list[dict[str, Any]]:
    path = _EVAL_DIR / _CASE_FILES[tier]
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(raw, list)
    return raw


def _resolve_file_path(rel_or_abs: str) -> str:
    p = Path(rel_or_abs)
    if p.is_absolute():
        return str(p)
    return str((_PROJECT_ROOT / rel_or_abs).resolve())


def _row_to_candidate(row: dict[str, Any]) -> ExplorationCandidate:
    src = row.get("source") or "graph"
    if src not in ("graph", "grep", "vector"):
        src = "graph"
    sym = row.get("symbol")
    if sym is not None and sym in ("", "null"):
        sym = None
    return ExplorationCandidate(
        file_path=_resolve_file_path(str(row["file_path"])),
        snippet=str(row.get("snippet") or ""),
        symbol=sym if isinstance(sym, str) else None,
        source=src,  # type: ignore[arg-type]
    )


def _needle_matches(c: ExplorationCandidate, needle: str) -> bool:
    n = (needle or "").strip()
    if not n:
        return False
    fp = c.file_path or ""
    if n in fp:
        return True
    try:
        if Path(fp).name == n:
            return True
    except OSError:
        pass
    if c.symbol and (n == c.symbol or n in c.symbol):
        return True
    for s in c.symbols or []:
        if n in s:
            return True
    sn = c.snippet or ""
    if n in sn:
        return True
    return False


def _path_or_symbol_matches(c: ExplorationCandidate, needle: str) -> bool:
    """Semantic anchor: file path or symbol (ordering-robust). Snippet-only is fallback."""
    n = (needle or "").strip()
    if not n:
        return False
    fp = c.file_path or ""
    if n in fp:
        return True
    try:
        if Path(fp).name == n or n == Path(fp).name:
            return True
    except OSError:
        pass
    if c.symbol and (n == c.symbol or n in c.symbol):
        return True
    for s in c.symbols or []:
        if n in s or s in n:
            return True
    return False


def _must_token_satisfied(
    token: Any,
    selected: list[ExplorationCandidate],
    top: list[ExplorationCandidate],
) -> bool:
    if isinstance(token, bool):
        return False
    if isinstance(token, int):
        if token < 0 or token >= len(top):
            return False
        want = top[token].file_path
        return any(c.file_path == want for c in selected)
    s = str(token).strip()
    if not s:
        return False
    if any(_path_or_symbol_matches(c, s) for c in selected):
        return True
    return any(_needle_matches(c, s) for c in selected)


def _make_selector() -> CandidateSelector:
    return CandidateSelector(
        llm_generate_batch=lambda prompt: call_reasoning_model(
            prompt, task_name=EXPLORATION_TASK_SELECTOR_BATCH
        ),
        model_name_batch=get_prompt_model_name_for_task(EXPLORATION_TASK_SELECTOR_BATCH),
    )


def _run_one(
    selector: CandidateSelector,
    tier: Tier,
    case: dict[str, Any],
) -> dict[str, Any]:
    cid = case["id"]
    trace_id = str(case.get("trace_id") or cid)
    instruction = case["instruction"]
    intent = str(case.get("intent") or "explanation")
    limit = int(case.get("limit") or 3)
    exp = case.get("expected") or {}
    raw_rows = case.get("candidates") or []
    if not raw_rows and not case.get("allow_empty_candidates"):
        pytest.fail(f"Case {cid}: missing `candidates`.")

    if not raw_rows and case.get("allow_empty_candidates"):
        return {
            "id": cid,
            "trace_id": trace_id,
            "tier": tier,
            "empty_failure": False,
            "error": None,
            "selected_paths": [],
            "warnings": [],
            "result_dump": None,
            "coverage_signal": None,
            "skipped_empty_pool": True,
        }

    candidates = [_row_to_candidate(dict(r)) for r in raw_rows]
    top = candidates[:EXPLORATION_SELECTOR_TOP_K]

    try:
        result: SelectorBatchResult = selector.select_batch(
            instruction,
            intent,
            candidates,
            limit=limit,
            explored_location_keys=None,
        )
    except Exception as e:
        return {
            "id": cid,
            "trace_id": trace_id,
            "tier": tier,
            "empty_failure": True,
            "error": repr(e),
            "selected_paths": [],
            "warnings": [f"selector raised: {e!r}"],
            "result_dump": None,
        }

    selected = list(result.selected_candidates)
    paths = [c.file_path for c in selected]

    if not selected:
        cov = result.coverage_signal
        if case.get("allow_empty_selection"):
            return {
                "id": cid,
                "trace_id": trace_id,
                "tier": tier,
                "empty_failure": False,
                "error": None,
                "selected_paths": paths,
                "warnings": [],
                "result_dump": result.model_dump(),
                "coverage_signal": cov,
            }
        return {
            "id": cid,
            "trace_id": trace_id,
            "tier": tier,
            "empty_failure": True,
            "error": None,
            "selected_paths": paths,
            "warnings": ["empty selection"],
            "result_dump": result.model_dump(),
            "coverage_signal": cov,
        }

    warnings: list[str] = []

    must_select = exp.get("must_select") or []
    for tok in must_select:
        if not _must_token_satisfied(tok, selected, top):
            warnings.append(f"missed high-signal candidate (must_select not satisfied: {tok!r})")
            _LOG.warning("[selector_batch_eval] case %s missed must_select %r", cid, tok)

    should_select = exp.get("should_select") or []
    for tok in should_select:
        if not _must_token_satisfied(tok, selected, top):
            warnings.append(f"missing should_select: {tok!r}")

    syms = [c.symbol for c in selected if c.symbol]
    if len(syms) >= 2 and len(syms) != len(set(syms)):
        warnings.append("redundant candidates (duplicate symbol picks)")

    comp = exp.get("complementarity") or {}
    if comp.get("required"):
        impl_s = list(comp.get("implementation_signals") or [])
        use_s = list(comp.get("usage_signals") or [])

        def _pool_match(subs: list[str]) -> bool:
            if not subs:
                return True
            return any(
                any(_needle_matches(c, sub) for sub in subs if str(sub).strip())
                for c in selected
            )

        if impl_s and not _pool_match(impl_s):
            warnings.append("missing complementarity (no implementation signal in selection)")
        if use_s and not _pool_match(use_s):
            warnings.append("missing complementarity (no usage/test signal in selection)")

    soft_max = exp.get("soft_max_selections")
    if soft_max is not None and len(selected) > int(soft_max):
        warnings.append("over-selection (too many candidates vs soft_max_selections)")

    msf = exp.get("must_select_first")
    if msf is not None and selected:
        try:
            idx = int(msf)
        except (TypeError, ValueError):
            idx = -1
        if 0 <= idx < len(top) and selected[0].file_path != top[idx].file_path:
            warnings.append(f"poor ranking (expected first pick to match input index {idx})")

    return {
        "id": cid,
        "trace_id": trace_id,
        "tier": tier,
        "empty_failure": False,
        "error": None,
        "selected_paths": paths,
        "warnings": warnings,
        "result_dump": result.model_dump(),
        "coverage_signal": result.coverage_signal,
    }


def _print_report(records: list[tuple[Tier, dict[str, Any], dict[str, Any]]]) -> None:
    n = len(records)
    hard_fails = [(t, c, r) for t, c, r in records if r.get("empty_failure")]
    warn_recs = [(t, c, r) for t, c, r in records if r.get("warnings")]

    patterns: set[str] = set()
    for _, _, r in records:
        for w in r.get("warnings") or []:
            wl = w.lower()
            if "over-selection" in wl:
                patterns.add("over-selection")
            if "poor ranking" in wl:
                patterns.add("poor ranking")
            if "complementarity" in wl:
                patterns.add("missing complementarity")
            if "missed high-signal" in wl or "must_select" in wl:
                patterns.add("missed implementation / must_select")
            if "redundant" in wl:
                patterns.add("redundant candidates")

    lines: list[str] = [
        "=== SELECTOR DIAGNOSTIC REPORT ===",
        "",
        "[SUMMARY]",
        f"Total cases: {n}",
        f"Failures (empty selection / error): {len(hard_fails)}",
        f"Cases with warnings: {len(warn_recs)}",
        "",
        "[FAILURES]",
        "",
    ]
    if not hard_fails:
        lines.append("(none)")
        lines.append("")
    else:
        for tier, case, r in hard_fails:
            lines.append("--- CASE ---")
            lines.append(f"ID: {case['id']}")
            lines.append(f"Tier: {tier}")
            lines.append("Instruction:")
            lines.append(case["instruction"])
            lines.append("")
            lines.append(f"Expected: {case.get('expected')}")
            lines.append(f"Actual: {r.get('selected_paths')!r} coverage={r.get('coverage_signal')!r}")
            if r.get("error"):
                lines.append(f"Error: {r['error']}")
            lines.append("Warnings:")
            lines.append("- empty selection (hard failure for this eval)")
            lines.append("")

    lines.append("[WARNINGS — DETAIL]")
    lines.append("")
    soft = [(t, c, r) for t, c, r in warn_recs if not r.get("empty_failure")]
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
            lines.append(f"Expected: {case.get('expected')}")
            lines.append(f"Actual: {r.get('selected_paths')!r}")
            lines.append("Warnings:")
            for w in r["warnings"]:
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
        row_hard_fail=lambda r: bool(r.get("empty_failure")),
    )
    print_edge_case_coverage_report("SelectorBatch", cov, metrics=metrics)
    print_trace_log(
        "SelectorBatch",
        records,
        row_hard_fail=lambda r: bool(r.get("empty_failure")),
    )
    wtxt: list[str] = []
    for _, _, r in records:
        wtxt.extend(r.get("warnings") or [])
    print_edge_failure_patterns_diagnostic("SelectorBatch", wtxt)


@pytest.mark.slow
@pytest.mark.selector_batch_eval
@pytest.mark.skipif(
    os.environ.get("SELECTOR_BATCH_EVAL_LIVE") != "1",
    reason="Set SELECTOR_BATCH_EVAL_LIVE=1 to run live CandidateSelector.select_batch eval.",
)
def test_selector_batch_eval_suite() -> None:
    selector = _make_selector()
    records: list[tuple[Tier, dict[str, Any], dict[str, Any]]] = []

    for tier in ("easy", "medium", "hard", "edge"):
        for case in _load_cases(tier):
            row = _run_one(selector, tier, case)
            records.append((tier, case, row))

    if not records:
        pytest.fail("No eval cases loaded.")

    _print_report(records)
    _print_edge_reports(records)

    failures = [r for _, _, r in records if r.get("empty_failure")]
    if failures:
        detail = "; ".join(
            f"{r['id']}(paths={r.get('selected_paths')!r}, err={r.get('error')!r})" for r in failures
        )
        pytest.fail(f"Selector batch eval hard failure(s) — empty selection or error: {detail}")
