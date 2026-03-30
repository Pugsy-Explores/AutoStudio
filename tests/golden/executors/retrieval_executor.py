"""
Retrieval executor — wrap retrieval pipeline.
Derives metrics from search_debug_records. No LLM calls.
"""

from typing import Any, Dict


def run_retrieval(input_: Dict[str, Any]) -> Dict[str, Any]:
    """
    Run retrieval pipeline and return evaluation view.
    input_ must have: instruction, project_root (injected by test via pre_hook).
    """
    instruction = input_.get("instruction") or ""
    project_root = input_.get("project_root")
    if not project_root:
        return {
            "metrics": {
                "retrieval_empty": True,
                "has_impl": False,
                "has_linked": False,
            }
        }

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
    retrieval_empty = last_rec.get("retrieval_empty", True)
    has_impl = last_rec.get("has_impl_in_pool") or last_rec.get("final_has_impl", False)
    has_linked = last_rec.get("has_linked_in_pool") or last_rec.get("final_has_linked", False)

    return {
        "metrics": {
            "retrieval_empty": retrieval_empty,
            "has_impl": has_impl,
            "has_linked": has_linked,
        }
    }
