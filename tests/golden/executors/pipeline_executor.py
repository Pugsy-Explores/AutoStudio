"""
Pipeline executor — run retrieval → selector → exploration → explain.
Uses offline LLM stubs for determinism.
"""

from typing import Any, Dict


def run_pipeline(input_: Dict[str, Any]) -> Dict[str, Any]:
    """
    Run full pipeline with run_structural_agent_offline.
    input_ must have: instruction, project_root, repo_path (injected by test).
    Returns structure and metrics for golden evaluation.
    """
    instruction = input_.get("instruction") or ""
    repo_path = input_.get("repo_path") or "mini_repos/mr01_arch"
    task_id = input_.get("task_id") or "golden_pipeline"

    if not instruction:
        return {
            "structure": {"steps": 0, "search_steps": 0, "has_loop": False},
            "metrics": {
                "answer_supported": False,
                "termination_reason": None,
            },
        }

    from tests.agent_eval.real_execution import run_structural_agent_offline
    from tests.agent_eval.task_specs import TaskSpec

    from tests.agent_eval.task_specs import resolve_repo_dir

    spec = TaskSpec(
        task_id=task_id,
        layer="mini_repo",
        repo_id=repo_path.split("/")[-1],
        repo_path=repo_path,
        instruction=instruction,
        setup_commands=(),
        validation_commands=(),
        expected_artifacts=(),
        timeout_seconds=60,
        tags=(),
        grading_mode="explain_artifact",
        orchestration_path="compat",
        explain_required_substrings=(),
        evaluation_kind="execution_regression",
    )

    repo_dir = resolve_repo_dir(spec)
    if not repo_dir.is_dir():
        return {
            "structure": {"steps": 0, "search_steps": 0, "has_loop": False},
            "metrics": {
                "answer_supported": False,
                "termination_reason": "REPO_NOT_FOUND",
            },
        }

    result = run_structural_agent_offline(spec, str(repo_dir))

    rq = result.get("retrieval_quality_bundle") or {}
    loop_out = result.get("loop_output") or {}
    answer = result.get("answer", "") or (loop_out.get("output", "") if isinstance(loop_out, dict) else "")

    raw_completed = loop_out.get("completed_steps", 0) or 0
    completed = len(raw_completed) if isinstance(raw_completed, list) else (int(raw_completed) if isinstance(raw_completed, (int, float)) else 0)
    search_steps = _count_search_steps(loop_out)
    termination_reason = rq.get("termination_reason") or loop_out.get("terminal")
    has_loop = termination_reason == "LOOP_PROTECTION"

    exploration = {
        "exploration_used": rq.get("exploration_new_token_count") is not None or rq.get("exploration_used_new_token_count") is not None,
        "exploration_added_count": rq.get("exploration_new_token_count") or rq.get("exploration_used_new_token_count") or 0,
        "exploration_effective": rq.get("exploration_effective", False),
    }

    return {
        "structure": {
            "steps": completed,
            "search_steps": search_steps,
            "has_loop": has_loop,
        },
        "metrics": {
            "answer_supported": rq.get("answer_supported"),
            "termination_reason": termination_reason,
            "support_strength": rq.get("average_support_strength"),
        },
        "exploration": exploration,
        "answer": answer,
    }


def _count_search_steps(loop_out: dict) -> int:
    """Count SEARCH steps from execution trace."""
    steps = loop_out.get("step_results") or []
    if not steps:
        return 0
    count = 0
    for s in steps:
        if isinstance(s, dict) and (s.get("action") or "").upper() == "SEARCH":
            count += 1
        elif hasattr(s, "action") and (getattr(s, "action", "") or "").upper() == "SEARCH":
            count += 1
    return count
