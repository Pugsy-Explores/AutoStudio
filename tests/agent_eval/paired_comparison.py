"""
Stage 33 + 34 + 35 + 36 — Offline vs live_model gap audit and comparison.

Stage 36: Evidence-aware, honest policy. Outcome matrix, evidence-quality metrics,
usefulness judgment, policy conditional on evidence.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

Judgment = Literal["offline_is_predictive", "offline_is_partially_predictive", "offline_is_misleading"]
UsefulnessJudgment = Literal[
    "predictive_and_useful",
    "predictive_but_low_evidence",
    "insufficient_evidence",
    "misleading",
]
DecisionRecommendation = Literal[
    "offline_primary",
    "offline_primary_selective_live_gate",
    "live_primary",
    "live_too_unstable_to_gate",
]
PolicySupport = Literal["strongly_supported", "provisionally_supported"]


def _load_json(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _safe_int(x: Any) -> int:
    if x is None:
        return 0
    try:
        return int(x)
    except (TypeError, ValueError):
        return 0


def compute_summary_deltas(
    offline_summary: dict[str, Any],
    live_summary: dict[str, Any],
) -> dict[str, Any]:
    """Compute deltas between offline and live_model summary metrics."""
    metrics = [
        "success_count",
        "validation_pass_count",
        "structural_success_count",
        "model_call_count_total",
        "small_model_call_count_total",
        "reasoning_model_call_count_total",
        "attempts_total_aggregate",
        "retries_used_aggregate",
        "replans_used_aggregate",
    ]
    deltas: dict[str, Any] = {}
    for k in metrics:
        o = _safe_int(offline_summary.get(k))
        l = _safe_int(live_summary.get(k))
        deltas[k] = {"offline": o, "live": l, "delta": l - o}
    return deltas


def _per_task_deltas(
    offline_per_task: list[dict[str, Any]],
    live_per_task: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build per-task success/validation/structural deltas."""
    off_by_id = {p["task_id"]: p for p in offline_per_task}
    live_by_id = {p["task_id"]: p for p in live_per_task}
    common = set(off_by_id) & set(live_by_id)
    rows = []
    for tid in sorted(common):
        o = off_by_id[tid]
        l = live_by_id[tid]
        rows.append({
            "task_id": tid,
            "success_offline": o.get("success"),
            "success_live": l.get("success"),
            "success_delta": (1 if l.get("success") else 0) - (1 if o.get("success") else 0),
            "validation_offline": o.get("validation_passed"),
            "validation_live": l.get("validation_passed"),
            "structural_offline": o.get("structural_success"),
            "structural_live": l.get("structural_success"),
            "failure_bucket_offline": o.get("failure_bucket"),
            "failure_bucket_live": l.get("failure_bucket"),
            "attempts_offline": o.get("attempts_total"),
            "attempts_live": l.get("attempts_total"),
            "retries_offline": o.get("retries_used"),
            "retries_live": l.get("retries_used"),
        })
    return rows


def _failure_bucket_deltas(
    offline_per_task: list[dict[str, Any]],
    live_per_task: list[dict[str, Any]],
) -> dict[str, dict[str, int]]:
    """Delta of failure_bucket counts between modes."""
    def _hist(per_task: list[dict]) -> dict[str, int]:
        h: dict[str, int] = {}
        for p in per_task:
            b = p.get("failure_bucket") or "None"
            h[str(b)] = h.get(str(b), 0) + 1
        return h

    off_hist = _hist(offline_per_task)
    live_hist = _hist(live_per_task)
    all_keys = set(off_hist) | set(live_hist)
    return {
        k: {"offline": off_hist.get(k, 0), "live": live_hist.get(k, 0), "delta": live_hist.get(k, 0) - off_hist.get(k, 0)}
        for k in sorted(all_keys)
    }


def _semantic_rca_deltas(
    offline_run_dir: Path,
    live_run_dir: Path,
    task_ids: list[str],
) -> dict[str, dict[str, int]]:
    """Delta of semantic_rca guessed_root_cause counts between modes."""
    def _load_causes(run_dir: Path) -> dict[str, str]:
        out: dict[str, str] = {}
        for tid in task_ids:
            p = run_dir / "tasks" / tid / "semantic_rca.json"
            if not p.is_file():
                continue
            try:
                d = _load_json(p)
                if "_error" in d:
                    continue
                c = d.get("guessed_root_cause") or "unknown"
                out[tid] = c
            except Exception:
                pass
        return out

    off_causes = _load_causes(offline_run_dir)
    live_causes = _load_causes(live_run_dir)

    def _hist(causes: dict[str, str]) -> dict[str, int]:
        h: dict[str, int] = {}
        for c in causes.values():
            h[c] = h.get(c, 0) + 1
        return h

    off_hist = _hist(off_causes)
    live_hist = _hist(live_causes)
    all_keys = set(off_hist) | set(live_hist)
    return {
        k: {"offline": off_hist.get(k, 0), "live": live_hist.get(k, 0), "delta": live_hist.get(k, 0) - off_hist.get(k, 0)}
        for k in sorted(all_keys)
    }


