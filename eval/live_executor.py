"""
Canonical LIVE pipeline capture for tiered evaluation (Tier 1–4).

Uses production wiring only:
  ``create_runtime()`` → ``explore`` → ``PlannerV2`` (via ``mode_manager.planner.plan``)
  → ``synthesize_answer`` → ``validate_answer``

All LLM usage goes through existing modules (``call_reasoning_model`` inside those layers).

**Environment:** set ``SERENA_PROJECT_DIR`` / cwd to the repo under test; optional
``AUTOSTUDIO_EVAL_PROJECT_ROOT`` overrides the workspace root. ``SKIP_STARTUP_CHECKS=1``
is set by default for eval runs to match live integration tests.

**Capture:** fills ``loop_meta.steps`` / ``total_iterations`` / ``validation_failures`` and
``state.final`` / ``state.progression`` for iteration-level and compressed-state eval.

Do not import this module from ``eval/runner`` at package import time — only when
running live eval (avoids heavy imports in CI).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from eval.runner import EvalTask, PipelineCapture

_LOG = logging.getLogger(__name__)


def _default_repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _prepare_workspace(task: EvalTask) -> Path:
    env_root = os.environ.get("AUTOSTUDIO_EVAL_PROJECT_ROOT", "").strip()
    if env_root:
        root = Path(env_root).resolve()
    else:
        hint = (task.get("workspace_root") or "").strip()
        base = _default_repo_root()
        root = (base / hint).resolve() if hint else base
    if not root.is_dir():
        raise FileNotFoundError(f"Eval workspace is not a directory: {root}")
    os.chdir(root)
    os.environ["SERENA_PROJECT_DIR"] = str(root)
    os.environ.setdefault("SKIP_STARTUP_CHECKS", "1")
    return root


def _dump(obj: Any) -> dict[str, Any]:
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if isinstance(obj, dict):
        return obj
    return {"value": str(obj)}


def _compress_exploration(exploration: Any) -> dict[str, Any]:
    summ = getattr(exploration, "exploration_summary", None)
    gaps = getattr(summ, "knowledge_gaps", None) if summ is not None else None
    if not isinstance(gaps, list):
        gaps = []
    overall = getattr(summ, "overall", None) if summ is not None else ""
    return {
        "evidence_count": len(getattr(exploration, "evidence", None) or []),
        "knowledge_gaps_count": len(gaps),
        "confidence": str(getattr(exploration, "confidence", "") or ""),
        "overall_preview": str(overall or "")[:400],
    }


def _compress_synthesis(synthesis: Any) -> dict[str, Any]:
    return {
        "synthesis_success": bool(getattr(synthesis, "synthesis_success", False)),
        "coverage": str(getattr(synthesis, "coverage", "") or ""),
        "direct_answer_preview": str(getattr(synthesis, "direct_answer", None) or "")[:400],
    }


def _compress_validation(validation: Any) -> dict[str, Any]:
    issues = getattr(validation, "issues", None) or []
    if not isinstance(issues, list):
        issues = []
    return {
        "is_complete": bool(getattr(validation, "is_complete", False)),
        "issues_count": len(issues),
        "confidence": str(getattr(validation, "confidence", "") or ""),
        "validation_reason_preview": str(getattr(validation, "validation_reason", None) or "")[:400],
    }


def _build_state_payload(
    exploration: Any,
    synthesis: Any,
    validation: Any,
) -> dict[str, Any]:
    ex_c = _compress_exploration(exploration)
    syn_c = _compress_synthesis(synthesis)
    val_c = _compress_validation(validation)
    final = {"exploration": ex_c, "synthesis": syn_c, "validation": val_c}
    progression: list[dict[str, Any]] = [
        {"phase": "post_exploration", "exploration": dict(ex_c)},
        {
            "phase": "post_synthesis",
            "exploration": dict(ex_c),
            "synthesis": dict(syn_c),
        },
        {
            "phase": "post_validation",
            "exploration": dict(ex_c),
            "synthesis": dict(syn_c),
            "validation": dict(val_c),
        },
    ]
    return {"final": final, "progression": progression}


def _iteration_step_record(
    *,
    iteration: int,
    plan_engine: Any,
    validation: Any,
    ex_c: dict[str, Any],
) -> dict[str, Any]:
    eng = plan_engine
    if eng is not None:
        q = (getattr(eng, "query", None) or "")[:160]
        decision_s = f"{getattr(eng, 'decision', '')} tool={getattr(eng, 'tool', '')} query={q}"
    else:
        decision_s = ""
    issues = getattr(validation, "issues", None) or []
    n_issues = len(issues) if isinstance(issues, list) else 0
    if getattr(validation, "is_complete", False):
        validation_s = "complete"
    else:
        validation_s = f"incomplete issues={n_issues}"
    state_s = (
        f"evidence={ex_c.get('evidence_count')} gaps={ex_c.get('knowledge_gaps_count')} "
        f"conf={ex_c.get('confidence')} val_ok={getattr(validation, 'is_complete', False)}"
    )
    return {
        "iteration": iteration,
        "decision": decision_s.strip(),
        "validation": validation_s,
        "state_summary": state_s,
    }


def live_executor(task: EvalTask) -> PipelineCapture:
    """
    Run one instruction through real exploration, planner, synthesis, and validation.

    Returns a :class:`PipelineCapture` with serializable dicts. On failure, sets
    ``loop_meta["error"]`` and re-raises only if ``TIERED_EVAL_LIVE_STRICT=1``.

    Single-pass run = one outer ``iteration`` in ``loop_meta.steps``; multi-iteration
    pipelines should append rows and set ``total_iterations`` / ``validation_failures``.
    """
    instruction = (task.get("instruction") or "").strip()
    if not instruction:
        raise ValueError("EvalTask.instruction is required for live_executor")

    _prepare_workspace(task)

    from agent_v2.exploration.answer_synthesizer import synthesize_answer
    from agent_v2.runtime.bootstrap import create_runtime
    from agent_v2.validation.answer_validator import validate_answer

    rt = create_runtime()
    exploration = rt.explore(instruction)
    plan = rt.mode_manager.planner.plan(
        instruction=instruction,
        deep=False,
        exploration=exploration,
    )
    synthesis = synthesize_answer(exploration)
    validation = validate_answer(
        instruction=instruction,
        exploration=exploration,
        synthesis=synthesis,
    )

    decision: dict[str, Any] = {"plan": _dump(plan)}
    eng = getattr(plan, "engine", None)
    if eng is not None:
        decision["decision_type"] = eng.decision
        decision["type"] = eng.decision
        decision["tool"] = eng.tool
        decision["query"] = eng.query
    ctl = getattr(plan, "controller", None)
    if ctl is not None:
        decision["controller"] = _dump(ctl)

    ex_c = _compress_exploration(exploration)
    val_failures = 0 if getattr(validation, "is_complete", False) else 1
    step_row = _iteration_step_record(
        iteration=1,
        plan_engine=eng,
        validation=validation,
        ex_c=ex_c,
    )
    meta: dict[str, Any] = {
        "live_executor": True,
        "exploration_id": getattr(exploration, "exploration_id", None),
        "plan_id": getattr(plan, "plan_id", None),
        "steps": [step_row],
        "total_iterations": 1,
        "validation_failures": val_failures,
        "open_questions_after": ex_c.get("knowledge_gaps_count"),
        "findings_count_after": ex_c.get("evidence_count"),
    }

    state_payload = _build_state_payload(exploration, synthesis, validation)

    out: PipelineCapture = {
        "decision": decision,
        "exploration": exploration.model_dump(mode="json"),
        "synthesis": synthesis.model_dump(mode="json"),
        "validation": validation.model_dump(mode="json"),
        "state": state_payload,
        "loop_meta": meta,
    }
    return out


def live_executor_safe(task: EvalTask) -> PipelineCapture:
    """Like :func:`live_executor` but returns error payload instead of raising."""
    empty_loop = {
        "steps": [],
        "total_iterations": 0,
        "validation_failures": 0,
    }
    try:
        return live_executor(task)
    except Exception as ex:
        _LOG.exception("live_executor failed: %s", ex)
        strict = os.environ.get("TIERED_EVAL_LIVE_STRICT", "").lower() in ("1", "true", "yes")
        if strict:
            raise
        return {
            "loop_meta": {
                "live_executor": True,
                "error": str(ex),
                "error_type": type(ex).__name__,
                **empty_loop,
            },
            "state": {"final": {}, "progression": []},
        }
