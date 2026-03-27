#!/usr/bin/env python3
"""Retrieval pipeline evaluation script.

Validates the full mid-pipeline path:
  query → multi-retrieval → file-level merge → rerank → post-rerank top_k

Output per case:
  case_id | instruction | top results (file + score) | PASS/FAIL

Usage:
  python3 scripts/eval_retrieval_pipeline.py
  python3 scripts/eval_retrieval_pipeline.py --cases 15
  python3 scripts/eval_retrieval_pipeline.py --top-k 10 --no-rerank
  python3 scripts/eval_retrieval_pipeline.py --json     # machine-readable output

Exit codes:
  0 — all cases PASS (or PASS+WARN threshold met)
  1 — too many FAILures

Pass rule: ``found_in_top5 and signal_rich``. Drift (test-heavy top-5) is warning-only
when the target is in top-5 — it must not veto pass, or rank #1 impl files still FAIL
when neighbors are tests (RCA 2026-03).
"""

from __future__ import annotations

import argparse
import ast
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
# Repo scanning — mirrors tests/retrieval/test_retrieval_pipeline_behavior.py
# ---------------------------------------------------------------------------


def _iter_agent_py_files(root: Path, *, max_files: int = 400) -> list[Path]:
    out: list[Path] = []
    agent = root / "agent_v2"
    if not agent.is_dir():
        return out
    for p in sorted(agent.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        if p.name.startswith("test_"):
            continue
        out.append(p)
        if len(out) >= max_files:
            break
    return out


def _parse_top_level(path: Path) -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
    try:
        src = path.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(src, filename=str(path))
    except (OSError, SyntaxError):
        return [], []
    classes: list[tuple[str, int]] = []
    funcs: list[tuple[str, int]] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            classes.append((node.name, node.lineno))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            funcs.append((node.name, node.lineno))
    return classes, funcs


@dataclass
class EvalCase:
    case_id: str
    instruction: str
    expected_symbol: str
    expected_file_hint: str
    keywords: list[str] = field(default_factory=list)


def build_eval_cases(root: Path, *, max_cases: int = 14) -> list[EvalCase]:
    """Dynamically generate eval cases from real agent_v2/ symbols and paths."""
    agent = root / "agent_v2"

    _TARGET_MODULES: list[tuple[str, str]] = [
        ("exploration/exploration_engine_v2.py", "engine"),
        ("exploration/candidate_selector.py", "selector"),
        ("exploration/exploration_scoper.py", "scoper"),
        ("exploration/query_intent_parser.py", "intent_parser"),
        ("exploration/exploration_working_memory.py", "working_memory"),
        ("exploration/graph_expander.py", "graph_expander"),
        ("runtime/dispatcher.py", "dispatcher"),
        ("runtime/exploration_runner.py", "runner"),
        ("schemas/exploration.py", "schema"),
        ("config.py", "config"),
    ]

    cases: list[EvalCase] = []
    seen: set[str] = set()

    for rel_path, tag in _TARGET_MODULES:
        full = agent / rel_path
        if not full.is_file():
            continue
        classes, funcs = _parse_top_level(full)
        candidates = [(n, ln, "class") for n, ln in classes] + [(n, ln, "fn") for n, ln in funcs]
        for sym_name, _, sym_kind in candidates:
            if sym_name in seen:
                continue
            seen.add(sym_name)
            hint = str(full.relative_to(root))
            kws = [sym_name, tag]
            if sym_kind == "class":
                instruction = f"Find the {sym_name} class definition and its public interface"
            else:
                instruction = f"Locate the {sym_name} function implementation"
            cases.append(
                EvalCase(
                    case_id=f"{tag}_{sym_name}",
                    instruction=instruction,
                    expected_symbol=sym_name,
                    expected_file_hint=hint,
                    keywords=kws,
                )
            )
            if len(cases) >= max_cases:
                break
        if len(cases) >= max_cases:
            break

    return cases


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


@dataclass
class CaseResult:
    case_id: str
    instruction: str
    expected_file_hint: str
    top_paths: list[str]
    top_scores: list[float | None]
    top_symbols: list[list[str]]
    total_candidates: int
    found_in_any: bool
    found_in_top3: bool
    found_in_top5: bool
    test_file_drift: bool
    signal_rich: bool
    elapsed_ms: float
    pass_: bool
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
        return CaseResult(
            case_id=case.case_id,
            instruction=case.instruction,
            expected_file_hint=case.expected_file_hint,
            top_paths=[],
            top_scores=[],
            top_symbols=[],
            total_candidates=0,
            found_in_any=False,
            found_in_top3=False,
            found_in_top5=False,
            test_file_drift=False,
            signal_rich=False,
            elapsed_ms=0.0,
            pass_=False,
            warnings=[f"EXCEPTION: {exc}"],
        )
    elapsed_ms = (time.perf_counter() - t0) * 1000

    all_paths = [c.file_path for c in candidates]
    found_in_any = any(case.expected_file_hint in p for p in all_paths)
    rank = next(
        (i + 1 for i, p in enumerate(all_paths) if case.expected_file_hint in p),
        None,
    )
    found_in_top3 = rank is not None and rank <= 3
    found_in_top5 = rank is not None and rank <= 5
    drift = _majority_are_tests(candidates, top_n=5)

    # Signal richness check
    rich_failures = sum(
        1 for c in candidates
        if not getattr(c, "snippet_summary", None) or not getattr(c, "symbols", None)
    )
    signal_rich = rich_failures == 0

    warnings: list[str] = []
    if not found_in_any:
        warnings.append(f"MISS: '{case.expected_file_hint}' not in any candidate")
    elif rank is not None and rank > 5:
        warnings.append(f"RANK: expected file at rank {rank} (not top-5)")
    elif rank is not None and 4 <= rank <= 5:
        warnings.append(
            f"RANK: expected file at rank {rank} (top-5 but not top-3 — soft pass)"
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

    pass_ = found_in_top5 and signal_rich

    top_n = 10
    top_paths = all_paths[:top_n]
    top_scores = [getattr(c, "discovery_rerank_score", getattr(c, "discovery_max_score", None)) for c in candidates[:top_n]]
    top_symbols = [list(getattr(c, "symbols", None) or [])[:5] for c in candidates[:top_n]]

    return CaseResult(
        case_id=case.case_id,
        instruction=case.instruction,
        expected_file_hint=case.expected_file_hint,
        top_paths=top_paths,
        top_scores=top_scores,
        top_symbols=top_symbols,
        total_candidates=len(candidates),
        found_in_any=found_in_any,
        found_in_top3=found_in_top3,
        found_in_top5=found_in_top5,
        test_file_drift=drift,
        signal_rich=signal_rich,
        elapsed_ms=elapsed_ms,
        pass_=pass_,
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


def print_case_result(result: CaseResult, *, verbose: bool, use_color: bool) -> None:
    status = "PASS" if result.pass_ else "FAIL"
    color = _GREEN if result.pass_ else _RED
    badge = _color(f"[{status}]", color, use_color=use_color)
    print(f"\n{badge} {result.case_id}")
    print(f"  instruction : {result.instruction}")
    print(f"  expected    : {result.expected_file_hint}")
    print(f"  total cands : {result.total_candidates}  elapsed: {result.elapsed_ms:.0f}ms")

    if result.warnings:
        for w in result.warnings:
            print(f"  {_color('WARN', _YELLOW, use_color=use_color)} {w}")

    if verbose or not result.pass_:
        print(f"  top {len(result.top_paths)} candidates:")
        for i, (path, score, syms) in enumerate(
            zip(result.top_paths, result.top_scores, result.top_symbols)
        ):
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
    drift_count = sum(1 for r in results if r.test_file_drift)

    print(f"\n{'─'*70}")
    print(f"  SUMMARY")
    print(f"  total cases   : {total}")
    print(f"  passed        : {_color(str(passed), _GREEN, use_color=use_color)}")
    print(f"  failed        : {_color(str(failed), _RED if failed else _GREEN, use_color=use_color)}")
    print(f"  found in any  : {found_any}/{total}")
    print(f"  found in top3 : {found_top3}/{total}")
    print(f"  found in top5 : {found_top5}/{total}")
    print(f"  drift cases   : {drift_count}")
    print(f"{'─'*70}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Eval retrieval pipeline — mid-pipeline quality validation"
    )
    parser.add_argument("--cases", type=int, default=14, help="Max test cases to generate (default 14)")
    parser.add_argument("--top-k", type=int, default=None, help="Override EXPLORATION_DISCOVERY_POST_RERANK_TOP_K")
    parser.add_argument("--no-rerank", action="store_true", help="Disable cross-encoder rerank")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print top-10 candidates for every case")
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

    print(f"{'─'*70}")
    print(f"  Retrieval Pipeline Eval — {_REPO_ROOT.name}")
    print(f"  Scanning agent_v2/ for real test cases (max={args.cases})")
    print(f"  Rerank: {'OFF' if args.no_rerank else 'ON'}")
    print(f"{'─'*70}")

    # Build cases
    cases = build_eval_cases(_REPO_ROOT, max_cases=args.cases)
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
        print_case_result(result, verbose=args.verbose, use_color=use_color)

    print_summary(results, use_color=use_color)

    if args.json:
        import dataclasses
        summary = {
            "total": len(results),
            "passed": sum(1 for r in results if r.pass_),
            "failed": sum(1 for r in results if not r.pass_),
            "found_in_top3": sum(1 for r in results if r.found_in_top3),
            "found_in_top5": sum(1 for r in results if r.found_in_top5),
            "drift_cases": sum(1 for r in results if r.test_file_drift),
            "cases": [dataclasses.asdict(r) for r in results],
        }
        print("\n" + json.dumps(summary, indent=2))

    failures = sum(1 for r in results if not r.pass_)
    return 0 if failures <= args.fail_threshold else 1


if __name__ == "__main__":
    sys.exit(main())
