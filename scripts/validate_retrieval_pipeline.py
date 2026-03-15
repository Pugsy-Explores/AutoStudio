#!/usr/bin/env python3
"""
Validate retrieval pipeline for Step 3: run pipeline with query and print each stage.

Usage:
  python scripts/validate_retrieval_pipeline.py [query]

Example:
  python scripts/validate_retrieval_pipeline.py "Explain StepExecutor"

Expected: ranked_context != []. If empty, retrieval bug.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate retrieval pipeline for Step 3")
    parser.add_argument("query", nargs="?", default="Explain StepExecutor")
    parser.add_argument("--project-root", default=".", help="Project root")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    if not project_root.exists():
        logger.error("Project root not found: %s", project_root)
        return 1

    from agent.memory.state import AgentState
    from agent.retrieval.graph_retriever import retrieve_symbol_context
    from agent.retrieval.retrieval_pipeline import run_retrieval_pipeline

    query = args.query
    print(f"Query: {query}")
    print(f"Project root: {project_root}")

    # 1. Graph retrieval (search)
    print("\n--- 1. Graph retrieval ---")
    graph_result = retrieve_symbol_context(query, project_root=str(project_root))
    if not graph_result:
        print("  [FAIL] Graph retriever returned None (run: python -m repo_index.index_repo .)")
        return 1
    results = graph_result.get("results", [])
    print(f"  results: {len(results)}")
    for r in results[:3]:
        print(f"    - {r.get('file')} {r.get('symbol')}")

    if not results:
        print("  [FAIL] No results from graph retrieval")
        return 1

    # 2. Run full pipeline (anchor detection, expand, build_context, rank, prune)
    print("\n--- 2. Run retrieval pipeline ---")
    state = AgentState(
        instruction=query,
        current_plan={"steps": []},
        context={"project_root": str(project_root), "instruction": query},
    )
    run_retrieval_pipeline(results, state, query=query)

    # 3. Check stages
    print("\n--- 3. Pipeline stages ---")
    print(f"  anchors: {len(state.context.get('retrieved_symbols', []))} symbols")
    print(f"  context_snippets: {len(state.context.get('context_snippets', []))}")
    print(f"  context_candidates: {len(state.context.get('context_candidates', []))}")
    ranked = state.context.get("ranked_context") or []
    print(f"  ranked_context: {len(ranked)}")

    if not ranked:
        print("\n[FAIL] ranked_context is empty! Retrieval bug.")
        return 1

    print("\n[PASS] ranked_context != []")
    for i, r in enumerate(ranked[:3]):
        snip = (r.get("snippet") or "")[:80]
        print(f"  {i+1}. {r.get('file')} {r.get('symbol')} ... {snip}...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
