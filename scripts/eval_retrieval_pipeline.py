#!/usr/bin/env python3
"""Retrieval pipeline evaluation script.

Validates the full mid-pipeline path:
  query → multi-retrieval → file-level merge → rerank → post-rerank top_k

Output per case:
  case_id | instruction | top N candidates (file + score + syms; padded to N if fewer) | PASS/FAIL
  (N = DISPLAY_TOP_N, default 10)

Usage:
  python3 scripts/eval_retrieval_pipeline.py
  python3 scripts/eval_retrieval_pipeline.py --cases 15
  python3 scripts/eval_retrieval_pipeline.py --multi-repo --per-repo-max 8 --max-total 40
  python3 scripts/eval_retrieval_pipeline.py --patterns   # concurrency / DB / utility / entry / vague (tiny GH repos)
  python3 scripts/eval_retrieval_pipeline.py --top-k 10 --no-rerank
  python3 scripts/eval_retrieval_pipeline.py --json     # machine-readable summary after text

Multi-repo: set ``EXPLORATION_TEST_REPOS`` (or ``AGENT_V2_EXPLORATION_TEST_REPOS_JSON``), run
``python3 scripts/index_exploration_test_repos.py`` to clone/index, then ``--multi-repo`` which sets
``AGENT_V2_APPEND_EXPLORATION_TEST_REPOS_TO_RETRIEVAL=1`` for merged retrieval.

Exit codes:
  0 — all cases PASS (or PASS+WARN threshold met)
  1 — too many FAILures

Pass rule: ``found_in_top5 and signal_rich``. Drift (test-heavy top-5) is warning-only
when the target is in top-5 — it must not veto pass, or rank #1 impl files still FAIL
when neighbors are tests (RCA 2026-03).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Repo root
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[1]

if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s %(name)s: %(message)s",
)
_LOG = logging.getLogger("eval_retrieval_pipeline")

# ---------------------------------------------------------------------------
# Case generation — shared with tests/retrieval/case_generation.py
# ---------------------------------------------------------------------------

from tests.retrieval.case_generation import (  # noqa: E402
    RetrievalEvalCase as EvalCase,
    build_default_local_cases,
    build_multi_repo_eval_cases,
)
from tests.retrieval import pattern_coverage as _pattern_coverage  # noqa: E402


# ---------------------------------------------------------------------------
# Engine bootstrap
# ---------------------------------------------------------------------------


def _majority_are_tests(candidates: list[Any], top_n: int = 5) -> bool:
    top = candidates[:top_n]
    if not top:
        return False
    count = sum(1 for c in top if "/test" in c.file_path or c.file_path.startswith("test_"))
    return count > len(top) // 2


def build_engine(repo_root: Path):
    """Build a minimal ExplorationEngineV2 with real dispatcher, stub LLM components."""
    os.environ.setdefault("SKIP_STARTUP_CHECKS", "1")
    os.environ["AGENT_V2_ENABLE_EXPLORATION_ENGINE_V2"] = "1"
    os.chdir(str(repo_root))
    os.environ["SERENA_PROJECT_DIR"] = str(repo_root)

    try:
        import numpy as np
        np.random.seed(42)
    except ImportError:
        pass

    from agent.tools.react_tools import register_all_tools
    register_all_tools()

    from agent.execution.step_dispatcher import _dispatch_react
    from agent_v2.runtime.dispatcher import Dispatcher
    from agent_v2.exploration.exploration_engine_v2 import ExplorationEngineV2
    from agent_v2.schemas.exploration import QueryIntent, ExplorationDecision
    from agent_v2.schemas.execution import ExecutionResult, ExecutionOutput

    class _StubParser:
        def parse(self, instruction: str, **_k) -> QueryIntent:
            return QueryIntent(symbols=[], keywords=[instruction], intents=["find_definition"])

    class _StubSelector:
        def select(self, *a, **k): return None
        def select_batch(self, *a, **k): return None

    class _StubReader:
        def inspect(self, *a, **k): return None

    class _StubAnalyzer:
        def analyze(self, *a, **k) -> ExplorationDecision:
            return ExplorationDecision(status="partial", needs=[], reason="stub", next_action="stop")

    class _StubGraph:
        def expand(self, symbol, file_path, state, *, max_nodes, max_depth):
            return [], ExecutionResult(
                step_id="stub",
                tool_name="graph_query",
                success=True,
                output=ExecutionOutput(summary="stub", data={}),
            )

    engine = ExplorationEngineV2(
        dispatcher=Dispatcher(execute_fn=_dispatch_react),
        intent_parser=_StubParser(),  # type: ignore[arg-type]
        selector=_StubSelector(),  # type: ignore[arg-type]
        inspection_reader=_StubReader(),  # type: ignore[arg-type]
        analyzer=_StubAnalyzer(),  # type: ignore[arg-type]
        graph_expander=_StubGraph(),  # type: ignore[arg-type]
    )
    return engine


# ---------------------------------------------------------------------------
# Evaluation run
# ---------------------------------------------------------------------------

# Always print this many rows per case (pad with placeholders if fewer candidates).
DISPLAY_TOP_N = 10


def _pad_top_slots(
    paths: list[str],
    scores: list[float | None],
    symbols: list[list[str]],
    n: int,
) -> tuple[list[str], list[float | None], list[list[str]]]:
    """Ensure fixed-length display rows for terminal output."""
    out_p = list(paths)
    out_s = list(scores)
    out_y = [list(x) for x in symbols]
    while len(out_p) < n:
        out_p.append("")
        out_s.append(None)
        out_y.append([])
    return out_p[:n], out_s[:n], out_y[:n]


@dataclass
class CaseResult:
    case_id: str
    instruction: str
    expected_file_hint: str
    repo: str
    category: str
    rank_cap: int
    top_paths: list[str]
    top_scores: list[float | None]
    top_symbols: list[list[str]]
    total_candidates: int
    found_in_any: bool
    found_in_top3: bool
    found_in_top5: bool
    found_in_top10: bool
    test_file_drift: bool
    signal_rich: bool
    elapsed_ms: float
    pass_: bool
    repo_match_ok: bool
    warnings: list[str] = field(default_factory=list)


def run_case(engine: Any, case: EvalCase) -> CaseResult:
    from agent_v2.schemas.exploration import QueryIntent

    intent = QueryIntent(
        symbols=[case.expected_symbol],
        keywords=case.keywords,
        intents=["find_definition"],
    )

    t0 = time.perf_counter()
    try:
        candidates = engine.run_retrieval_pipeline(case.instruction, intent)
    except Exception as exc:
        _LOG.exception("Case %s failed with exception", case.case_id)
        tp, ts, ty = _pad_top_slots([], [], [], DISPLAY_TOP_N)
        return CaseResult(
            case_id=case.case_id,
            instruction=case.instruction,
            expected_file_hint=case.expected_file_hint,
            repo=case.repo,
            category=case.category,
            rank_cap=case.rank_fail_after,
            top_paths=tp,
            top_scores=ts,
            top_symbols=ty,
            total_candidates=0,
            found_in_any=False,
            found_in_top3=False,
            found_in_top5=False,
            found_in_top10=False,
            test_file_drift=False,
            signal_rich=False,
            elapsed_ms=0.0,
            pass_=False,
            repo_match_ok=False,
            warnings=[f"EXCEPTION: {exc}"],
        )
    elapsed_ms = (time.perf_counter() - t0) * 1000

    all_paths = [c.file_path for c in candidates]
    found_in_any = any(case.expected_file_hint in p for p in all_paths)
    rank = next(
        (i + 1 for i, p in enumerate(all_paths) if case.expected_file_hint in p),
        None,
    )
    cap = case.rank_fail_after
    found_in_top3 = rank is not None and rank <= 3
    found_in_top5 = rank is not None and rank <= 5
    found_in_top10 = rank is not None and rank <= 10
    in_band = rank is not None and rank <= cap
    drift = _majority_are_tests(candidates, top_n=5)

    # Cross-repo: expected row should be tagged with the same repo when label resolves
    repo_match_ok = True
    if rank is not None and found_in_any:
        row = next((c for c in candidates if case.expected_file_hint in c.file_path), None)
        if row is not None and getattr(row, "repo", None) is not None:
            repo_match_ok = getattr(row, "repo", None) == case.repo

    # Signal richness check
    rich_failures = sum(
        1 for c in candidates
        if not getattr(c, "snippet_summary", None) or not getattr(c, "symbols", None)
    )
    signal_rich = rich_failures == 0

    warnings: list[str] = []
    if not found_in_any:
        warnings.append(f"MISS: '{case.expected_file_hint}' not in any candidate")
    elif rank is not None and rank > cap:
        warnings.append(f"RANK: expected file at rank {rank} (not in top-{cap})")
    elif rank is not None and cap <= 5 and 4 <= rank <= 5:
        warnings.append(
            f"RANK: expected file at rank {rank} (top-5 but not top-3 — soft pass)"
        )
    elif rank is not None and cap > 5 and 4 <= rank <= cap:
        warnings.append(
            f"RANK: expected file at rank {rank} (within top-{cap} but not top-3 — soft pass)"
        )
    # Drift is warning-only when the expected file is in top-5: ranking gates (top-3 / top-5
    # soft pass) already validated retrieval quality; a test-heavy neighborhood with the
    # implementation file highly ranked is not a failure (see eval RCA: drift veto vs rank).
    if drift:
        if found_in_top5:
            warnings.append(
                "DRIFT: majority of top-5 look like test paths (informational — target in top-5)"
            )
        else:
            warnings.append("DRIFT: majority of top-5 are test files")
    if rich_failures > 0:
        warnings.append(f"RICHNESS: {rich_failures}/{len(candidates)} candidates missing signal")
    if not repo_match_ok and found_in_any and rank is not None and rank <= cap:
        warnings.append(
            f"REPO: candidate.repo != expected {case.repo!r} (cross-repo label mismatch)"
        )

    pass_ = in_band and signal_rich and repo_match_ok

    raw_paths = all_paths[:DISPLAY_TOP_N]
    raw_scores = [
        getattr(c, "discovery_rerank_score", getattr(c, "discovery_max_score", None))
        for c in candidates[:DISPLAY_TOP_N]
    ]
    raw_symbols = [list(getattr(c, "symbols", None) or [])[:5] for c in candidates[:DISPLAY_TOP_N]]
    top_paths, top_scores, top_symbols = _pad_top_slots(raw_paths, raw_scores, raw_symbols, DISPLAY_TOP_N)

    return CaseResult(
        case_id=case.case_id,
        instruction=case.instruction,
        expected_file_hint=case.expected_file_hint,
        repo=case.repo,
        category=case.category,
        rank_cap=cap,
        top_paths=top_paths,
        top_scores=top_scores,
        top_symbols=top_symbols,
        total_candidates=len(candidates),
        found_in_any=found_in_any,
        found_in_top3=found_in_top3,
        found_in_top5=found_in_top5,
        found_in_top10=found_in_top10,
        test_file_drift=drift,
        signal_rich=signal_rich,
        elapsed_ms=elapsed_ms,
        pass_=pass_,
        repo_match_ok=repo_match_ok,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Printing
# ---------------------------------------------------------------------------

_BOLD = "\033[1m"
_GREEN = "\033[32m"
_RED = "\033[31m"
_YELLOW = "\033[33m"
_RESET = "\033[0m"


def _color(text: str, code: str, *, use_color: bool = True) -> str:
    if not use_color:
        return text
    return f"{code}{text}{_RESET}"


def print_case_result(result: CaseResult, *, use_color: bool) -> None:
    status = "PASS" if result.pass_ else "FAIL"
    color = _GREEN if result.pass_ else _RED
    badge = _color(f"[{status}]", color, use_color=use_color)
    print(f"\n{badge} {result.case_id}")
    print(f"  repo        : {result.repo}  category={result.category}  rank_cap={result.rank_cap}")
    print(f"  instruction : {result.instruction}")
    print(f"  expected    : {result.expected_file_hint}")
    print(f"  total cands : {result.total_candidates}  elapsed: {result.elapsed_ms:.0f}ms")

    if result.warnings:
        for w in result.warnings:
            print(f"  {_color('WARN', _YELLOW, use_color=use_color)} {w}")

    print(f"  top {DISPLAY_TOP_N} candidates:")
    for i, (path, score, syms) in enumerate(
        zip(result.top_paths, result.top_scores, result.top_symbols)
    ):
        if not path:
            print(f"    [{i:02d}]  —  (no candidate)  score=n/a  syms=[]")
            continue
        sc_str = f"{score:.4f}" if isinstance(score, float) else "n/a"
        marker = " ◀" if result.expected_file_hint in path else ""
        print(f"    [{i:02d}] {path}  score={sc_str}  syms={syms[:3]}{marker}")


def print_summary(results: list[CaseResult], *, use_color: bool) -> None:
    total = len(results)
    passed = sum(1 for r in results if r.pass_)
    failed = total - passed
    found_any = sum(1 for r in results if r.found_in_any)
    found_top3 = sum(1 for r in results if r.found_in_top3)
    found_top5 = sum(1 for r in results if r.found_in_top5)
    found_top10 = sum(1 for r in results if r.found_in_top10)
    drift_count = sum(1 for r in results if r.test_file_drift)

    print(f"\n{'─'*70}")
    print(f"  SUMMARY")
    print(f"  total cases   : {total}")
    print(f"  passed        : {_color(str(passed), _GREEN, use_color=use_color)}")
    print(f"  failed        : {_color(str(failed), _RED if failed else _GREEN, use_color=use_color)}")
    print(f"  found in any  : {found_any}/{total}")
    print(f"  found in top3 : {found_top3}/{total}")
    print(f"  found in top5 : {found_top5}/{total}")
    print(f"  found in top10: {found_top10}/{total}")
    print(f"  drift cases   : {drift_count}")

    by_repo: dict[str, list[CaseResult]] = {}
    for r in results:
        by_repo.setdefault(r.repo, []).append(r)
    print(f"\n  BY REPO")
    for repo, rows in sorted(by_repo.items()):
        rp = sum(1 for x in rows if x.pass_)
        print(f"    {repo}: {rp}/{len(rows)} pass")

    by_cat: dict[str, list[CaseResult]] = {}
    for r in results:
        by_cat.setdefault(r.category, []).append(r)
    print(f"\n  BY CATEGORY")
    for cat, rows in sorted(by_cat.items()):
        cp = sum(1 for x in rows if x.pass_)
        print(f"    {cat}: {cp}/{len(rows)} pass")
    print(f"{'─'*70}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Eval retrieval pipeline — mid-pipeline quality validation"
    )
    parser.add_argument("--cases", type=int, default=14, help="Max test cases to generate (default 14)")
    parser.add_argument(
        "--multi-repo",
        action="store_true",
        help="Use EXPLORATION_TEST_REPOS: multi-repo cases + append roots to retrieval (set env)",
    )
    parser.add_argument(
        "--per-repo-max",
        type=int,
        default=8,
        help="With --multi-repo: max cases per repo (default 8)",
    )
    parser.add_argument(
        "--max-total",
        type=int,
        default=40,
        help="With --multi-repo: cap total cases (default 40)",
    )
    parser.add_argument(
        "--patterns",
        action="store_true",
        help=(
            "Use tests/retrieval/pattern_sources.json: tiny GitHub clones + dimension-tagged cases "
            "(sets AGENT_V2_EXPLORATION_TEST_REPOS_JSON + append retrieval)"
        ),
    )
    parser.add_argument(
        "--no-index",
        action="store_true",
        help="With --patterns: do not auto-run symbol index on missing .symbol_graph",
    )
    parser.add_argument("--top-k", type=int, default=None, help="Override EXPLORATION_DISCOVERY_POST_RERANK_TOP_K")
    parser.add_argument("--no-rerank", action="store_true", help="Disable cross-encoder rerank")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON summary to stdout")
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI color codes")
    parser.add_argument("--fail-threshold", type=int, default=0,
                        help="Allow up to N failures before non-zero exit (default 0)")
    args = parser.parse_args(argv)

    use_color = not args.no_color and sys.stdout.isatty()

    # Apply overrides before importing agent modules
    if args.no_rerank:
        os.environ["AGENT_V2_EXPLORATION_DISCOVERY_RERANK_ENABLED"] = "0"
    if args.top_k is not None:
        os.environ["AGENT_V2_EXPLORATION_DISCOVERY_POST_RERANK_TOP_K"] = str(args.top_k)
    if args.multi_repo and not args.patterns:
        os.environ["AGENT_V2_APPEND_EXPLORATION_TEST_REPOS_TO_RETRIEVAL"] = "1"
    if args.patterns:
        os.environ["AGENT_V2_APPEND_EXPLORATION_TEST_REPOS_TO_RETRIEVAL"] = "1"

    print(f"{'─'*70}")
    print(f"  Retrieval Pipeline Eval — {_REPO_ROOT.name}")
    if args.patterns:
        mode = "pattern coverage (pattern_sources.json)"
    elif args.multi_repo:
        mode = "multi-repo (EXPLORATION_TEST_REPOS)"
    else:
        mode = "local agent_v2"
    print(f"  Mode: {mode}")
    if args.patterns:
        print("  Manifest-driven tiny repos: concurrency, database, utility, entrypoint, vague_query")
    elif not args.multi_repo:
        print(f"  Scanning agent_v2/ for real test cases (max={args.cases})")
    else:
        print(f"  per_repo_max={args.per_repo_max} max_total={args.max_total}")
    print(f"  Rerank: {'OFF' if args.no_rerank else 'ON'}")
    print(f"{'─'*70}")

    # Build cases
    if args.patterns:
        _pattern_coverage.apply_pattern_coverage_env(_REPO_ROOT)
        if not args.no_index and not _pattern_coverage.pattern_repos_indexed(_REPO_ROOT):
            print("  Indexing pattern repos (first run, may take a minute) ...")
            _pattern_coverage.index_pattern_repos(_REPO_ROOT, verbose=False)
        cases = _pattern_coverage.build_pattern_coverage_cases(_REPO_ROOT)
    elif args.multi_repo:
        cases = build_multi_repo_eval_cases(
            _REPO_ROOT,
            max_per_repo=args.per_repo_max,
            max_total=args.max_total,
        )
    else:
        cases = build_default_local_cases(_REPO_ROOT, max_cases=args.cases)
    print(f"  Generated {len(cases)} eval cases\n")

    # Boot engine
    print("  Bootstrapping engine + dispatcher ...")
    try:
        engine = build_engine(_REPO_ROOT)
    except Exception as exc:
        print(f"  {_color('ERROR', _RED, use_color=use_color)}: Failed to bootstrap engine: {exc}")
        import traceback
        traceback.print_exc()
        return 1

    # Run cases
    results: list[CaseResult] = []
    for i, case in enumerate(cases, 1):
        print(f"  Running case {i}/{len(cases)}: {case.case_id} ...", end="", flush=True)
        result = run_case(engine, case)
        results.append(result)
        status_sym = "✓" if result.pass_ else "✗"
        color = _GREEN if result.pass_ else _RED
        print(f"  {_color(status_sym, color, use_color=use_color)}")
        print_case_result(result, use_color=use_color)

    print_summary(results, use_color=use_color)

    if args.json:
        import dataclasses
        summary = {
            "total": len(results),
            "passed": sum(1 for r in results if r.pass_),
            "failed": sum(1 for r in results if not r.pass_),
            "found_in_top3": sum(1 for r in results if r.found_in_top3),
            "found_in_top5": sum(1 for r in results if r.found_in_top5),
            "found_in_top10": sum(1 for r in results if r.found_in_top10),
            "drift_cases": sum(1 for r in results if r.test_file_drift),
            "cases": [dataclasses.asdict(r) for r in results],
        }
        print("\n" + json.dumps(summary, indent=2))

    failures = sum(1 for r in results if not r.pass_)
    return 0 if failures <= args.fail_threshold else 1


if __name__ == "__main__":
    sys.exit(main())
