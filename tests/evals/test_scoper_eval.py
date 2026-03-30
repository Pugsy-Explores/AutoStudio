"""
Live LLM evaluation for ExplorationScoper (candidate filtering recall vs precision).

Not a unit test: calls ``ExplorationScoper.scope`` with the real scoper model via
``call_reasoning_model`` (task EXPLORATION_SCOPER).

Run (repo root, credentials configured):

  export SCOPER_EVAL_LIVE=1
  pytest tests/evals/test_scoper_eval.py -v -s -m scoper_eval

Omit ``-s`` to hide the printed report.

CI default: skipped unless SCOPER_EVAL_LIVE=1.

Each YAML case extends the base schema with ``candidates``: synthetic discovery rows
(``file_path``, ``snippet``, optional ``symbol``, ``source``) resolved under the repo root.

``must_include`` uses **primary** matches (path/symbol) vs **incidental** (snippet-only);
snippet-only hits trigger a weak-signal warning.
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
from agent_v2.exploration.exploration_scoper import ExplorationScoper
from agent_v2.exploration.exploration_task_names import EXPLORATION_TASK_SCOPER
from agent_v2.schemas.exploration import ExplorationCandidate

from tests.evals.edge_case_coverage import (
    compute_category_metrics,
    count_categories_from_records,
    print_edge_case_coverage_report,
    print_edge_failure_patterns_diagnostic,
    print_trace_log,
)

Tier = Literal["easy", "medium", "hard", "edge"]

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_EVAL_DIR = Path(__file__).resolve().parent / "scoper"
_CASE_FILES: dict[Tier, str] = {
    "easy": "easy_cases.yaml",
    "medium": "medium_cases.yaml",
    "hard": "hard_cases.yaml",
    "edge": "edge_cases.yaml",
}

_LOG = logging.getLogger(__name__)

# Noise: warn when this many selected rows match neither must nor should (absolute).
_NOISE_ABS_THRESHOLD = 2
# Noise: warn when share of irrelevant selections exceeds this (with at least 2 selected).
_NOISE_RATIO_THRESHOLD = 0.6
# Low recall: single selected file while many unique paths were in the pool.
_LOW_RECALL_DEDUPE_MIN = 4


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


def _dedupe_count(candidates: list[ExplorationCandidate], scoper: ExplorationScoper) -> int:
    payload, _ = scoper._aggregate_payload_by_file_path(candidates)  # noqa: SLF001
    return len(payload)


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
    if c.symbol:
        if n == c.symbol or n in c.symbol:
            return True
    for s in c.symbols or []:
        if n in s or s in n:
            return True
    sn = c.snippet or ""
    if n in sn:
        return True
    return False


def _primary_match(c: ExplorationCandidate, needle: str) -> bool:
    """Core signal: file path or symbol — not snippet-only."""
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


def _matches_any_needle(c: ExplorationCandidate, needles: list[str]) -> bool:
    return any(_needle_matches(c, x) for x in needles if str(x).strip())


def _make_scoper() -> ExplorationScoper:
    return ExplorationScoper(
        llm_generate=lambda prompt: call_reasoning_model(prompt, task_name=EXPLORATION_TASK_SCOPER),
        model_name=get_prompt_model_name_for_task(EXPLORATION_TASK_SCOPER),
    )


def _run_one(
    scoper: ExplorationScoper,
    tier: Tier,
    case: dict[str, Any],
) -> dict[str, Any]:
    cid = case["id"]
    trace_id = str(case.get("trace_id") or cid)
    instruction = case["instruction"]
    exp = case.get("expected") or {}
    must_include = list(exp.get("must_include") or [])
    should_include = list(exp.get("should_include") or [])
    raw_rows = case.get("candidates") or []
    if not raw_rows and not case.get("allow_empty_candidates"):
        pytest.fail(f"Case {cid}: missing `candidates` in YAML.")

    if not raw_rows and case.get("allow_empty_candidates"):
        return {
            "id": cid,
            "trace_id": trace_id,
            "tier": tier,
            "empty_failure": False,
            "error": None,
            "selected_paths": [],
            "warnings": [],
            "missing_must": list(must_include),
            "missing_should": list(should_include),
            "dedupe_n": 0,
            "selected_n": 0,
            "irrelevant_paths": [],
            "skipped_empty_pool": True,
        }

    candidates = [_row_to_candidate(dict(r)) for r in raw_rows]
    dedupe_n = _dedupe_count(candidates, scoper)

    try:
        selected = scoper.scope(instruction, candidates)
    except Exception as e:
        return {
            "id": cid,
            "trace_id": trace_id,
            "tier": tier,
            "empty_failure": True,
            "error": repr(e),
            "selected_paths": [],
            "warnings": [f"scoper raised: {e!r}"],
            "missing_must": must_include,
            "missing_should": should_include,
            "dedupe_n": dedupe_n,
            "selected_n": 0,
        }

    paths = [c.file_path for c in selected]
    warnings: list[str] = []

    if not selected:
        warnings.append("empty output (hard failure signal)")
        return {
            "id": cid,
            "trace_id": trace_id,
            "tier": tier,
            "empty_failure": True,
            "error": None,
            "selected_paths": paths,
            "warnings": warnings,
            "missing_must": list(must_include),
            "missing_should": list(should_include),
            "dedupe_n": dedupe_n,
            "selected_n": 0,
        }

    missing_must = [m for m in must_include if not any(_needle_matches(c, m) for c in selected)]
    if missing_must:
        warnings.append("missing core implementation (must_include not covered)")
        for m in missing_must:
            _LOG.warning("[scoper_eval] case %s: missing must_include %r", cid, m)

    weak_only_must: list[str] = []
    for m in must_include:
        if m in missing_must:
            continue
        if not isinstance(m, str) or not str(m).strip():
            continue
        if any(_primary_match(c, str(m)) for c in selected):
            continue
        if any(_needle_matches(c, str(m)) for c in selected):
            weak_only_must.append(str(m))
    if weak_only_must:
        warnings.append(
            "matched only via weak signal (not core implementation): "
            f"{weak_only_must} — substring in snippet but not path/symbol anchor"
        )

    missing_should = [s for s in should_include if not any(_needle_matches(c, s) for c in selected)]
    if missing_should:
        warnings.append(f"missing should_include signals: {missing_should}")

    if len(selected) == 1 and dedupe_n >= _LOW_RECALL_DEDUPE_MIN:
        if must_include and not any(_needle_matches(selected[0], m) for m in must_include):
            warnings.append("low recall (single weak candidate vs must_include)")
        else:
            warnings.append("low recall (single selection with broad candidate pool)")

    needles_all = [str(x) for x in must_include + should_include if str(x).strip()]
    irrelevant: list[str] = []
    for c in selected:
        if needles_all and not _matches_any_needle(c, needles_all):
            irrelevant.append(c.file_path)
    if len(selected) >= 2:
        if len(irrelevant) >= _NOISE_ABS_THRESHOLD:
            warnings.append(
                f"noise: many irrelevant selections ({len(irrelevant)} rows match no expected signals)"
            )
        elif len(irrelevant) / len(selected) >= _NOISE_RATIO_THRESHOLD:
            warnings.append("noise: high share of selections match no expected signals")

    return {
        "id": cid,
        "trace_id": trace_id,
        "tier": tier,
        "empty_failure": False,
        "error": None,
        "selected_paths": paths,
        "warnings": warnings,
        "missing_must": missing_must,
        "missing_should": missing_should,
        "dedupe_n": dedupe_n,
        "selected_n": len(selected),
        "irrelevant_paths": irrelevant,
    }


def _print_report(records: list[tuple[Tier, dict[str, Any], dict[str, Any]]]) -> None:
    n = len(records)
    hard_fails = [(t, c, r) for t, c, r in records if r.get("empty_failure")]
    warn_cases = [(t, c, r) for t, c, r in records if r.get("warnings")]

    patterns: set[str] = set()
    for _, _, r in records:
        for w in r.get("warnings") or []:
            wl = w.lower()
            if "must_include" in w or "missing core" in wl:
                patterns.add("missing core modules")
            if "low recall" in wl:
                patterns.add("under-selection (recall)")
            if "noise" in wl:
                patterns.add("over-selection of peripheral files")
            if "should_include" in w:
                patterns.add("weak should-include coverage")
            if "weak signal" in wl or "not core implementation" in wl:
                patterns.add("snippet-only match (incidental vs primary)")

    lines: list[str] = [
        "=== SCOPER DIAGNOSTIC REPORT ===",
        "",
        "[SUMMARY]",
        f"Total cases: {n}",
        f"Hard failures (empty / exception): {len(hard_fails)}",
        f"Cases with warnings: {len(warn_cases)}",
        "",
    ]

    lines.append("[FAILURES — HIGH SIGNAL]")
    lines.append("")
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
            exp = case.get("expected") or {}
            lines.append(f"Missing expected: {exp.get('must_include') or []}")
            lines.append(f"Returned: {r.get('selected_paths') or []}")
            if r.get("error"):
                lines.append(f"Error: {r['error']}")
            lines.append("Warnings:")
            lines.append("- empty output or scoper could not return valid indices")
            lines.append("")

    lines.append("[WARNINGS — DETAIL]")
    lines.append("")
    warned_only = [(t, c, r) for t, c, r in warn_cases if not r.get("empty_failure")]
    if not warned_only:
        lines.append("(none)")
        lines.append("")
    else:
        for tier, case, r in warned_only:
            if not r.get("warnings"):
                continue
            lines.append("--- CASE ---")
            lines.append(f"ID: {case['id']}")
            lines.append(f"Tier: {tier}")
            lines.append("Instruction:")
            lines.append(case["instruction"])
            lines.append("")
            exp = case.get("expected") or {}
            lines.append(f"Missing expected: {r.get('missing_must') or exp.get('must_include')}")
            lines.append(f"Returned: {r.get('selected_paths') or []}")
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
    print_edge_case_coverage_report("Scoper", cov, metrics=metrics)
    print_trace_log(
        "Scoper",
        records,
        row_hard_fail=lambda r: bool(r.get("empty_failure")),
    )
    wtxt: list[str] = []
    for _, _, r in records:
        wtxt.extend(r.get("warnings") or [])
    print_edge_failure_patterns_diagnostic("Scoper", wtxt)


@pytest.mark.slow
@pytest.mark.scoper_eval
@pytest.mark.skipif(
    os.environ.get("SCOPER_EVAL_LIVE") != "1",
    reason="Set SCOPER_EVAL_LIVE=1 to run live ExplorationScoper eval (real LLM).",
)
def test_scoper_eval_suite() -> None:
    scoper = _make_scoper()
    records: list[tuple[Tier, dict[str, Any], dict[str, Any]]] = []

    for tier in ("easy", "medium", "hard", "edge"):
        for case in _load_cases(tier):
            row = _run_one(scoper, tier, case)
            records.append((tier, case, row))

    if not records:
        pytest.fail("No eval cases loaded.")

    _print_report(records)
    _print_edge_reports(records)

    failures = [r for _, _, r in records if r.get("empty_failure")]
    if failures:
        detail = "; ".join(
            f"{r['id']}(error={r.get('error')!r}, paths={r.get('selected_paths')!r})" for r in failures
        )
        pytest.fail(f"Scoper eval hard failure(s) — empty output or exception: {detail}")
