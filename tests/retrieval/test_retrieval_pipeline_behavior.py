"""Mid-pipeline retrieval harness.

Validates the full path:
  query → multi-retrieval → merge/dedupe → rerank → post-rerank top_k

NO mocking, NO stub data, NO synthetic symbols.
Uses real agent_v2/ code; tested AFTER rerank (not before).

Performance (RCA):
  Previously four separate parametrized tests each called ``run_retrieval_pipeline``
  for the same case (correctness / ranking / drift / richness), so 14 cases × 4 =
  56 full heavy runs per file.  The combined test below runs **once per case**
  and applies all assertions, plus one extra run for determinism on the first case only.

Run:
    pytest tests/retrieval/test_retrieval_pipeline_behavior.py -v
    pytest tests/retrieval/test_retrieval_pipeline_behavior.py -v -s   # with print output
    pytest tests/retrieval/ -m retrieval -v
"""

from __future__ import annotations

import ast
import logging
import os
import sys
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

_LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Repo root resolution
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]

if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# ---------------------------------------------------------------------------
# Dynamic repo scanning — no hardcoded symbol names
# ---------------------------------------------------------------------------


def _parse_top_level(path: Path) -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
    """Return (classes, functions) as (name, lineno) tuples from top-level AST nodes."""
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
class _TestCase:
    case_id: str
    instruction: str
    expected_symbol: str
    expected_file_hint: str
    keywords: list[str] = field(default_factory=list)
    alt_file_hints: list[str] = field(default_factory=list)


