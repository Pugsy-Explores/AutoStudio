"""
Real execution_loop wiring for agent_eval (Stage 12.1): same get_parent_plan / get_plan injection
as mocked runs, but no execution_loop mock. LLM calls are stubbed at module boundaries so runs stay
offline (no HTTP).
"""

from __future__ import annotations

# Pre-import numpy before offline_llm_stubs/ThreadPoolExecutor to avoid RecursionError
# in rank_bm25 and reranker (Python 3.12 + nested import loader)
import numpy  # noqa: F401

import os
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

from agent.memory.state import AgentState
from agent.orchestrator.deterministic_runner import run_hierarchical

from agent.tools.validation_scope import ENV_INNER_VALIDATION_CMD

from tests.agent_eval.harness import (
    _compat_get_plan,
    _parent_plan_for_spec,
    _serialize_loop_output,
    _task_success,
    _failure_class_from,
    _replan_observed,
)


def _compat_plan_dict_for_audit(spec) -> dict:
    """Compat plan: SEARCH then EDIT for edit-style audit tasks; else legacy single EXPLAIN."""
    tags = getattr(spec, "tags", ()) or ()
    if any(t in tags for t in ("repair", "feature", "refactor", "tests", "multi_file")):
        desc = (spec.instruction or "")[:800]
        return {
            "plan_id": f"bench_compat_{spec.task_id}",
            "steps": [
                {"id": 1, "action": "SEARCH", "description": desc, "reason": "agent_eval"},
                {"id": 2, "action": "EDIT", "description": desc, "reason": "agent_eval"},
            ],
        }
    return _compat_get_plan()


def _stub_small(*_a: Any, **_k: Any) -> str:
    return '{"query": "benchmark", "tool": "", "reason": ""}'


def _stub_reasoning_json(*_a: Any, **_k: Any) -> str:
    return '{"steps": []}'


def _reasoning_router(*args: Any, **kwargs: Any) -> str:
    """Route offline stubs by task_name so critic/retry get valid JSON (no network)."""
    tn = (kwargs.get("task_name") or "").lower()
    if "critic" in tn or "validation" in tn:
        return (
            '{"failure_type": "bad_patch", "affected_step": null, "evidence": "offline_stub", '
            '"confidence": 0.5, "suggested_strategy": "retry_edit_with_different_patch"}'
        )
    if "retry" in tn or "retry_planning" in tn:
        return (
            '{"strategy": "rewrite_retrieval_query", "rewrite_query": "offline", '
            '"plan_override": null, "retrieve_files": []}'
        )
    return _stub_reasoning_json()


def _stub_explain_text(*_a: Any, **_k: Any) -> str:
    # Must be >= 40 chars to pass _is_valid_explain (validator rejects shorter outputs).
    return "Offline stub explanation for benchmark. This satisfies the minimum length for validation."


def _make_explain_stub_with_substrings(substrings: tuple[str, ...] | None) -> Any:
    """Return a stub that includes explain_required_substrings when grading_mode is explain_artifact."""
    if not substrings:
        return _stub_explain_text

    def _stub(*_a: Any, **_k: Any) -> str:
        # Include all required substrings so explain_artifact_ok passes (generic, task-spec-driven).
        return " ".join(str(s) for s in substrings) + ". Offline stub explanation for benchmark."

    return _stub


def _stub_rank_scores(*_a: Any, **_k: Any) -> str:
    return "0.95\n0.85"


def _stub_router(*_a: Any, **_k: Any) -> str:
    return '{"category": "CODE_EDIT", "confidence": 0.9}'


def _execution_loop_drop_max_runtime(*args: Any, **kwargs: Any):
    """Shim: ``run_deterministic`` passes ``max_runtime_seconds``; ``execution_loop`` has no such arg."""
    kwargs.pop("max_runtime_seconds", None)
    from agent.orchestrator.execution_loop import execution_loop as _real_loop

    return _real_loop(*args, **kwargs)


