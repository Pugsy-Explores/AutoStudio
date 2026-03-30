"""
Load tiered JSON tasks, run an optional pipeline executor, score, and write reports.

Integration: pass ``executor(task) -> PipelineCapture`` that calls production code
(PlannerV2, exploration, ``validate_answer``, etc.) and returns serializable dicts
per stage. This module stays free of direct LLM imports so it remains lightweight.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, TypedDict

from .metrics import EvalMetrics, METRIC_KEYS, aggregate_metrics

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class PipelineCapture(TypedDict, total=False):
    """Serializable stage outputs for scoring and benchmarking."""

    decision: dict[str, Any]
    exploration: dict[str, Any]
    synthesis: dict[str, Any]
    validation: dict[str, Any]
    """AnswerValidationResult or dict with is_complete, issues, missing_context."""
    state: dict[str, Any]
    """
    Visible compressed state for eval (not full AgentState):

    - ``final`` — dict after last iteration (exploration / synthesis / validation summaries).
    - ``progression`` — list of per-phase snapshots (e.g. post_exploration → post_synthesis → post_validation).
    """
    loop_meta: dict[str, Any]
    """
    Iteration-aware metadata (Tier 4 / validation loops):

    - ``steps`` — ``[{"iteration": 1, "decision": "...", "validation": "...", "state_summary": "..."}, ...]``
    - ``total_iterations`` — outer-loop count (typically ``len(steps)``)
    - ``validation_failures`` — iterations where validation did not complete successfully

    Legacy: numeric ``steps``/``iterations``, ``converged``, nested ``state_progress``.
    """
    state_progress: dict[str, Any]
    """
    Before/after snapshot for iterative loops (Tier 4 / debugging). Prefer flat counts:

    - ``findings_count_before`` / ``findings_count_after`` (non-negative ints)
    - ``open_questions_before`` / ``open_questions_after``
    - ``confidence_before`` / ``confidence_after`` (``"low"|"medium"|"high"`` or 0–2)

    May also be nested under ``loop_meta["state_progress"]``.
    """
    validation_gain: dict[str, Any]
    """
    Pre/post validation loop for ``validation_gain`` metric:

    - ``completeness_before`` / ``completeness_after`` (0.0–1.0)
    - ``answer_before`` / ``answer_after`` (strings; optional substring checks via task ground_truth)
    """


class EvalTask(TypedDict, total=False):
    id: str
    tier: int
    module: str
    instruction: str
    expected_behavior: str
    expected_signals: list[str]
    ground_truth: dict[str, Any]
    workspace_root: str
    """Optional repo-relative root for repo-based cases (informational)."""


@dataclass
class EvalReport:
    tasks: list[EvalTask]
    per_task: list[dict[str, Any]]
    metrics: EvalMetrics
    raw_captures: list[PipelineCapture | None] = field(default_factory=list)


ExecutorFn = Callable[[EvalTask], PipelineCapture]


# ---------------------------------------------------------------------------
# IO
# ---------------------------------------------------------------------------


def _resolve_eval_dir() -> Path:
    return Path(__file__).resolve().parent


def load_dataset(path: str | Path) -> list[EvalTask]:
    """Load JSON array of tasks or ``{"tasks": [...]}``."""
    p = Path(path)
    raw = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "tasks" in raw:
        raw = raw["tasks"]
    if not isinstance(raw, list):
        raise ValueError("Dataset must be a JSON array or {tasks: [...]}")
    out: list[EvalTask] = []
    for i, row in enumerate(raw):
        if not isinstance(row, dict):
            raise ValueError(f"Task {i} must be an object")
        out.append(_normalize_task(row))
    return out


def _normalize_task(row: dict[str, Any]) -> EvalTask:
    tid = str(row.get("id") or row.get("task_id") or "")
    if not tid:
        raise ValueError("Each task requires id")
    tier = int(row["tier"])
    if tier not in (1, 2, 3, 4):
        raise ValueError(f"Invalid tier {tier} for task {tid}")
    mod = str(row.get("module", "")).strip().lower()
    allowed = {"planner", "decision", "exploration", "synthesizer", "validator"}
    if mod not in allowed:
        raise ValueError(f"task {tid}: module must be one of {allowed}, got {mod!r}")
    task: EvalTask = {
        "id": tid,
        "tier": tier,
        "module": mod,
        "instruction": str(row.get("instruction", "")),
        "expected_behavior": str(row.get("expected_behavior", "")),
        "expected_signals": list(row.get("expected_signals") or []),
    }
    if row.get("ground_truth") is not None:
        task["ground_truth"] = row["ground_truth"]  # type: ignore[assignment]
    if row.get("workspace_root"):
        task["workspace_root"] = str(row["workspace_root"])
    return task


def write_report(report: EvalReport, path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "metrics": asdict(report.metrics),
        "per_task": report.per_task,
    }
    p.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Scoring (lightweight, deterministic; extend with LLM judges outside this file)
# ---------------------------------------------------------------------------


def _dump(o: Any) -> str:
    if o is None:
        return ""
    if isinstance(o, str):
        return o
    try:
        return json.dumps(o, ensure_ascii=False, default=str)
    except TypeError:
        return str(o)


def _signal_hits(signals: list[str], haystack: str) -> tuple[int, int]:
    if not signals:
        return (0, 0)
    hit = 0
    for s in signals:
        if not s:
            continue
        if s in haystack or s.lower() in haystack.lower():
            hit += 1
    return (hit, len(signals))


_CONF_RANK = {"low": 0, "medium": 1, "high": 2}


def _confidence_rank(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        v = int(max(0, min(2, round(float(value)))))
        return v
    s = str(value).strip().lower()
    return _CONF_RANK.get(s)


def _exploration_slice_from_snapshot(snap: dict[str, Any]) -> dict[str, Any] | None:
    ex = snap.get("exploration")
    if isinstance(ex, dict) and (
        "evidence_count" in ex or "knowledge_gaps_count" in ex or ex.get("confidence") is not None
    ):
        return ex
    return None


def _state_progress_from_progression(progression: list[Any]) -> float | None:
    """Derive a 0–1 score from first vs last snapshot in ``state.progression``."""
    if len(progression) < 2:
        return None
    first = progression[0]
    last = progression[-1]
    if not isinstance(first, dict) or not isinstance(last, dict):
        return None
    fe = _exploration_slice_from_snapshot(first)
    le = _exploration_slice_from_snapshot(last)
    if not fe or not le:
        return None
    parts: list[float] = []
    fb = fe.get("evidence_count")
    fa = le.get("evidence_count")
    if fb is not None and fa is not None:
        fb_f, fa_f = float(fb), float(fa)
        if fa_f > fb_f:
            parts.append(1.0)
        elif fa_f == fb_f:
            parts.append(0.5)
        else:
            parts.append(0.0)
    ob = fe.get("knowledge_gaps_count")
    oa = le.get("knowledge_gaps_count")
    if ob is not None and oa is not None:
        ob_f, oa_f = float(ob), float(oa)
        if oa_f < ob_f:
            parts.append(1.0)
        elif oa_f == ob_f:
            parts.append(0.5)
        else:
            parts.append(0.0)
    cb, ca = fe.get("confidence"), le.get("confidence")
    rb, ra = _confidence_rank(cb), _confidence_rank(ca)
    if rb is not None and ra is not None:
        if ra > rb:
            parts.append(1.0)
        elif ra == rb:
            parts.append(0.5)
        else:
            parts.append(0.0)
    if not parts:
        return None
    return sum(parts) / len(parts)


def score_state_progress(capture: dict[str, Any]) -> float:
    """
    Score whether state improved across an iteration (findings ↑, open questions ↓, confidence ↑).

    Reads, in order:

    1. ``capture["state"]["progression"]`` (first vs last snapshot ``exploration`` slice)
    2. ``capture["state_progress"]`` or ``capture["loop_meta"]["state_progress"]`` (flat before/after)

    Returns 0.0 if no structured fields are present.
    """
    st = capture.get("state")
    if isinstance(st, dict):
        prog = st.get("progression")
        if isinstance(prog, list) and len(prog) >= 2:
            derived = _state_progress_from_progression(prog)
            if derived is not None:
                return derived

    raw = capture.get("state_progress")
    if not isinstance(raw, dict):
        lm = capture.get("loop_meta")
        if isinstance(lm, dict) and isinstance(lm.get("state_progress"), dict):
            raw = lm["state_progress"]
        else:
            return 0.0
    parts: list[float] = []
    fb = raw.get("findings_count_before")
    fa = raw.get("findings_count_after")
    if fb is not None and fa is not None:
        fb_f, fa_f = float(fb), float(fa)
        if fa_f > fb_f:
            parts.append(1.0)
        elif fa_f == fb_f:
            parts.append(0.5)
        else:
            parts.append(0.0)
    ob = raw.get("open_questions_before")
    oa = raw.get("open_questions_after")
    if ob is not None and oa is not None:
        ob_f, oa_f = float(ob), float(oa)
        if oa_f < ob_f:
            parts.append(1.0)
        elif oa_f == ob_f:
            parts.append(0.5)
        else:
            parts.append(0.0)
    cb = raw.get("confidence_before")
    ca = raw.get("confidence_after")
    rb, ra = _confidence_rank(cb), _confidence_rank(ca)
    if rb is not None and ra is not None:
        if ra > rb:
            parts.append(1.0)
        elif ra == rb:
            parts.append(0.5)
        else:
            parts.append(0.0)
    if not parts:
        return 0.0
    return sum(parts) / len(parts)


def score_validation_gain(task: EvalTask, capture: dict[str, Any]) -> float:
    """
    Did validation / follow-up exploration improve the outcome vs the first synthesis?

    Uses ``capture["validation_gain"]`` with optional completeness scores and answers.
    Ground truth (optional): ``answer_after_must_contain``, ``post_loop_must_contain`` (markers
    that should appear only after improvement — scored vs answer_before/answer_after).
    """
    vg = capture.get("validation_gain")
    if not isinstance(vg, dict):
        return 0.0
    gt = task.get("ground_truth") or {}
    scores: list[float] = []

    b = vg.get("completeness_before")
    a = vg.get("completeness_after")
    if b is not None and a is not None:
        b_f, a_f = float(b), float(a)
        b_f = max(0.0, min(1.0, b_f))
        a_f = max(0.0, min(1.0, a_f))
        if b_f >= 0.999:
            scores.append(1.0 if a_f >= b_f else 0.0)
        else:
            gap_closed = max(0.0, (a_f - b_f) / max(1e-9, 1.0 - b_f))
            scores.append(min(1.0, gap_closed))

    ab = str(vg.get("answer_before") or "")
    aa = str(vg.get("answer_after") or "")
    markers = gt.get("post_loop_must_contain") or gt.get("answer_after_must_contain") or []
    if isinstance(markers, list) and markers and (ab or aa):
        gained = 0
        for m in markers:
            if not m:
                continue
            ml = str(m).lower()
            in_after = ml in aa.lower()
            in_before = ml in ab.lower()
            if in_after and not in_before:
                gained += 1
        scores.append(gained / max(1, len([m for m in markers if m])))

    if not scores:
        return 0.0
    raw_mean = sum(scores) / len(scores)
    if gt.get("expect_validation_gain") is True and b is not None and a is not None:
        if float(a) < float(b):
            raw_mean = 0.0

    return max(0.0, min(1.0, raw_mean))


def score_task(task: EvalTask, capture: PipelineCapture | None) -> dict[str, Any]:
    """
    Produce per-task metrics (0–1) plus diagnostic fields.

    Uses ``expected_signals`` and optional ``ground_truth`` for module-specific checks.
    """
    mod = task["module"]
    tier = int(task["tier"])
    gt = task.get("ground_truth") or {}
    cap = capture or {}
    signals = list(task.get("expected_signals") or [])

    decision_accuracy = 0.0
    retrieval_recall = 0.0
    synthesis_correctness = 0.0
    validation_effectiveness = 0.0
    loop_efficiency = 0.0

    dec = cap.get("decision") or {}
    expl = cap.get("exploration") or {}
    syn = cap.get("synthesis") or {}
    val = cap.get("validation") or {}
    loop = cap.get("loop_meta") or {}

    if mod == "decision":
        want_tool = gt.get("tool") or gt.get("expected_tool")
        want_type = gt.get("decision_type") or gt.get("type")
        got_tool = dec.get("tool") or dec.get("selected_tool")
        got_type = dec.get("type") or dec.get("decision_type")
        parts = 0
        ok = 0
        if want_tool is not None:
            parts += 1
            if str(got_tool or "").strip().lower() == str(want_tool).strip().lower():
                ok += 1
        if want_type is not None:
            parts += 1
            if str(got_type or "").strip().lower() == str(want_type).strip().lower():
                ok += 1
        if parts:
            decision_accuracy = ok / parts
        else:
            h, n = _signal_hits(signals, _dump(dec))
            decision_accuracy = h / n if n else 0.0

    elif mod == "planner":
        steps = gt.get("expected_step_actions") or gt.get("actions")
        pred = dec.get("steps") or dec.get("plan", {}).get("steps") if isinstance(dec.get("plan"), dict) else dec.get("steps")
        if isinstance(pred, list) and isinstance(steps, list) and steps:
            pred_actions = [
                str(s.get("action", "")).upper()
                for s in pred
                if isinstance(s, dict)
            ]
            want = [str(a).upper() for a in steps]
            hit = sum(1 for a in want if a in pred_actions)
            decision_accuracy = hit / len(want)
        else:
            h, n = _signal_hits(signals, _dump(dec))
            decision_accuracy = h / n if n else 0.0

    elif mod == "exploration":
        hay = _dump(expl)
        h, n = _signal_hits(signals, hay)
        retrieval_recall = h / n if n else 0.0
        must = gt.get("must_include_paths") or []
        if isinstance(must, list) and must:
            p_hit = sum(1 for p in must if p and p in hay)
            retrieval_recall = 0.5 * retrieval_recall + 0.5 * (p_hit / len(must))

    elif mod == "synthesizer":
        hay = _dump(syn)
        ans = gt.get("direct_answer_contains") or gt.get("answer_substrings") or []
        if isinstance(ans, list) and ans:
            ok = sum(1 for a in ans if a and a.lower() in hay.lower())
            synthesis_correctness = ok / len(ans)
        else:
            h, n = _signal_hits(signals, hay)
            synthesis_correctness = h / n if n else 0.0

    elif mod == "validator":
        want_complete = gt.get("is_complete")
        if want_complete is not None:
            got = val.get("is_complete")
            validation_effectiveness = 1.0 if bool(got) == bool(want_complete) else 0.0
        want_issues = gt.get("must_detect_issues") or []
        if isinstance(want_issues, list) and want_issues:
            issues = val.get("issues") or []
            is_str = [str(x).lower() for x in issues] if isinstance(issues, list) else []
            blob = " ".join(is_str)
            detected = sum(1 for w in want_issues if w and w.lower() in blob)
            validation_effectiveness = max(
                validation_effectiveness,
                detected / len(want_issues) if want_issues else 0.0,
            )
        if validation_effectiveness == 0.0 and signals:
            h, n = _signal_hits(signals, _dump(val))
            validation_effectiveness = h / n if n else 0.0

    # Loop efficiency: prefer ``total_iterations`` / structured ``steps`` list; else legacy counts
    max_steps = float(gt.get("max_steps") or loop.get("max_steps") or 8)
    lm_steps = loop.get("steps")
    if isinstance(lm_steps, list) and lm_steps and isinstance(lm_steps[0], dict):
        steps_taken = float(loop.get("total_iterations") or len(lm_steps))
    elif isinstance(lm_steps, (int, float)) and not isinstance(lm_steps, bool) and lm_steps > 0:
        steps_taken = float(lm_steps)
    else:
        steps_taken = float(loop.get("total_iterations") or loop.get("iterations") or 0)
    if steps_taken > 0 and max_steps > 0:
        loop_efficiency = max(0.0, min(1.0, 1.0 - (steps_taken - 1) / max_steps))
    elif gt.get("expected_steps") is not None:
        exp = float(gt["expected_steps"])
        act = float(steps_taken or exp)
        if exp > 0:
            loop_efficiency = max(0.0, min(1.0, exp / max(act, 1.0)))

    state_progress_score = score_state_progress(cap)
    validation_gain_score = score_validation_gain(task, cap)

    row = {
        "task_id": task["id"],
        "tier": tier,
        "module": mod,
        "decision_accuracy": round(decision_accuracy, 4),
        "retrieval_recall": round(retrieval_recall, 4),
        "synthesis_correctness": round(synthesis_correctness, 4),
        "validation_effectiveness": round(validation_effectiveness, 4),
        "loop_efficiency": round(loop_efficiency, 4),
        "state_progress_score": round(state_progress_score, 4),
        "validation_gain": round(validation_gain_score, 4),
    }
    return row


def run_tiered_eval(
    tasks: list[EvalTask],
    executor: ExecutorFn | None = None,
) -> EvalReport:
    """
    Execute each task through ``executor`` (if provided) and score.

    If ``executor`` is None, scores are computed with empty captures (useful to
    validate dataset loading only).
    """
    per_task: list[dict[str, Any]] = []
    raw: list[PipelineCapture | None] = []

    for task in tasks:
        cap: PipelineCapture | None = None
        if executor is not None:
            cap = executor(task)
        raw.append(cap)
        scored = score_task(task, cap)
        scored["task_id"] = task["id"]
        per_task.append(scored)

    metrics = aggregate_metrics(per_task)
    return EvalReport(tasks=tasks, per_task=per_task, metrics=metrics, raw_captures=raw)


def default_dataset_path() -> Path:
    """Built-in sample dataset (repo-relative paths as hints only)."""
    return _resolve_eval_dir() / "datasets" / "sample_tasks.json"


def default_live_executor() -> ExecutorFn | None:
    """
    If ``TIERED_EVAL_LIVE`` is set to a truthy value, return :func:`eval.live_executor.live_executor_safe`.

    Otherwise returns ``None`` (heuristic scoring only). Does not import live modules unless live.
    """
    import os

    if os.environ.get("TIERED_EVAL_LIVE", "").lower() not in ("1", "true", "yes"):
        return None
    from eval.live_executor import live_executor_safe

    return live_executor_safe


def main(argv: list[str] | None = None) -> None:
    """CLI: load dataset, optional dry-run (no executor), print aggregate metrics JSON."""
    import argparse
    import os

    ap = argparse.ArgumentParser(description="Tiered eval runner (load + score).")
    ap.add_argument(
        "--dataset",
        type=str,
        default=str(default_dataset_path()),
        help="Path to JSON dataset file",
    )
    ap.add_argument(
        "--live",
        action="store_true",
        help="Run eval/live_executor.live_executor_safe (real LLM + pipeline). Implies credentials + repo index.",
    )
    args = ap.parse_args(argv)
    tasks = load_dataset(args.dataset)
    executor = None
    if args.live:
        os.environ.setdefault("TIERED_EVAL_LIVE", "1")
        from eval.live_executor import live_executor_safe

        executor = live_executor_safe
    else:
        executor = default_live_executor()
    report = run_tiered_eval(tasks, executor=executor)
    print(json.dumps(asdict(report.metrics), indent=2))


if __name__ == "__main__":
    main()


__all__ = [
    "METRIC_KEYS",
    "EvalReport",
    "EvalTask",
    "ExecutorFn",
    "PipelineCapture",
    "default_dataset_path",
    "default_live_executor",
    "load_dataset",
    "run_tiered_eval",
    "score_state_progress",
    "score_task",
    "score_validation_gain",
    "write_report",
]