def _build_test_cases(root: Path, *, max_cases: int = 14) -> list[_TestCase]:
    agent = root / "agent_v2"
    if not agent.is_dir():
        raise RuntimeError(f"agent_v2/ not found under {root}")

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

    cases: list[_TestCase] = []
    seen_symbols: set[str] = set()

    for rel_path, tag in _TARGET_MODULES:
        full = agent / rel_path
        if not full.is_file():
            continue
        classes, funcs = _parse_top_level(full)
        candidates_sym = [(n, ln, "class") for n, ln in classes] + [(n, ln, "fn") for n, ln in funcs]
        for sym_name, _, sym_kind in candidates_sym:
            if sym_name in seen_symbols:
                continue
            seen_symbols.add(sym_name)
            hint = str(full.relative_to(root))
            kws = [sym_name, tag]
            if sym_kind == "class":
                instruction = f"Find the {sym_name} class definition and its public interface"
            else:
                instruction = f"Locate the {sym_name} function implementation"
            cases.append(
                _TestCase(
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

    if not cases:
        raise RuntimeError("Could not generate any test cases — is agent_v2/ populated?")

    return cases


_CASES: list[_TestCase] = _build_test_cases(_REPO_ROOT)


# ---------------------------------------------------------------------------
# Engine fixture — real dispatcher, stub LLM components
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def retrieval_engine():
    """
    Builds a minimal ExplorationEngineV2 with a real Dispatcher backed by
    _dispatch_react.  All LLM-facing components (intent_parser, selector,
    analyzer, scoper) are stubbed because run_retrieval_pipeline() never
    calls them — it exits before the scoper/selector loop.
    """
    os.environ.setdefault("SKIP_STARTUP_CHECKS", "1")
    os.environ["AGENT_V2_ENABLE_EXPLORATION_ENGINE_V2"] = "1"
    os.environ.setdefault("AGENT_V2_EXPLORATION_DISCOVERY_RERANK_ENABLED", "1")
    try:
        import numpy as np
        np.random.seed(42)
    except ImportError:
        pass

    original_cwd = os.getcwd()
    os.chdir(str(_REPO_ROOT))
    os.environ["SERENA_PROJECT_DIR"] = str(_REPO_ROOT)

    from agent.tools.react_tools import register_all_tools
    register_all_tools()

    from agent.execution.step_dispatcher import _dispatch_react
    from agent_v2.runtime.dispatcher import Dispatcher
    from agent_v2.exploration.exploration_engine_v2 import ExplorationEngineV2
    from agent_v2.schemas.exploration import QueryIntent, ExplorationDecision
    from agent_v2.schemas.execution import ExecutionResult, ExecutionOutput

    class _StubParser:
        def parse(self, instruction: str, **_kwargs) -> QueryIntent:
            return QueryIntent(symbols=[], keywords=[instruction], intents=["find_definition"])

    class _StubSelector:
        def select(self, *a, **kw) -> Any:
            return None
        def select_batch(self, *a, **kw) -> Any:
            return None

    class _StubReader:
        def inspect(self, *a, **kw):
            return None

    class _StubAnalyzer:
        def analyze(self, *a, **kw) -> ExplorationDecision:
            return ExplorationDecision(
                status="partial",
                needs=[],
                reason="stub",
                next_action="stop",
            )

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

    yield engine

    os.chdir(original_cwd)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _majority_are_tests(candidates: list[Any], top_n: int = 5) -> bool:
    top = candidates[:top_n]
    if not top:
        return False
    test_count = sum(
        1 for c in top
        if "/test" in c.file_path or c.file_path.startswith("test_")
    )
    return test_count > len(top) // 2


def _print_top_candidates(case: _TestCase, candidates: list[Any], n: int = 10) -> None:
    print(f"\n{'='*70}")
    print(f"  CASE: {case.case_id}")
    print(f"  INSTRUCTION: {case.instruction}")
    print(f"  EXPECTED FILE HINT: {case.expected_file_hint}")
    print(f"  EXPECTED SYMBOL: {case.expected_symbol}")
    print(f"  TOTAL CANDIDATES: {len(candidates)}")
    print(f"  TOP {n}:")
    for i, c in enumerate(candidates[:n]):
        max_sc = getattr(c, "discovery_max_score", None)
        rerank_sc = getattr(c, "discovery_rerank_score", None)
        syms = getattr(c, "symbols", []) or []
        chans = getattr(c, "source_channels", []) or []
        line = f"    [{i:02d}] {c.file_path}"
        if isinstance(max_sc, float):
            line += f"  max_score={max_sc:.4f}"
        print(line)
        print(f"         symbols={syms[:5]}  channels={chans}  rerank={rerank_sc}")
    print(f"{'='*70}")


# ---------------------------------------------------------------------------
# Tests — single pipeline run per case (A–D + bounds); determinism on first case only
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.retrieval


@pytest.mark.parametrize("case", _CASES, ids=[c.case_id for c in _CASES])
def test_retrieval_pipeline_mid_stage(retrieval_engine, case: _TestCase):
    """A–D + post-rerank cap in one run per case (fast); determinism on first case only."""
    from agent_v2.schemas.exploration import QueryIntent
    import agent_v2.config as cfg

    intent = QueryIntent(
        symbols=[case.expected_symbol],
        keywords=case.keywords,
        intents=["find_definition"],
    )
    candidates = retrieval_engine.run_retrieval_pipeline(case.instruction, intent)

    _print_top_candidates(case, candidates)

    # Post-rerank candidate count bound
    limit = cfg.EXPLORATION_DISCOVERY_POST_RERANK_TOP_K
    assert len(candidates) <= limit, (
        f"[{case.case_id}] Candidate count {len(candidates)} exceeds POST_RERANK_TOP_K={limit}"
    )

    all_paths = [c.file_path for c in candidates]

    # A. Retrieval correctness
    found = any(case.expected_file_hint in p for p in all_paths)
    if not found:
        alt_found = any(any(alt in p for p in all_paths) for alt in case.alt_file_hints)
        if not alt_found:
            pytest.fail(
                f"[{case.case_id}] Expected file hint '{case.expected_file_hint}' "
                f"not found in any of {len(candidates)} candidates.\n"
                f"Paths: {all_paths[:10]}"
            )

    # B. Ranking: top-3 = pass; ranks 4–5 = warn + pass; rank > 5 = fail
    if not candidates:
        pytest.skip(f"[{case.case_id}] No candidates returned — retriever may be cold")
    top_paths = [c.file_path for c in candidates[:5]]
    rank = next(
        (i + 1 for i, p in enumerate(all_paths) if case.expected_file_hint in p),
        None,
    )
    if rank is None:
        pytest.skip(f"[{case.case_id}] File not found anywhere — skip ranking assertion")
    if rank > 5:
        pytest.fail(
            f"[{case.case_id}] Expected '{case.expected_file_hint}' in top-5 but "
            f"found at rank {rank}.\nTop-5 paths: {top_paths}"
        )
    if 4 <= rank <= 5:
        warnings.warn(
            f"[{case.case_id}] Expected file at rank {rank} (in top-5 but not top-3)",
            UserWarning,
            stacklevel=1,
        )

    # C. Drift (pathological test-heavy top-5): warning only when target is in top-5.
    # RCA: vetoing pass on drift contradicted B — e.g. rank #1 impl file but 4 test neighbors
    # still failed. Ranking bands (top-3 / top-5) are the retrieval-quality gate.
    if _majority_are_tests(candidates, top_n=5):
        warnings.warn(
            f"[{case.case_id}] DRIFT: majority of top-5 look like test paths "
            f"(informational; expected file rank={rank})",
            UserWarning,
            stacklevel=1,
        )

    # D. Signal richness
    failures: list[str] = []
    for c in candidates:
        if not getattr(c, "snippet_summary", None):
            failures.append(f"  missing snippet_summary: {c.file_path}")
        if not getattr(c, "symbols", None):
            failures.append(f"  empty symbols list: {c.file_path}")
    if failures:
        pytest.fail(
            f"[{case.case_id}] Signal richness failures ({len(failures)}):\n"
            + "\n".join(failures[:10])
        )

    # Determinism: second identical run (first case only — avoids N extra runs)
    if case.case_id == _CASES[0].case_id:
        run2 = retrieval_engine.run_retrieval_pipeline(case.instruction, intent)
        assert [c.file_path for c in candidates] == [c.file_path for c in run2], (
            "Retrieval pipeline is non-deterministic!\n"
            f"Run 1: {all_paths}\nRun 2: {[c.file_path for c in run2]}"
        )
