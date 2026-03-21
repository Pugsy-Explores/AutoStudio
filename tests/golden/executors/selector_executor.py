"""
Selector executor — wrap bundle selector via retrieval pipeline.
Extracts selection_loss, selected_count from search_debug_records and ranked_context.
"""

from typing import Any, Dict


def run_selector(input_: Dict[str, Any]) -> Dict[str, Any]:
    """
    Run retrieval pipeline (includes selector when pool is large enough).
    Extracts selector metrics from output.
    """
    from tests.golden.executors.retrieval_executor import run_retrieval

    raw = run_retrieval(input_)
    project_root = input_.get("project_root")
    instruction = input_.get("instruction") or ""

    if not project_root:
        return {"metrics": {"selection_loss": False, "selected_count": 0}}

    from agent.memory.state import AgentState
    from agent.retrieval.graph_retriever import retrieve_symbol_context
    from agent.retrieval.retrieval_pipeline import run_retrieval_pipeline

    query = instruction
    graph_result = retrieve_symbol_context(query, project_root=project_root)
    results = (graph_result or {}).get("results") or []

    state = AgentState(
        instruction=instruction,
        current_plan={"plan_id": "golden", "steps": []},
        context={"project_root": project_root, "instruction": instruction},
    )

    run_retrieval_pipeline(results, state, query=query)

    records = state.context.get("search_debug_records") or []
    last_rec = records[-1] if records else {}
    selection_loss = last_rec.get("selection_loss", False)
    ranked = state.context.get("ranked_context") or []
    keep_ids = state.context.get("bundle_selector_keep_ids")
    selected_count = len(keep_ids) if keep_ids is not None else len(ranked)

    return {
        "metrics": {
            "selection_loss": selection_loss,
            "selected_count": selected_count,
        }
    }
