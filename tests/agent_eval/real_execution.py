"""
Real execution_loop wiring for agent_eval (Stage 12.1 + Stage 28).

Stage 28 split:
- run_structural_agent_offline: deterministic execution with offline_llm_stubs, plan injection.
  Used for execution_regression benchmarks. Never hits real model.
- run_structural_agent_live_model: no stubs, real planner, must call configured hosted model.
  Used for full_agent / live-model evaluation. Integrity enforced.
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
from agent.models.model_client import get_model_call_audit, reset_model_call_audit
from agent.orchestrator.deterministic_runner import run_hierarchical
from agent.orchestrator.plan_resolver import get_plan_resolution_telemetry, reset_plan_resolution_telemetry

from agent.tools.validation_scope import ENV_INNER_VALIDATION_CMD

from tests.agent_eval.check_retrieval_quality import build_retrieval_quality_record
from tests.agent_eval.harness import (
    _compat_get_plan,
    _parent_plan_for_spec,
    _serialize_loop_output,
)
from tests.agent_eval.success import (
    failure_class_from,
    replan_observed,
    task_success,
)

# Patched call sites for offline_llm_stubs audit (Stage 28)
_OFFLINE_PATCHED_SITES = [
    "agent.models.model_client",
    "planner.planner",
    "agent.retrieval.query_rewriter",
    "agent.execution.step_dispatcher",
    "agent.retrieval.context_ranker",
    "agent.orchestrator.replanner",
    "agent.routing.instruction_router",
    "agent.orchestrator.validator",
    "agent.prompt_system.context.context_summarizer",
]


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


def _stub_bundle_selector(prompt: str, **_k: Any) -> str:
    """
    Offline eval stub: deterministic selection with priority linked > impl > non-test > order.
    Parses payload to extract [linked], [impl], [test] as has_linked, has_impl, is_test flags.
    Guarantees: if any linked row exists, at least one is selected and placed first.
    """
    import json
    import re
    text = prompt or ""
    # Match "  rc_XXXX: ..." lines; extract id and optional [impl][linked][test] suffixes
    candidates: list[dict[str, Any]] = []
    for m in re.finditer(r"\s+(rc_\d+):\s+[^\n]+", text):
        cid = m.group(1)
        line = m.group(0).lower()
        candidates.append({
            "id": cid,
            "has_linked": "[linked]" in line,
            "has_impl": "[impl]" in line,
            "is_test": "[test]" in line,
            "idx": len(candidates),
        })
    if not candidates:
        ids = list(dict.fromkeys(re.findall(r"\brc_\d+\b", text)))[:4]
        if not ids:
            ids = ["rc_0001", "rc_0002"]
        return json.dumps(
            {"keep_ids": ids, "primary_ids": ids[:1], "supporting_ids": ids[1:], "reason": "eval stub fallback"}
        )
    # Priority: linked > impl (no link) > non-test rest > test-only
    linked_rows = [c for c in candidates if c["has_linked"]]
    impl_no_link = [c for c in candidates if c["has_impl"] and not c["has_linked"]]
    non_test_rest = [
        c for c in candidates
        if not c["is_test"] and not c["has_linked"] and not c["has_impl"]
    ]
    rest = [c for c in candidates if c["is_test"] and not c["has_linked"]]
    ordered = linked_rows + impl_no_link + non_test_rest + rest
    chosen = list(dict.fromkeys(c["id"] for c in ordered))[:4]
    # Guarantee: if linked rows exist, at least one must be selected and first
    if linked_rows and chosen:
        linked_ids = {c["id"] for c in linked_rows}
        if not any(cid in linked_ids for cid in chosen):
            chosen = [linked_rows[0]["id"]] + [c for c in chosen if c != linked_rows[0]["id"]][:3]
        elif chosen[0] not in linked_ids:
            chosen = [linked_rows[0]["id"]] + [c for c in chosen if c != linked_rows[0]["id"]][:3]
    return json.dumps(
        {"keep_ids": chosen, "primary_ids": chosen[:1], "supporting_ids": chosen[1:], "reason": "eval stub (prefer linked+impl+non-test)"}
    )


def _stub_small_router(*args: Any, **kwargs: Any) -> str:
    """Route call_small_model by task_name; bundle_selector gets valid JSON for eval."""
    tn = (kwargs.get("task_name") or "").lower()
    if "bundle_selector" in tn and args:
        return _stub_bundle_selector(str(args[0]) if args else "", **kwargs)
    return _stub_small(*args, **kwargs)


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

    Stage 28: Yields audit dict with used_offline_stubs=True, patched_call_sites, used_explain_stub.
    """
    used_explain_stub = False
    explain_stub = _stub_explain_text
    if spec and getattr(spec, "grading_mode", "") == "explain_artifact":
        subs = getattr(spec, "explain_required_substrings", None)
        if subs:
            explain_stub = _make_explain_stub_with_substrings(subs)
            used_explain_stub = True
    audit = {
        "used_offline_stubs": True,
        "patched_call_sites": list(_OFFLINE_PATCHED_SITES),
        "used_explain_stub": used_explain_stub,
    }
    with (
        patch("agent.models.model_client.call_reasoning_model", side_effect=_reasoning_router),
        patch("agent.models.model_client.call_small_model", side_effect=_stub_small_router),
        patch("planner.planner.call_reasoning_model", side_effect=_stub_reasoning_json),
        patch("agent.retrieval.query_rewriter.call_reasoning_model", side_effect=_stub_reasoning_json),
        patch("agent.retrieval.query_rewriter.call_small_model", side_effect=_stub_small_router),
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
        yield audit


def _pytest_inner_validation_cmd(spec) -> str | None:
    """Inner edit→test loop: prefer pytest; else first validation command (docs check scripts, etc.)."""
    cmds = [c for c in (getattr(spec, "validation_commands", ()) or ()) if isinstance(c, str) and c.strip()]
    for cmd in cmds:
        if "pytest" in cmd:
            return cmd
    return cmds[0] if cmds else None


def run_structural_agent_offline(spec, project_root: str, *, trace_id: str | None = None) -> dict[str, Any]:
    """
    Run ``run_hierarchical`` with real ``execution_loop``, benchmark parent plan injection,
    and offline LLM stubs. No real model calls. Stage 28: explicit offline mode.
    """
    os.environ["SERENA_PROJECT_DIR"] = project_root
    parent = _parent_plan_for_spec(spec)
    tid = trace_id or f"bench-offline-{spec.task_id}"
    loop_out: dict = {}
    final_state = None
    exc = None
    stub_audit: dict = {}

    def _get_plan_side(*_a: Any, **_k: Any) -> dict:
        return _compat_plan_dict_for_audit(spec)

    prev_inner = os.environ.get(ENV_INNER_VALIDATION_CMD)
    inner_cmd = _pytest_inner_validation_cmd(spec)
    if inner_cmd:
        os.environ[ENV_INNER_VALIDATION_CMD] = inner_cmd

    try:
        with offline_llm_stubs(spec) as audit:
            stub_audit = audit
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
                            final_state = _state
                    else:
                        _state, loop_out = run_hierarchical(
                            spec.instruction,
                            project_root,
                            trace_id=tid,
                            log_event_fn=lambda *a, **k: None,
                        )
                        final_state = _state
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

    success = task_success(loop_out, spec.orchestration_path, exc)
    model_audit = get_model_call_audit()
    rq_bundle = (
        build_retrieval_quality_record(spec, final_state, loop_out if exc is None else None)
        if exc is None
        else {}
    )
    # Extract answer for golden LLM judge (Phase 3): last successful EXPLAIN output, else loop_output["output"]
    answer = ""
    if exc is None and final_state is not None:
        for sr in reversed(getattr(final_state, "step_results", []) or []):
            if getattr(sr, "action", "").upper() == "EXPLAIN" and getattr(sr, "success", False):
                out = getattr(sr, "output", "")
                answer = out if isinstance(out, str) else str(out or "")
                break
        if not answer and isinstance(loop_out, dict):
            answer = str(loop_out.get("output", "") or "")
    return {
        "loop_output": loop_out if exc is None else {},
        "exception": exc,
        "structural_success": success,
        "failure_class": failure_class_from(exc, success, loop_out if exc is None else {}),
        "replan_observed": replan_observed(loop_out if exc is None else {}),
        "loop_output_snapshot": _serialize_loop_output(loop_out if exc is None else {}),
        "attempts_total": loop_out.get("attempts_total") if isinstance(loop_out, dict) else None,
        "retries_used": loop_out.get("retries_used") if isinstance(loop_out, dict) else None,
        "model_usage_audit": model_audit,
        "plan_resolution_telemetry": {"plan_injection_used": True},
        "stub_audit": stub_audit,
        "used_offline_stubs": True,
        "used_plan_injection": True,
        "used_explain_stub": stub_audit.get("used_explain_stub", False),
        "live_model_integrity_ok": False,
        "integrity_failure_reason": "offline_mode_uses_stubs",
        "retrieval_quality_bundle": rq_bundle,
        "answer": answer,
    }


def run_structural_agent_live_model(spec, project_root: str, *, trace_id: str | None = None) -> dict[str, Any]:
    """
    Run ``run_hierarchical`` with real model client. No offline_llm_stubs, no plan injection.
    Must call configured hosted model. Stage 28: explicit live-model mode.
    """
    os.environ["SERENA_PROJECT_DIR"] = project_root
    tid = trace_id or f"bench-live-{spec.task_id}"
    loop_out: dict = {}
    final_state = None
    exc = None

    reset_model_call_audit()
    reset_plan_resolution_telemetry()

    prev_inner = os.environ.get(ENV_INNER_VALIDATION_CMD)
    inner_cmd = _pytest_inner_validation_cmd(spec)
    if inner_cmd:
        os.environ[ENV_INNER_VALIDATION_CMD] = inner_cmd

    try:
        with patch(
            "agent.orchestrator.deterministic_runner.execution_loop",
            side_effect=_execution_loop_drop_max_runtime,
        ):
            _state, loop_out = run_hierarchical(
                spec.instruction,
                project_root,
                trace_id=tid,
                log_event_fn=lambda *a, **k: None,
            )
            final_state = _state
    except Exception as e:
        exc = e
    finally:
        if inner_cmd:
            if prev_inner is None:
                os.environ.pop(ENV_INNER_VALIDATION_CMD, None)
            else:
                os.environ[ENV_INNER_VALIDATION_CMD] = prev_inner

    model_audit = get_model_call_audit()
    plan_resolution_telemetry = get_plan_resolution_telemetry()
    model_call_count = model_audit.get("model_call_count", 0) or 0

    used_offline_stubs = False
    used_plan_injection = False
    used_explain_stub = False
    live_model_integrity_ok = False
    integrity_failure_reason = None

    if used_offline_stubs:
        integrity_failure_reason = "used_offline_stubs"
    elif used_explain_stub:
        integrity_failure_reason = "used_explain_stub"
    elif model_call_count < 1:
        integrity_failure_reason = "zero_real_model_calls"
    elif used_plan_injection:
        integrity_failure_reason = "used_plan_injection_in_live_mode"
    else:
        live_model_integrity_ok = True

    success = task_success(loop_out, spec.orchestration_path, exc)
    rq_bundle = (
        build_retrieval_quality_record(spec, final_state, loop_out if exc is None else None)
        if exc is None
        else {}
    )
    return {
        "loop_output": loop_out if exc is None else {},
        "exception": exc,
        "structural_success": success,
        "failure_class": failure_class_from(exc, success, loop_out if exc is None else {}),
        "replan_observed": replan_observed(loop_out if exc is None else {}),
        "loop_output_snapshot": _serialize_loop_output(loop_out if exc is None else {}),
        "attempts_total": loop_out.get("attempts_total") if isinstance(loop_out, dict) else None,
        "retries_used": loop_out.get("retries_used") if isinstance(loop_out, dict) else None,
        "model_usage_audit": model_audit,
        "plan_resolution_telemetry": plan_resolution_telemetry,
        "stub_audit": {"used_offline_stubs": False, "patched_call_sites": [], "used_explain_stub": False},
        "used_offline_stubs": used_offline_stubs,
        "used_plan_injection": used_plan_injection,
        "used_explain_stub": used_explain_stub,
        "live_model_integrity_ok": live_model_integrity_ok,
        "integrity_failure_reason": integrity_failure_reason,
        "retrieval_quality_bundle": rq_bundle,
    }


def run_structural_agent_real(spec, project_root: str, *, trace_id: str | None = None) -> dict[str, Any]:
    """
    Deprecated: maps to run_structural_agent_offline. Use --execution-mode offline explicitly.
    """
    import warnings

    warnings.warn(
        "run_structural_agent_real is deprecated; use run_structural_agent_offline. "
        "--execution-mode real maps to offline. Use --execution-mode live_model for real model.",
        DeprecationWarning,
        stacklevel=2,
    )
    return run_structural_agent_offline(spec, project_root, trace_id=trace_id)