def _integrity_validity(offline_summary: dict, live_summary: dict) -> dict[str, Any]:
    """Integrity validity comparison."""
    return {
        "offline": {
            "run_valid_for_live_eval": offline_summary.get("run_valid_for_live_eval"),
            "invalid_live_model_task_count": offline_summary.get("invalid_live_model_task_count"),
            "zero_model_call_task_count": offline_summary.get("zero_model_call_task_count"),
        },
        "live": {
            "run_valid_for_live_eval": live_summary.get("run_valid_for_live_eval"),
            "invalid_live_model_task_count": live_summary.get("invalid_live_model_task_count"),
            "zero_model_call_task_count": live_summary.get("zero_model_call_task_count"),
        },
    }


def derive_judgment(deltas: dict[str, Any], per_task_deltas: list[dict[str, Any]]) -> Judgment:
    """
    Blunt judgment: offline predictive / partially predictive / misleading.

    Evidence-based; no score tuning.
    """
    success_delta = deltas.get("success_count", {}).get("delta", 0)

    # Per-task flips: offline success -> live fail (offline overstates), or offline fail -> live success (offline understates)
    flips_off_to_fail = sum(1 for p in per_task_deltas if p.get("success_offline") and not p.get("success_live"))
    flips_off_to_ok = sum(1 for p in per_task_deltas if not p.get("success_offline") and p.get("success_live"))

    # offline_is_predictive: same or very close success counts; few flips
    if abs(success_delta) <= 1 and flips_off_to_fail <= 1 and flips_off_to_ok <= 1:
        return "offline_is_predictive"

    # offline_is_misleading: large negative delta (live much worse) or many flips off->fail
    if success_delta <= -2 or flips_off_to_fail >= 2:
        return "offline_is_misleading"

    # offline_is_partially_predictive: moderate delta or some flips
    return "offline_is_partially_predictive"


def build_comparison_artifact(
    offline_run_dir: Path,
    live_run_dir: Path,
) -> tuple[dict[str, Any], str]:
    """
    Build full comparison artifact from two run directories.

    Returns (json_dict, markdown_str).
    """
    off_summary_path = offline_run_dir / "summary.json"
    live_summary_path = live_run_dir / "summary.json"
    if not off_summary_path.is_file():
        raise FileNotFoundError(f"Offline summary not found: {off_summary_path}")
    if not live_summary_path.is_file():
        raise FileNotFoundError(f"Live summary not found: {live_summary_path}")

    off_summary = _load_json(off_summary_path)
    live_summary = _load_json(live_summary_path)

    deltas = compute_summary_deltas(off_summary, live_summary)
    off_per_task = off_summary.get("per_task_outcomes") or []
    live_per_task = live_summary.get("per_task_outcomes") or []
    per_task = _per_task_deltas(off_per_task, live_per_task)
    failure_bucket_deltas = _failure_bucket_deltas(off_per_task, live_per_task)
    task_ids = off_summary.get("task_ids") or []
    semantic_rca_deltas = _semantic_rca_deltas(offline_run_dir, live_run_dir, task_ids)
    integrity = _integrity_validity(off_summary, live_summary)
    judgment = derive_judgment(deltas, per_task)

    artifact = {
        "offline_run_dir": str(offline_run_dir),
        "live_run_dir": str(live_run_dir),
        "task_ids": task_ids,
        "same_task_set": set(off_summary.get("task_ids") or []) == set(live_summary.get("task_ids") or []),
        "summary_deltas": deltas,
        "per_task_deltas": per_task,
        "failure_bucket_deltas": failure_bucket_deltas,
        "semantic_rca_cause_deltas": semantic_rca_deltas,
        "integrity": integrity,
        "judgment": judgment,
    }

    lines = [
        "# Stage 33 — Offline vs Live-Model Gap Audit",
        "",
        f"**Judgment:** {judgment}",
        "",
        "## Summary Deltas",
        "| Metric | Offline | Live | Delta |",
        "|--------|---------|------|-------|",
    ]
    for k, v in deltas.items():
        if isinstance(v, dict) and "offline" in v and "live" in v and "delta" in v:
            lines.append(f"| {k} | {v['offline']} | {v['live']} | {v['delta']:+d} |")

    lines.extend([
        "",
        "## Per-Task Success Deltas",
        "| task_id | offline | live | delta |",
        "|---------|---------|------|-------|",
    ])
    for p in per_task:
        so = "✓" if p.get("success_offline") else "✗"
        sl = "✓" if p.get("success_live") else "✗"
        d = p.get("success_delta", 0)
        lines.append(f"| {p['task_id']} | {so} | {sl} | {d:+d} |")

    lines.extend([
        "",
        "## Integrity",
        f"- Offline run_valid_for_live_eval: {integrity['offline'].get('run_valid_for_live_eval')}",
        f"- Live run_valid_for_live_eval: {integrity['live'].get('run_valid_for_live_eval')}",
        "",
        "## Failure Bucket Deltas",
    ])
    for k, v in failure_bucket_deltas.items():
        lines.append(f"- {k}: offline={v['offline']}, live={v['live']}, delta={v['delta']:+d}")

    lines.extend([
        "",
        "## Semantic RCA Cause Deltas",
    ])
    for k, v in semantic_rca_deltas.items():
        lines.append(f"- {k}: offline={v['offline']}, live={v['live']}, delta={v['delta']:+d}")

    return artifact, "\n".join(lines)


