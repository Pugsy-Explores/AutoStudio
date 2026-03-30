#!/usr/bin/env python3
"""Retrieval v2 smoke test: runs 3 canonical queries and prints full trace.

Usage:
    RETRIEVAL_PIPELINE_V2=1 python scripts/test_retrieval_v2.py [project_root]

Default project_root: current working directory.

Output per query:
  [pre-RRF]   per-source candidate lists (file, symbol, snippet)
  [post-RRF]  merged ranked list before path validate/prune
  [final]     final candidate list after prune (what the planner sees)
  [warnings]  any adapter errors (index missing, serena unavailable, etc.)
"""

from __future__ import annotations

import json
import os
import sys
import textwrap
from pathlib import Path

# Bootstrap: add project root to path so imports work without install
_here = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_here))

os.environ.setdefault("RETRIEVAL_PIPELINE_V2", "1")

TEST_QUERIES = [
    # 1. Simple symbol lookup — exact class name
    "StepDispatcher",
    # 2. Cross-file concept — how edits are applied
    "patch executor apply diff",
    # 3. Ambiguous / natural language
    "where does the agent loop decide next action",
]


def _fmt_candidates(candidates: list[dict], *, max_rows: int = 8) -> str:
    lines = []
    for i, c in enumerate(candidates[:max_rows]):
        file = c.get("file", "")
        sym = c.get("symbol", "") or ""
        src = c.get("source", "")
        snip = (c.get("snippet") or "").replace("\n", " ").strip()[:80]
        rank_label = f"  [{i+1:2d}]"
        lines.append(f"{rank_label} {src:7s} {file}:{sym}")
        if snip:
            lines.append(f"       {textwrap.shorten(snip, 90)}")
    if len(candidates) > max_rows:
        lines.append(f"       ... ({len(candidates) - max_rows} more)")
    return "\n".join(lines) if lines else "       (empty)"


def run_query(query: str, project_root: str) -> None:
    from agent.retrieval.candidate_schema import RetrievalInput
    from agent.retrieval.retrieval_pipeline_v2 import retrieve

    print()
    print("═" * 70)
    print(f"  QUERY: {query!r}")
    print("═" * 70)

    inp = RetrievalInput(
        query=query,
        project_root=project_root,
        top_k_per_source=15,
        rrf_top_n=50,
        rrf_k=60,
        max_snippets=20,
        max_chars=20_000,
    )

    out = retrieve([inp.query], project_root=inp.project_root)[0]
    stages = out.stages

    # ── pre-RRF: per-source ─────────────────────────────────────────────────
    print("\n▶ PRE-RRF (per source):")
    pre = stages.get("pre_rrf") or {}
    for src in ("graph", "bm25", "vector", "serena"):
        src_data = pre.get(src) or {}
        count = src_data.get("count", 0)
        candidates = src_data.get("candidates") or []
        print(f"\n  {src.upper()} ({count} candidates):")
        print(_fmt_candidates(candidates))

    # ── post-RRF: merged list ────────────────────────────────────────────────
    print("\n▶ POST-RRF (merged, before validate/prune):")
    post = stages.get("post_rrf") or {}
    rrf_candidates = post.get("candidates") or []
    print(f"  Total: {post.get('count', 0)}")
    print(_fmt_candidates(rrf_candidates, max_rows=10))

    # ── validate ────────────────────────────────────────────────────────────
    val = stages.get("post_validate") or {}
    dropped = val.get("dropped", 0)
    if dropped:
        print(f"\n  [path_validate] dropped {dropped} rows (non-existent / outside root)")

    # ── final: what the planner sees ────────────────────────────────────────
    print(f"\n▶ FINAL (after prune) — {len(out.candidates)} candidates:")
    final_rows = [
        {
            "file": c.path,
            "symbol": c.symbol or "",
            "source": c.source.value,
            "snippet": (c.snippet or "")[:120],
        }
        for c in out.candidates
    ]
    print(_fmt_candidates(final_rows, max_rows=20))

    # ── warnings ────────────────────────────────────────────────────────────
    if out.warnings:
        print("\n  [warnings]")
        for w in out.warnings:
            print(f"    • {w}")

    print()


def main() -> None:
    project_root = sys.argv[1] if len(sys.argv) > 1 else str(Path.cwd())
    project_root = str(Path(project_root).resolve())
    os.environ.setdefault("SERENA_PROJECT_DIR", project_root)

    print(f"project_root: {project_root}")
    print(f"RETRIEVAL_PIPELINE_V2: {os.environ.get('RETRIEVAL_PIPELINE_V2', '0')}")

    for q in TEST_QUERIES:
        run_query(q, project_root)

    print("\n✓ Done. Review PRE-RRF vs POST-RRF ranks to validate fusion quality.")
    print("  If a source is empty (index missing), that's expected without indexing.")
    print("  Warnings show which adapters are unavailable in this environment.\n")


if __name__ == "__main__":
    main()
