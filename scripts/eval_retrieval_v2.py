#!/usr/bin/env python3
"""Retrieval v2 evaluation harness.

Base suite (15): exact symbols, snake_case, NL concepts, config, ambiguity.

Hard suite (20): longer NL, near-duplicate vocabulary, cross-module concepts,
implementation vs test disambiguation, daemon/HTTP edge, graph-localization,
v2 pipeline internals.

For each query: expected_file is a ground-truth path fragment that MUST appear
in a candidate path (substring match).

Usage:
    python3 scripts/eval_retrieval_v2.py [project_root]
    python3 scripts/eval_retrieval_v2.py [project_root] --hard    # 20 hard only
    python3 scripts/eval_retrieval_v2.py [project_root] --all     # 15 + 20
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

_here = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_here))
os.environ.setdefault("RETRIEVAL_PIPELINE_V2", "1")

EVAL_CASES = [
    # (query, expected_file_fragment, description)
    # ── Exact symbol / class ──────────────────────────────────────────────
    ("execute_patch",
     "editing/patch_executor.py",
     "exact fn name → impl file, not tests"),

    ("ActionGenerator",
     "agent_v2/runtime/action_generator.py",
     "CamelCase class → correct module"),

    ("reciprocal_rank_fusion",
     "agent/retrieval/rank_fusion.py",
     "exact fn name → rank_fusion primitive"),

    ("GraphStorage",
     "repo_graph/graph_storage.py",
     "exact class → graph storage"),

    ("run_retrieval_pipeline",
     "agent/retrieval/retrieval_pipeline.py",
     "exact fn → retrieval pipeline"),

    # ── snake_case function ───────────────────────────────────────────────
    ("filter_and_rank_search_results",
     "agent/retrieval/search_target_filter.py",
     "heuristic fn → search_target_filter"),

    ("plan_diff",
     "editing/diff_planner.py",
     "diff planning fn → diff_planner"),

    ("build_bm25_index",
     "agent/retrieval/bm25_retriever.py",
     "BM25 index builder"),

    # ── Cross-file concept (NL) ───────────────────────────────────────────
    ("how does the agent retry after a failed patch",
     "agent/orchestrator/replan_recovery.py",
     "NL: retry/replan logic"),

    ("where is context ranked and pruned before model sees it",
     "agent/retrieval/context_pruner.py",
     "NL: context pruning"),

    ("how does the planner generate steps from an instruction",
     "planner/planner.py",
     "NL: planner step generation"),

    # ── Config lookup ─────────────────────────────────────────────────────
    ("RETRIEVAL_PIPELINE_V2 flag configuration",
     "config/retrieval_config.py",
     "config flag lookup"),

    ("RERANKER_ENABLED config",
     "config/retrieval_config.py",
     "reranker config flag"),

    # ── Ambiguous / multi-word ────────────────────────────────────────────
    ("vector search embedding chromadb",
     "agent/retrieval/vector_retriever.py",
     "NL concept → vector retriever"),

    ("safety check file path outside project root",
     "editing/patch_executor.py",
     "NL: path safety in patch executor"),
]

# 20 harder cases — NL-heavy, cross-cutting, or easy to confuse with tests/fixtures
EVAL_HARD_CASES = [
    # Exact / rare symbols (stress graph + boost)
    ("retrieve_v2",
     "agent/retrieval/retrieval_pipeline_v2.py",
     "v2 entrypoint symbol"),

    ("create_reranker",
     "agent/retrieval/reranker/reranker_factory.py",
     "reranker factory singleton"),

    ("MiniLMReranker",
     "agent/retrieval/reranker/minilm_reranker.py",
     "canonical ONNX CPU reranker"),

    ("retrieval_daemon_available",
     "agent/retrieval/daemon_client.py",
     "daemon health for embedding routing"),

    ("prune_deterministic",
     "agent/retrieval/prune_deterministic.py",
     "v2 deterministic pruner"),

    ("graph_lookup",
     "agent/retrieval/graph_lookup.py",
     "split graph lookup primitive"),

    # Cross-module / dispatcher
    ("_search_fn",
     "agent/execution/step_dispatcher.py",
     "nested search entry (underscore prefix)"),

    ("ToolGraph",
     "agent/execution/tool_graph.py",
     "tool registry graph"),

    # Editing / safety NL (impl vs tests)
    ("rollback snapshot when patch apply throws exception",
     "editing/patch_executor.py",
     "NL: rollback semantics in executor"),

    ("AST patch validation before touching files on disk",
     "editing/patch_executor.py",
     "NL: preflight / validate patch"),

    # Retrieval internals (NL)
    ("where does reciprocal rank fusion merge bm25 and vector results",
     "agent/retrieval/rank_fusion.py",
     "NL: RRF merge location"),

    ("how is the repo symbol graph sqlite index opened and queried",
     "repo_graph/graph_storage.py",
     "NL: GraphStorage / sqlite"),

    ("graph guided localization of failure to source files",
     "agent/retrieval/localization/localization_engine.py",
     "NL: localization engine"),

    # Config / mode
    ("REACT_MODE environment variable default",
     "config/agent_runtime.py",
     "NL: REACT_MODE config"),

    ("ENABLE_LLM_BUNDLE_SELECTOR feature flag",
     "config/retrieval_config.py",
     "bundle selector flag in retrieval_config"),

    # Vector / index NL
    ("sentence transformer miniLM chroma persistent client per workspace",
     "agent/retrieval/vector_retriever.py",
     "NL: vector retriever + chroma scoping"),

    # Serena / MCP
    ("serena mcp find symbol search for pattern adapter",
     "agent/tools/serena_adapter.py",
     "NL: Serena integration"),

    # State / orchestration NL
    ("AgentState ranked_context search results after retrieval pipeline",
     "agent/memory/state.py",
     "NL: state holds ranked context"),

    ("bounded exploration loop exploration runner react",
     "agent_v2/runtime/exploration_runner.py",
     "NL: exploration phase runner"),

    # Near-duplicate wording (hard BM25)
    ("retry strategy stricter prompt more context widening budget",
     "agent/prompt_system/retry_strategies/retry_with_stricter_prompt.py",
     "NL: retry_with_stricter_prompt module"),
]


@dataclass
class EvalResult:
    query: str
    description: str
    expected: str
    rank: int | None          # 1-indexed, None = not found in top-20
    sources_at_rank: str      # source(s) that contributed the hit
    top3: list[str]           # top-3 files for quick scan


def run_eval(project_root: str, cases: list[tuple[str, str, str]]) -> list[EvalResult]:
    from agent.retrieval.candidate_schema import RetrievalInput
    from agent.retrieval.retrieval_pipeline_v2 import retrieve

    results = []
    for query, expected_frag, desc in cases:
        inp = RetrievalInput(
            query=query,
            project_root=project_root,
            top_k_per_source=15,
            max_snippets=20,
        )
        out = retrieve([inp.query], project_root=inp.project_root)[0]

        rank = None
        src_at_rank = ""
        for i, c in enumerate(out.candidates):
            if expected_frag in c.path:
                rank = i + 1
                src_at_rank = c.source.value
                break

        top3 = [
            f"{c.source.value}:{Path(c.path).name}:{c.symbol or ''}"
            for c in out.candidates[:3]
        ]
        results.append(EvalResult(
            query=query,
            description=desc,
            expected=expected_frag,
            rank=rank,
            sources_at_rank=src_at_rank,
            top3=top3,
        ))
    return results


def print_report(results: list[EvalResult], suite_label: str = "") -> None:
    passed = [r for r in results if r.rank is not None and r.rank <= 10]
    top20  = [r for r in results if r.rank is not None]
    failed = [r for r in results if r.rank is None]

    label = f" ({suite_label})" if suite_label else ""
    print(f"\n{'═'*72}")
    print(f"  RETRIEVAL v2 EVAL{label} — {len(results)} queries")
    print(f"  Pass (rank ≤ 10): {len(passed)}/{len(results)}  "
          f"In top-20: {len(top20)}/{len(results)}  "
          f"Missing: {len(failed)}/{len(results)}")
    print(f"{'═'*72}\n")

    fmt_rank = lambda r: f"rank {r.rank:2d} [{r.sources_at_rank}]" if r.rank else "NOT FOUND"
    status   = lambda r: "✅" if (r.rank and r.rank <= 10) else ("⚠️ " if r.rank else "❌")

    for r in results:
        print(f"{status(r)} {fmt_rank(r):20s}  {r.query!r}")
        print(f"   expect: {r.expected}")
        print(f"   top-3:  {' | '.join(r.top3)}")
        print()

    if failed:
        print(f"{'─'*72}")
        print(f"  ❌ NOT FOUND ({len(failed)}):")
        for r in failed:
            print(f"     {r.query!r}  → expected {r.expected}")
        print()

    # rank distribution
    ranks = [r.rank for r in results if r.rank]
    if ranks:
        avg = sum(ranks) / len(ranks)
        print(f"  Rank distribution (found {len(ranks)}/{len(results)}):")
        print(f"    avg={avg:.1f}  min={min(ranks)}  max={max(ranks)}")
        buckets = {"1": 0, "2-3": 0, "4-10": 0, "11-20": 0}
        for rank in ranks:
            if rank == 1:    buckets["1"] += 1
            elif rank <= 3:  buckets["2-3"] += 1
            elif rank <= 10: buckets["4-10"] += 1
            else:            buckets["11-20"] += 1
        for k, v in buckets.items():
            bar = "█" * v
            print(f"    rank {k:6s}: {bar} ({v})")
    print()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Run retrieval v2 eval harness.")
    parser.add_argument(
        "project_root",
        nargs="?",
        default=".",
        help="Repository root (default: cwd)",
    )
    g = parser.add_mutually_exclusive_group()
    g.add_argument(
        "--hard",
        action="store_true",
        help="Run only the 20 hard queries",
    )
    g.add_argument(
        "--all",
        action="store_true",
        help="Run base (15) + hard (20) = 35 queries",
    )
    args = parser.parse_args()

    project_root = str(Path(args.project_root).resolve())
    os.environ.setdefault("SERENA_PROJECT_DIR", project_root)
    print(f"project_root: {project_root}")

    if args.hard:
        cases = EVAL_HARD_CASES
        suite_label = "HARD x20"
    elif args.all:
        cases = EVAL_CASES + EVAL_HARD_CASES
        suite_label = "BASE+HARD x35"
    else:
        cases = EVAL_CASES
        suite_label = "BASE x15"

    results = run_eval(project_root, cases)
    print_report(results, suite_label=suite_label)


if __name__ == "__main__":
    main()