@contextmanager
def offline_llm_stubs(spec=None):
    """Patch model entry points imported by modules (not model_client alone — bound imports).
    When spec has grading_mode==explain_artifact and explain_required_substrings, the explain
    stub returns text containing those substrings so validation passes (task-spec-driven, generic).
    """
    explain_stub = _stub_explain_text
    if spec and getattr(spec, "grading_mode", "") == "explain_artifact":
        subs = getattr(spec, "explain_required_substrings", None)
        if subs:
            explain_stub = _make_explain_stub_with_substrings(subs)
    with (
        patch("agent.models.model_client.call_reasoning_model", side_effect=_reasoning_router),
        patch("agent.models.model_client.call_small_model", side_effect=_stub_small),
        patch("planner.planner.call_reasoning_model", side_effect=_stub_reasoning_json),
        patch("agent.retrieval.query_rewriter.call_reasoning_model", side_effect=_stub_reasoning_json),
        patch("agent.retrieval.query_rewriter.call_small_model", side_effect=_stub_small),
        patch("agent.execution.step_dispatcher.call_reasoning_model", side_effect=explain_stub),
        patch("agent.execution.step_dispatcher.call_small_model", side_effect=explain_stub),
        patch("agent.retrieval.context_ranker.call_reasoning_model", side_effect=_stub_rank_scores),
        patch("agent.orchestrator.replanner.call_small_model", side_effect=_stub_small),
        patch("agent.orchestrator.replanner.call_reasoning_model", side_effect=_stub_reasoning_json),
        patch("agent.routing.instruction_router.call_small_model", side_effect=_stub_router),
        patch("agent.orchestrator.validator.call_small_model", side_effect=explain_stub),
        patch("agent.orchestrator.validator.call_reasoning_model", side_effect=explain_stub),
        patch("agent.prompt_system.context.context_summarizer.call_small_model", side_effect=explain_stub),
    ):
        yield


def _pytest_inner_validation_cmd(spec) -> str | None:
    """Inner edit→test loop: prefer pytest; else first validation command (docs check scripts, etc.)."""
    cmds = [c for c in (getattr(spec, "validation_commands", ()) or ()) if isinstance(c, str) and c.strip()]
    for cmd in cmds:
        if "pytest" in cmd:
            return cmd
    return cmds[0] if cmds else None


def run_structural_agent_real(spec, project_root: str, *, trace_id: str | None = None) -> dict[str, Any]:
    """
    Run ``run_hierarchical`` with real ``execution_loop``, benchmark parent plan injection,
    and offline LLM stubs.
    """
    os.environ["SERENA_PROJECT_DIR"] = project_root
    parent = _parent_plan_for_spec(spec)
    tid = trace_id or f"bench-real-{spec.task_id}"
    loop_out: dict = {}
    exc = None

    def _get_plan_side(*_a: Any, **_k: Any) -> dict:
        return _compat_plan_dict_for_audit(spec)

    prev_inner = os.environ.get(ENV_INNER_VALIDATION_CMD)
    inner_cmd = _pytest_inner_validation_cmd(spec)
    if inner_cmd:
        os.environ[ENV_INNER_VALIDATION_CMD] = inner_cmd

    try:
        with offline_llm_stubs(spec):
            with patch(
                "agent.orchestrator.deterministic_runner.execution_loop",
                side_effect=_execution_loop_drop_max_runtime,
            ):
                with patch(
                    "agent.orchestrator.deterministic_runner.get_parent_plan",
                    return_value=parent,
                ):
                    if spec.orchestration_path == "compat":
                        with patch(
                            "agent.orchestrator.deterministic_runner.get_plan",
                            side_effect=_get_plan_side,
                        ):
                            _state, loop_out = run_hierarchical(
                                spec.instruction,
                                project_root,
                                trace_id=tid,
                                log_event_fn=lambda *a, **k: None,
                            )
                    else:
                        _state, loop_out = run_hierarchical(
                            spec.instruction,
                            project_root,
                            trace_id=tid,
                            log_event_fn=lambda *a, **k: None,
                        )
    except Exception as e:
        exc = e
    finally:
        if inner_cmd:
            if prev_inner is None:
                os.environ.pop(ENV_INNER_VALIDATION_CMD, None)
            else:
                os.environ[ENV_INNER_VALIDATION_CMD] = prev_inner

    if exc is None and spec.orchestration_path == "compat":
        from tests.hierarchical_test_locks import assert_compat_loop_output_has_no_hierarchical_keys

        assert_compat_loop_output_has_no_hierarchical_keys(loop_out)

    success = _task_success(loop_out, spec.orchestration_path, exc)
    return {
        "loop_output": loop_out if exc is None else {},
        "exception": exc,
        "structural_success": success,
        "failure_class": _failure_class_from(exc, success, loop_out if exc is None else {}),
        "replan_observed": _replan_observed(loop_out if exc is None else {}),
        "loop_output_snapshot": _serialize_loop_output(loop_out if exc is None else {}),
        "attempts_total": loop_out.get("attempts_total") if isinstance(loop_out, dict) else None,
        "retries_used": loop_out.get("retries_used") if isinstance(loop_out, dict) else None,
    }
