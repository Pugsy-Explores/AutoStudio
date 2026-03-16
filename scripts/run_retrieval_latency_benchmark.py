#!/usr/bin/env python3
"""Retrieval latency benchmark. Target: search_latency < 1s, context_latency < 5s, agent_runtime < 10s."""

import os
import sys
import time

# Add project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.memory.state import AgentState
from agent.retrieval.retrieval_pipeline import run_retrieval_pipeline, search_candidates


def main() -> None:
    project_root = os.environ.get("SERENA_PROJECT_DIR") or os.getcwd()
    query = "expand graph implementation"

    state = AgentState(
        instruction=query,
        current_plan={"steps": []},
        context={"project_root": project_root, "trace_id": "bench", "instruction": query},
    )

    # Simulate search results for run_retrieval_pipeline (minimal anchors)
    search_results = []

    # 1. search_candidates latency
    t0 = time.perf_counter()
    candidates = search_candidates(query, project_root=project_root)
    search_latency = time.perf_counter() - t0

    # 2. context_latency (run_retrieval_pipeline with candidates as search_results)
    if candidates:
        # Convert to search_results format
        search_results = [
            {"file": c.get("file", ""), "symbol": c.get("symbol", ""), "snippet": c.get("snippet", ""), "line": 0}
            for c in candidates[:10]
        ]

    t0 = time.perf_counter()
    run_retrieval_pipeline(search_results, state, query=query)
    context_latency = time.perf_counter() - t0

    # 3. total_agent_runtime (simplified: search + context)
    agent_runtime = search_latency + context_latency

    print("[retrieval_benchmark]")
    print(f"  search_latency={search_latency:.3f}s")
    print(f"  context_latency={context_latency:.3f}s")
    print(f"  agent_runtime={agent_runtime:.3f}s")
    print(f"  target: search<1s, context<5s, agent<10s")
    ok = search_latency < 1.0 and context_latency < 5.0 and agent_runtime < 10.0
    print(f"  pass={ok}")


if __name__ == "__main__":
    main()