# ---------------------------------------------------------------------------
# Stage 34 — Multi-live runs, agreement rate, variability, decision recommendation
# ---------------------------------------------------------------------------


def _load_summary(run_dir: Path) -> dict[str, Any]:
    p = run_dir / "summary.json"
    if not p.is_file():
        raise FileNotFoundError(f"Summary not found: {p}")
    return _load_json(p)


def compute_agreement_rate(
    offline_per_task: list[dict[str, Any]],
    live_per_task: list[dict[str, Any]],
) -> float:
    """Fraction of tasks where offline and live agree on success."""
    off_by_id = {p["task_id"]: p.get("success") for p in offline_per_task}
    live_by_id = {p["task_id"]: p.get("success") for p in live_per_task}
    common = set(off_by_id) & set(live_by_id)
    if not common:
        return 0.0
    agreed = sum(1 for tid in common if off_by_id[tid] == live_by_id[tid])
    return agreed / len(common)


def compute_live_variability(
    live_summaries: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Variability across repeated live runs: success_count std, per-task success variance.
    """
    if not live_summaries:
        return {"live_run_count": 0, "success_count_mean": 0, "success_count_std": 0, "per_task_agreement": {}}
    success_counts = [s.get("success_count", 0) or 0 for s in live_summaries]
    mean = sum(success_counts) / len(success_counts)
    variance = sum((x - mean) ** 2 for x in success_counts) / len(success_counts) if len(success_counts) > 1 else 0
    std = variance ** 0.5

    task_ids = live_summaries[0].get("task_ids") or []
    per_task_outcomes = [s.get("per_task_outcomes") or [] for s in live_summaries]
    by_id_list: dict[str, list[bool]] = {tid: [] for tid in task_ids}
    for outcomes in per_task_outcomes:
        for p in outcomes:
            tid = p.get("task_id")
            if tid in by_id_list:
                by_id_list[tid].append(bool(p.get("success")))
    per_task_agreement = {}
    for tid, successes in by_id_list.items():
        if not successes:
            continue
        n = len(successes)
        majority = max(successes.count(True), successes.count(False))
        per_task_agreement[tid] = majority / n

    return {
        "live_run_count": len(live_summaries),
        "success_count_mean": mean,
        "success_count_std": std,
        "success_count_min": min(success_counts),
        "success_count_max": max(success_counts),
        "per_task_agreement": per_task_agreement,
        "min_task_agreement": min(per_task_agreement.values()) if per_task_agreement else 1.0,
    }


def _task_type_from_spec(specs_by_id: dict[str, Any], task_id: str) -> str:
    """Infer canonical task type from spec tags for policy reporting."""
    spec = specs_by_id.get(task_id) if specs_by_id else None
    if not spec:
        return "unknown"
    tags = getattr(spec, "tags", ()) or ()
    # Canonical types: repair, feature, docs_consistency, explain_artifact, multi_file
    if "repair" in tags:
        return "repair"
    if "feature" in tags:
        return "feature"
    if "docs" in tags or "consistency" in tags:
        return "docs_consistency"
    if "explain" in tags:
        return "explain_artifact"
    if "multi_file" in tags or "refactor" in tags:
        return "multi_file"
    return "unknown"


def compute_task_type_agreement_rates(
    offline_per_task: list[dict[str, Any]],
    live_per_task: list[dict[str, Any]],
    specs_by_id: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    """Agreement rate per task type."""
    if not specs_by_id:
        return {}
    off_by_id = {p["task_id"]: p.get("success") for p in offline_per_task}
    live_by_id = {p["task_id"]: p.get("success") for p in live_per_task}
    by_type: dict[str, list[tuple[bool, bool]]] = {}
    for tid in set(off_by_id) & set(live_by_id):
        ttype = _task_type_from_spec(specs_by_id, tid)
        by_type.setdefault(ttype, []).append((off_by_id[tid], live_by_id[tid]))
    out: dict[str, dict[str, Any]] = {}
    for ttype, pairs in by_type.items():
        agreed = sum(1 for o, l in pairs if o == l)
        total = len(pairs)
        out[ttype] = {
            "agreement_rate": agreed / total if total else 0,
            "total": total,
            "agreed": agreed,
        }
    return out


def compute_per_task_flip_rate(per_task_deltas: list[dict[str, Any]]) -> float:
    """Fraction of tasks that flipped (offline != live) on success."""
    if not per_task_deltas:
        return 0.0
    flipped = sum(1 for p in per_task_deltas if p.get("success_offline") != p.get("success_live"))
    return flipped / len(per_task_deltas)


# ---------------------------------------------------------------------------
# Stage 36 — Outcome matrix, evidence quality, usefulness judgment
# ---------------------------------------------------------------------------

def compute_outcome_matrix(
    offline_per_task: list[dict[str, Any]],
    live_per_task: list[dict[str, Any]],
) -> dict[str, int]:
    """
    Count outcomes: pass_pass, fail_fail, offline_pass_live_fail, offline_fail_live_pass.
    Distinguishes representative agreement (nontrivial outcomes) from all-fail.
    """
    off_by_id = {p["task_id"]: bool(p.get("success")) for p in offline_per_task}
    live_by_id = {p["task_id"]: bool(p.get("success")) for p in live_per_task}
    common = set(off_by_id) & set(live_by_id)
    matrix = {"pass_pass": 0, "fail_fail": 0, "offline_pass_live_fail": 0, "offline_fail_live_pass": 0}
    for tid in common:
        o, l = off_by_id[tid], live_by_id[tid]
        if o and l:
            matrix["pass_pass"] += 1
        elif not o and not l:
            matrix["fail_fail"] += 1
        elif o and not l:
            matrix["offline_pass_live_fail"] += 1
        else:
            matrix["offline_fail_live_pass"] += 1
    return matrix


def compute_evidence_quality(
    task_count: int,
    live_run_count: int,
    outcome_matrix: dict[str, int],
    task_type_agreement: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """
    Evidence-quality metrics for policy conditioning.
    """
    nontrivial_success_count = (
        outcome_matrix.get("pass_pass", 0)
        + outcome_matrix.get("offline_pass_live_fail", 0)
        + outcome_matrix.get("offline_fail_live_pass", 0)
    )
    task_type_count = len(task_type_agreement)
    canonical_types = {"repair", "feature", "docs_consistency", "explain_artifact", "multi_file"}
    task_type_coverage = len(set(task_type_agreement) & canonical_types) / len(canonical_types) if canonical_types else 0
    return {
        "task_count": task_count,
        "live_repeat_count": live_run_count,
        "nontrivial_success_count": nontrivial_success_count,
        "task_type_count": task_type_count,
        "task_type_coverage": round(task_type_coverage, 2),
        "pass_pass_count": outcome_matrix.get("pass_pass", 0),
        "fail_fail_count": outcome_matrix.get("fail_fail", 0),
        "offline_pass_live_fail_count": outcome_matrix.get("offline_pass_live_fail", 0),
        "offline_fail_live_pass_count": outcome_matrix.get("offline_fail_live_pass", 0),
    }


def compute_representative_agreement_rate(
    outcome_matrix: dict[str, int],
) -> float | None:
    """
    Agreement rate among nontrivial outcomes only (excludes fail_fail).
    Returns None when no nontrivial outcomes (all fail/fail).
    """
    nontrivial = (
        outcome_matrix.get("pass_pass", 0)
        + outcome_matrix.get("offline_pass_live_fail", 0)
        + outcome_matrix.get("offline_fail_live_pass", 0)
    )
    if nontrivial == 0:
        return None
    agreed = outcome_matrix.get("pass_pass", 0)
    return agreed / nontrivial


def derive_usefulness_judgment(
    judgment: Judgment,
    evidence_quality: dict[str, Any],
    agreement_rate: float,
) -> tuple[UsefulnessJudgment, str]:
    """
    Usefulness judgment: is evidence strong enough to support policy?
    Returns (judgment, explanation).
    """
    task_count = evidence_quality.get("task_count", 0)
    live_count = evidence_quality.get("live_repeat_count", 0)
    nontrivial = evidence_quality.get("nontrivial_success_count", 0)
    task_type_coverage = evidence_quality.get("task_type_coverage", 0)

    if judgment == "offline_is_misleading":
        return "misleading", "Offline is misleading; do not rely on offline for policy."

    if task_count < 4 or live_count < 2:
        return "insufficient_evidence", (
            f"Insufficient evidence: task_count={task_count} (need ≥4), live_repeat_count={live_count} (need ≥2)."
        )

    if nontrivial == 0 and task_count < 8:
        return "insufficient_evidence", (
            f"All outcomes are fail/fail; agreement is vacuously high. "
            f"nontrivial_success_count=0, task_count={task_count}. Need nontrivial outcomes or ≥8 tasks."
        )

    strong_evidence = (
        task_count >= 6
        and live_count >= 3
        and (nontrivial >= 2 or task_type_coverage >= 0.6)
    )

    if judgment == "offline_is_predictive" and strong_evidence and agreement_rate >= 0.75:
        return "predictive_and_useful", (
            f"Offline predictive with strong evidence: task_count={task_count}, "
            f"live_repeats={live_count}, nontrivial={nontrivial}, agreement={agreement_rate:.2%}."
        )

    if judgment == "offline_is_predictive":
        return "predictive_but_low_evidence", (
            f"Offline predictive but evidence is weak: task_count={task_count}, "
            f"live_repeats={live_count}, nontrivial={nontrivial}. Policy is provisional."
        )

    if judgment == "offline_is_partially_predictive":
        return "predictive_but_low_evidence", (
            f"Offline partially predictive; evidence quality: task_count={task_count}, "
            f"nontrivial={nontrivial}. Policy is provisional."
        )

    return "insufficient_evidence", "Evidence does not support a clear judgment."


def derive_policy_support(usefulness: UsefulnessJudgment) -> PolicySupport:
    """Policy support: strongly_supported only when predictive_and_useful."""
    if usefulness == "predictive_and_useful":
        return "strongly_supported"
    return "provisionally_supported"


def compute_live_variability_by_task_type(
    live_summaries: list[dict[str, Any]],
    specs_by_id: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    """Live variability (success std, min_task_agreement) per task type."""
    if not live_summaries or not specs_by_id:
        return {}
    task_ids = live_summaries[0].get("task_ids") or []
    per_task_outcomes = [s.get("per_task_outcomes") or [] for s in live_summaries]
    by_id_list: dict[str, list[bool]] = {tid: [] for tid in task_ids}
    for outcomes in per_task_outcomes:
        for p in outcomes:
            tid = p.get("task_id")
            if tid in by_id_list:
                by_id_list[tid].append(bool(p.get("success")))
    by_type: dict[str, list[float]] = {}
    for tid, successes in by_id_list.items():
        if not successes:
            continue
        ttype = _task_type_from_spec(specs_by_id, tid)
        n = len(successes)
        majority = max(successes.count(True), successes.count(False))
        by_type.setdefault(ttype, []).append(majority / n)
    out: dict[str, dict[str, Any]] = {}
    for ttype, agreements in by_type.items():
        min_agr = min(agreements)
        out[ttype] = {"min_task_agreement": min_agr, "task_count": len(agreements)}
    return out


def compute_retry_replan_variability(live_summaries: list[dict[str, Any]]) -> dict[str, Any]:
    """Retry and replan variability across live runs."""
    if not live_summaries:
        return {}
    retries = [s.get("retries_used_aggregate") or 0 for s in live_summaries]
    replans = [s.get("replans_used_aggregate") or 0 for s in live_summaries]
    r_vals = [_safe_int(x) for x in retries]
    p_vals = [_safe_int(x) for x in replans]
    r_mean = sum(r_vals) / len(r_vals) if r_vals else 0
    p_mean = sum(p_vals) / len(p_vals) if p_vals else 0
    r_var = sum((x - r_mean) ** 2 for x in r_vals) / len(r_vals) if len(r_vals) > 1 else 0
    p_var = sum((x - p_mean) ** 2 for x in p_vals) / len(p_vals) if len(p_vals) > 1 else 0
    return {
        "retries_mean": r_mean,
        "retries_std": r_var ** 0.5,
        "replans_mean": p_mean,
        "replans_std": p_var ** 0.5,
    }


def _judgment_per_task_type(
    task_type_agreement: dict[str, dict[str, Any]],
) -> dict[str, Literal["predictive", "partially_predictive", "misleading"]]:
    """Per-task-type judgment: predictive (>=0.9), misleading (<0.5), else partially_predictive."""
    out: dict[str, Literal["predictive", "partially_predictive", "misleading"]] = {}
    for ttype, data in task_type_agreement.items():
        rate = data.get("agreement_rate", 0)
        if rate >= 0.9:
            out[ttype] = "predictive"
        elif rate < 0.5:
            out[ttype] = "misleading"
        else:
            out[ttype] = "partially_predictive"
    return out


GatingPolicy = Literal[
    "offline_primary_nightly_live_spot_check",
    "offline_primary_selective_live_gate_edit_multifile",
    "live_too_unstable_to_gate",
    "live_primary_specific_task_classes",
]


def derive_gating_policy(
    recommendation: DecisionRecommendation,
    task_type_judgments: dict[str, Literal["predictive", "partially_predictive", "misleading"]],
    live_variability: dict[str, Any],
    usefulness: UsefulnessJudgment,
    policy_support: PolicySupport,
) -> tuple[GatingPolicy, str]:
    """
    Produce concrete gating policy and human-readable wording.
    Policy wording is conditional on evidence quality (Stage 36).
    """
    live_std = live_variability.get("success_count_std", 0)
    min_agreement = live_variability.get("min_task_agreement", 1.0)
    support_note = " (strongly supported)" if policy_support == "strongly_supported" else " (provisionally supported)"

    if recommendation == "live_too_unstable_to_gate":
        return (
            "live_too_unstable_to_gate",
            "Live model too unstable to gate. Use offline as primary; do not gate on live.",
        )

    if recommendation == "live_primary":
        misleading = [t for t, j in task_type_judgments.items() if j == "misleading"]
        return (
            "live_primary_specific_task_classes",
            f"Live primary for task classes where offline is misleading: {misleading or ['N/A']}. "
            "Gate on live for those; offline for others.",
        )

    if recommendation == "offline_primary_selective_live_gate":
        partial = [t for t, j in task_type_judgments.items() if j == "partially_predictive"]
        misleading = [t for t, j in task_type_judgments.items() if j == "misleading"]
        gate_types = list(set(partial + misleading))
        return (
            "offline_primary_selective_live_gate_edit_multifile",
            f"Offline primary; selective live gate for: {gate_types or ['EDIT/multi_file']}. "
            f"Nightly live spot check recommended.{support_note}",
        )

    if recommendation == "offline_primary":
        return (
            "offline_primary_nightly_live_spot_check",
            f"Offline primary. Nightly live spot check on paired8 (or subset) to validate offline remains predictive.{support_note}",
        )

    return (
        "offline_primary_nightly_live_spot_check",
        f"Offline primary. Nightly live spot check recommended.{support_note}",
    )


def derive_decision_recommendation(
    judgment: Judgment,
    agreement_rate: float,
    live_variability: dict[str, Any],
    per_task_deltas: list[dict[str, Any]],
    specs_by_id: dict[str, Any] | None = None,
) -> tuple[DecisionRecommendation, dict[str, Any]]:
    """
    Blunt decision recommendation for gating strategy.

    Returns (recommendation, evidence_dict).
    """
    evidence: dict[str, Any] = {
        "judgment": judgment,
        "agreement_rate": agreement_rate,
        "live_success_std": live_variability.get("success_count_std", 0),
        "live_run_count": live_variability.get("live_run_count", 0),
        "min_task_agreement": live_variability.get("min_task_agreement", 1.0),
    }

    live_std = live_variability.get("success_count_std", 0)
    min_agreement = live_variability.get("min_task_agreement", 1.0)

    # live_too_unstable_to_gate: high variance across live runs
    if live_variability.get("live_run_count", 0) >= 2 and (live_std >= 1.0 or min_agreement < 1.0):
        return "live_too_unstable_to_gate", evidence

    # offline_primary: offline predictive and agreement high
    if judgment == "offline_is_predictive" and agreement_rate >= 0.75:
        return "offline_primary", evidence

    # offline_is_misleading: offline not reliable
    if judgment == "offline_is_misleading":
        if live_std < 0.5 and min_agreement >= 1.0:
            return "live_primary", evidence
        return "live_too_unstable_to_gate", evidence

    # offline_is_partially_predictive: which task types?
    if judgment == "offline_is_partially_predictive":
        agreeing_tasks = [p for p in per_task_deltas if p.get("success_offline") == p.get("success_live")]
        disagreeing_tasks = [p for p in per_task_deltas if p.get("success_offline") != p.get("success_live")]
        if specs_by_id:
            agreeing_types = [_task_type_from_spec(specs_by_id, p["task_id"]) for p in agreeing_tasks]
            disagreeing_types = [_task_type_from_spec(specs_by_id, p["task_id"]) for p in disagreeing_tasks]
            evidence["agreeing_task_types"] = agreeing_types
            evidence["disagreeing_task_types"] = disagreeing_types
        if agreement_rate >= 0.5 and live_std < 0.5:
            return "offline_primary_selective_live_gate", evidence
        if live_std >= 1.0:
            return "live_too_unstable_to_gate", evidence
        return "offline_primary_selective_live_gate", evidence

    return "offline_primary", evidence


def build_multi_live_comparison_artifact(
    offline_run_dir: Path,
    live_run_dirs: list[Path],
    specs_by_id: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str]:
    """
    Build comparison artifact for 1 offline + N live runs.

    Uses first live run for per-task deltas; aggregates all for variability.
    Returns (json_dict, markdown_str).
    """
    off_summary = _load_summary(offline_run_dir)
    live_summaries = [_load_summary(d) for d in live_run_dirs]
    if not live_summaries:
        raise ValueError("At least one live run required")

    # Use first live for deltas (or median/mode if we had that)
    first_live = live_summaries[0]
    deltas = compute_summary_deltas(off_summary, first_live)
    off_per_task = off_summary.get("per_task_outcomes") or []
    live_per_task = first_live.get("per_task_outcomes") or []
    per_task = _per_task_deltas(off_per_task, live_per_task)
    failure_bucket_deltas = _failure_bucket_deltas(off_per_task, live_per_task)
    task_ids = off_summary.get("task_ids") or []
    semantic_rca_deltas = _semantic_rca_deltas(offline_run_dir, live_run_dirs[0], task_ids)
    integrity = _integrity_validity(off_summary, first_live)
    judgment = derive_judgment(deltas, per_task)

    agreement_rate = compute_agreement_rate(off_per_task, live_per_task)
    live_variability = compute_live_variability(live_summaries)
    recommendation, rec_evidence = derive_decision_recommendation(
        judgment, agreement_rate, live_variability, per_task, specs_by_id
    )

    # Stage 35: task-type-level metrics
    task_type_agreement = compute_task_type_agreement_rates(off_per_task, live_per_task, specs_by_id)
    per_task_flip_rate = compute_per_task_flip_rate(per_task)
    live_variability_by_type = compute_live_variability_by_task_type(live_summaries, specs_by_id)
    retry_replan_variability = compute_retry_replan_variability(live_summaries)
    task_type_judgments = _judgment_per_task_type(task_type_agreement)

    # Stage 36: outcome matrix, evidence quality, usefulness judgment
    outcome_matrix = compute_outcome_matrix(off_per_task, live_per_task)
    evidence_quality = compute_evidence_quality(
        len(task_ids), len(live_run_dirs), outcome_matrix, task_type_agreement
    )
    representative_agreement = compute_representative_agreement_rate(outcome_matrix)
    usefulness_judgment, usefulness_explanation = derive_usefulness_judgment(
        judgment, evidence_quality, agreement_rate
    )
    policy_support = derive_policy_support(usefulness_judgment)
    gating_policy, gating_policy_wording = derive_gating_policy(
        recommendation, task_type_judgments, live_variability,
        usefulness_judgment, policy_support,
    )

    artifact = {
        "offline_run_dir": str(offline_run_dir),
        "live_run_dirs": [str(d) for d in live_run_dirs],
        "live_run_count": len(live_run_dirs),
        "task_ids": task_ids,
        "same_task_set": True,
        "summary_deltas": deltas,
        "per_task_deltas": per_task,
        "failure_bucket_deltas": failure_bucket_deltas,
        "semantic_rca_cause_deltas": semantic_rca_deltas,
        "integrity": integrity,
        "judgment": judgment,
        "agreement_rate": agreement_rate,
        "per_task_flip_rate": per_task_flip_rate,
        "task_type_agreement_rate": task_type_agreement,
        "task_type_judgments": task_type_judgments,
        "live_variability": live_variability,
        "live_variability_by_task_type": live_variability_by_type,
        "retry_replan_variability": retry_replan_variability,
        "decision_recommendation": recommendation,
        "gating_policy": gating_policy,
        "gating_policy_wording": gating_policy_wording,
        "recommendation_evidence": rec_evidence,
        "outcome_matrix": outcome_matrix,
        "evidence_quality": evidence_quality,
        "representative_agreement_rate": representative_agreement,
        "usefulness_judgment": usefulness_judgment,
        "usefulness_explanation": usefulness_explanation,
        "policy_support": policy_support,
    }

    lines = [
        "# Stage 34/35/36 — Evidence-Aware Offline vs Live-Model Gap Audit",
        "",
        f"**Gating Policy:** {gating_policy}",
        f"**Policy Support:** {policy_support}",
        "",
        f"**Policy Wording:** {gating_policy_wording}",
        "",
        f"**Usefulness Judgment:** {usefulness_judgment}",
        f"**Usefulness Explanation:** {usefulness_explanation}",
        "",
        f"**Decision Recommendation:** {recommendation}",
        f"**Judgment:** {judgment}",
        f"**Agreement rate (raw):** {agreement_rate:.2%}",
        f"**Representative agreement (nontrivial only):** {representative_agreement if representative_agreement is not None else 'N/A (all fail/fail)'}",
        f"**Per-task flip rate:** {per_task_flip_rate:.2%}",
        f"**Live runs:** {len(live_run_dirs)}",
        f"**Live success variability (std):** {live_variability.get('success_count_std', 0):.2f}",
        "",
        "## Decision Questions",
        "",
        "### Is offline predictive of live_model behavior?",
        f"- Overall: {judgment}",
        "",
        "### For which task types is offline predictive, partially predictive, or misleading?",
    ]
    for ttype, j in sorted(task_type_judgments.items()):
        rate = task_type_agreement.get(ttype, {}).get("agreement_rate", 0)
        lines.append(f"- {ttype}: {j} (agreement {rate:.2%})")

    lines.extend([
        "",
        "## Outcome Matrix",
        f"- pass_pass: {outcome_matrix.get('pass_pass', 0)}",
        f"- fail_fail: {outcome_matrix.get('fail_fail', 0)}",
        f"- offline_pass_live_fail: {outcome_matrix.get('offline_pass_live_fail', 0)}",
        f"- offline_fail_live_pass: {outcome_matrix.get('offline_fail_live_pass', 0)}",
        "",
        "## Evidence Quality",
        f"- task_count: {evidence_quality.get('task_count', 0)}",
        f"- live_repeat_count: {evidence_quality.get('live_repeat_count', 0)}",
        f"- nontrivial_success_count: {evidence_quality.get('nontrivial_success_count', 0)}",
        f"- task_type_coverage: {evidence_quality.get('task_type_coverage', 0):.2%}",
        "",
        "### Is live_model stable enough to be used as a gate?",
        f"- Live success std: {live_variability.get('success_count_std', 0):.2f}",
        f"- Min per-task agreement across runs: {live_variability.get('min_task_agreement', 1):.2%}",
        f"- Recommendation: {recommendation}",
        "",
        "## Summary Deltas (offline vs first live)",
        "| Metric | Offline | Live | Delta |",
        "|--------|---------|------|-------|",
    ])
    for k, v in deltas.items():
        if isinstance(v, dict) and "offline" in v and "live" in v and "delta" in v:
            lines.append(f"| {k} | {v['offline']} | {v['live']} | {v['delta']:+d} |")

    lines.extend([
        "",
        "## Per-Task Success Deltas",
        "| task_id | offline | live | delta |",
        "|---------|---------|------|-------|",
    ])
    for p in per_task:
        so = "✓" if p.get("success_offline") else "✗"
        sl = "✓" if p.get("success_live") else "✗"
        d = p.get("success_delta", 0)
        lines.append(f"| {p['task_id']} | {so} | {sl} | {d:+d} |")

    lines.extend([
        "",
        "## Live Variability",
        f"- success_count: mean={live_variability.get('success_count_mean', 0):.1f}, "
        f"std={live_variability.get('success_count_std', 0):.2f}, "
        f"min={live_variability.get('success_count_min', 0)}, max={live_variability.get('success_count_max', 0)}",
        "",
        "## Live Variability by Task Type",
    ])
    for ttype, v in sorted(live_variability_by_type.items()):
        lines.append(f"- {ttype}: min_task_agreement={v.get('min_task_agreement', 0):.2%}")
    lines.extend([
        "",
        "## Retry/Replan Variability",
        f"- retries: mean={retry_replan_variability.get('retries_mean', 0):.1f}, std={retry_replan_variability.get('retries_std', 0):.2f}",
        f"- replans: mean={retry_replan_variability.get('replans_mean', 0):.1f}, std={retry_replan_variability.get('replans_std', 0):.2f}",
        "",
        "## Integrity",
        f"- Offline run_valid_for_live_eval: {integrity['offline'].get('run_valid_for_live_eval')}",
        f"- Live run_valid_for_live_eval: {integrity['live'].get('run_valid_for_live_eval')}",
    ])

    return artifact, "\n".join(lines)
