"""
A/B offline evaluation: ENABLE_LLM_BUNDLE_SELECTOR off vs on.

Runs the same suite twice and compares retrieval quality metrics,
with selector-focused summary fields.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

from tests.agent_eval.check_retrieval_quality import (
    aggregate_retrieval_metrics,
    build_per_task_diffs,
    compare_ab_runs,
    diagnostic_verdict_kind_expansion,
    load_retrieval_quality_records_from_run_dir,
    selector_quality_verdict,
)
from tests.agent_eval.runner import REPO_ROOT, run_suite
from tests.agent_eval.suites.search_stack import architecture_task_ids, selector_hard_task_ids


def _fmt(v: object) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.4f}".rstrip("0").rstrip(".")
    return str(v)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _get_allowlist(args) -> frozenset[str] | None:
    """Resolve task allowlist from parsed args."""
    if args.selector_hard_only:
        return selector_hard_task_ids()
    if args.architecture_only:
        return architecture_task_ids()
    return None


def _selector_verdict(off_agg: dict, on_agg: dict) -> str:
    """
    Conservative offline verdict for selector alpha rollout.
    - Prefer OFF when replanner trigger worsens and architecture retention does not improve.
    """
    if (off_agg.get("task_count") or 0) < 1 or (on_agg.get("task_count") or 0) < 1:
        return "inconclusive"
    impl_delta = (on_agg.get("average_selected_impl_body_count") or 0) - (off_agg.get("average_selected_impl_body_count") or 0)
    linked_delta = (on_agg.get("average_selected_linked_row_count") or 0) - (off_agg.get("average_selected_linked_row_count") or 0)
    replanner_delta = (on_agg.get("replanner_trigger_rate") or 0) - (off_agg.get("replanner_trigger_rate") or 0)
    if replanner_delta > 0.05 and impl_delta <= 0 and linked_delta <= 0:
        return "stay_off"
    if impl_delta > 0 or linked_delta > 0:
        return "canary_worthy"
    return "inconclusive"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="A/B ENABLE_LLM_BUNDLE_SELECTOR offline retrieval metrics.")
    p.add_argument("--output-dir", type=Path, default=Path("artifacts/bundle_selector_ab"))
    p.add_argument("--suite", default="search_stack")
    p.add_argument("--execution-mode", default="offline", choices=("mocked", "offline", "live_model", "real"))
    p.add_argument(
        "--architecture-only",
        action="store_true",
        default=True,
        help="Run only architecture tasks (default true for selector).",
    )
    p.add_argument(
        "--selector-hard-only",
        action="store_true",
        help="Run only selector_hard tasks (harder architecture slice for alpha eval).",
    )
    p.add_argument(
        "--timeout-per-task",
        type=int,
        default=None,
        help="Timeout in seconds per task (e.g. 5). No timeout if not set.",
    )
    args = p.parse_args(argv)

    out_root = (REPO_ROOT / args.output_dir).resolve() if not args.output_dir.is_absolute() else args.output_dir
    ts = time.strftime("%Y%m%d_%H%M%S")
    batch = out_root / f"run_{ts}"
    arm_off = batch / "ENABLE_LLM_BUNDLE_SELECTOR_0"
    arm_on = batch / "ENABLE_LLM_BUNDLE_SELECTOR_1"
    allowlist = _get_allowlist(args)
    noop_out = Path("artifacts/agent_eval_runs/_bundle_selector_ab_placeholder")

    with patch("config.retrieval_config.ENABLE_LLM_BUNDLE_SELECTOR", False):
        run_suite(
            args.suite,
            noop_out,
            repo_root=REPO_ROOT,
            execution_mode=args.execution_mode,
            task_id_allowlist=allowlist,
            output_dir=arm_off,
            task_timeout_seconds=args.timeout_per_task,
        )
    with patch("config.retrieval_config.ENABLE_LLM_BUNDLE_SELECTOR", True), patch(
        "config.retrieval_config.FORCE_SELECTOR_IN_EVAL", True
    ), patch("config.retrieval_config.ENABLE_BUNDLE_SELECTION", True), patch(
        "config.retrieval_config.ENABLE_EXPLORATION", True
    ):
        run_suite(
            args.suite,
            noop_out,
            repo_root=REPO_ROOT,
            execution_mode=args.execution_mode,
            task_id_allowlist=allowlist,
            output_dir=arm_on,
            task_timeout_seconds=args.timeout_per_task,
        )

    rec_off = load_retrieval_quality_records_from_run_dir(arm_off)
    rec_on = load_retrieval_quality_records_from_run_dir(arm_on)
    agg_off = aggregate_retrieval_metrics(rec_off)
    agg_on = aggregate_retrieval_metrics(rec_on)
    comparison = compare_ab_runs(
        agg_off,
        agg_on,
        label_off="ENABLE_LLM_BUNDLE_SELECTOR=0",
        label_on="ENABLE_LLM_BUNDLE_SELECTOR=1",
        rec_off=rec_off,
        rec_on=rec_on,
    )
    agg_off_cmp = comparison["aggregate_off"]
    agg_on_cmp = comparison["aggregate_on"]
    comparison["diagnostic"] = diagnostic_verdict_kind_expansion(agg_off_cmp, agg_on_cmp)
    comparison["per_task_diffs"] = build_per_task_diffs(rec_off, rec_on)
    comparison["verdict_selector"] = _selector_verdict(agg_off_cmp, agg_on_cmp)
    comparison["verdict_selector_quality"] = selector_quality_verdict(agg_off_cmp, agg_on_cmp)
    comparison["selector_decision_source"] = "stub" if args.execution_mode == "offline" else "model"
    comparison["selector_quality_confidence"] = "low" if args.execution_mode == "offline" else "normal"
    comparison["quality_verdict_summary"] = (
        "quality verdict is directional only" if args.execution_mode == "offline" else "quality verdict is representative"
    )
    comparison["run_timestamp"] = ts
    comparison["arm_off_run_dir"] = str(arm_off)
    comparison["arm_on_run_dir"] = str(arm_on)
    comparison["selector_hard_only"] = args.selector_hard_only

    # Hard-slice aggregates and per-task table when running selector-hard tasks
    hard_ids = selector_hard_task_ids()
    hard_diffs = [d for d in comparison["per_task_diffs"] if d.get("task_id") in hard_ids]
    rec_off_by_tid = {r.get("task_id"): r for r in rec_off if r.get("task_id")}
    rec_on_by_tid = {r.get("task_id"): r for r in rec_on if r.get("task_id")}
    if hard_diffs and args.selector_hard_only:
        hard_rows: list[dict] = []
        for d in hard_diffs:
            tid = d.get("task_id")
            r_off = rec_off_by_tid.get(tid) or {}
            r_on = rec_on_by_tid.get(tid) or {}
            hard_rows.append({
                "task_id": tid,
                "final_context_count_off": d.get("final_context_count_off"),
                "final_context_count_on": d.get("final_context_count_on"),
                "selected_id_count_on": r_on.get("selected_id_count"),
                "linked_retention": d.get("selected_linked_retention_rate"),
                "impl_retention": d.get("selected_impl_retention_rate"),
                "architecture_ready_off": r_off.get("architecture_answer_ready"),
                "architecture_ready_on": r_on.get("architecture_answer_ready"),
                "replanner_off": r_off.get("replanner_triggered"),
                "replanner_on": r_on.get("replanner_triggered"),
            })
        comparison["selector_hard_per_task"] = hard_rows
        hard_rec_off = [r for r in rec_off if r.get("task_id") in hard_ids]
        hard_rec_on = [r for r in rec_on if r.get("task_id") in hard_ids]
        hard_agg_off = aggregate_retrieval_metrics(hard_rec_off)
        hard_agg_on = aggregate_retrieval_metrics(hard_rec_on)
        from tests.agent_eval.check_retrieval_quality import compute_selector_quality_aggregates

        sq_hard = compute_selector_quality_aggregates(hard_rec_off, hard_rec_on)
        for k, v in sq_hard.items():
            hard_agg_on[k] = v
        comparison["selector_hard_aggregate_off"] = hard_agg_off
        comparison["selector_hard_aggregate_on"] = hard_agg_on
        comparison["verdict_selector_hard"] = _selector_verdict(hard_agg_off, hard_agg_on)
        comparison["verdict_selector_quality_hard"] = selector_quality_verdict(hard_agg_off, hard_agg_on)

    json_path = batch / "bundle_selector_ab_comparison.json"
    md_path = batch / "bundle_selector_ab_summary.md"
    _write_text(json_path, json.dumps(comparison, indent=2))

    deltas = comparison.get("deltas_on_minus_off") or {}
    verdict = comparison.get("verdict_selector") or "inconclusive"
    verdict_q = comparison.get("verdict_selector_quality") or "neutral"
    agg_off_cmp = comparison.get("aggregate_off") or agg_off
    agg_on_cmp = comparison.get("aggregate_on") or agg_on
    src = comparison.get("selector_decision_source", "—")
    conf = comparison.get("selector_quality_confidence", "—")
    q_summary = comparison.get("quality_verdict_summary", "—")
    lines = [
        "# ENABLE_LLM_BUNDLE_SELECTOR — offline A/B",
        "",
        f"**Verdict:** `{verdict}`",
        f"**Selector quality verdict:** `{verdict_q}`",
        f"**Selector decision source:** `{src}` | **Confidence:** `{conf}`",
        f"**{q_summary}**",
        "",
        "| Metric | OFF | ON | Δ (ON − OFF) |",
        "|--------|-----|----|--------------|",
    ]
    keys = (
        "task_count",
        "bundle_selector_usage_rate",
        "average_selected_id_count",
        "average_selected_impl_body_count",
        "average_selected_linked_row_count",
        "average_selected_test_row_count",
        "selected_rows_only_rate",
        "replanner_trigger_rate",
        "architecture_ok_rate",
    )
    for k in keys:
        lines.append(f"| {k} | {_fmt(agg_off_cmp.get(k))} | {_fmt(agg_on_cmp.get(k))} | {_fmt(deltas.get(k))} |")

    lines.extend(
        [
            "",
            f"**Timeout count (excluded from averages):** {_fmt(agg_on_cmp.get('timeout_count'))}",
            "",
            "## Key metrics (watch these)",
            f"- **linked_retention_rate:** {_fmt(agg_on_cmp.get('selected_linked_retention_rate'))}",
            f"- **architecture_safe_selection_rate:** {_fmt(agg_on_cmp.get('architecture_safe_selection_rate'))}",
            f"- **exploration_linked_gain:** {_fmt(agg_on_cmp.get('average_exploration_linked_gain'))}",
            f"- **structure_score:** {_fmt(agg_on_cmp.get('average_structure_score'))}",
            "",
            "## Selector quality",
            f"- Impl retention rate: {_fmt(agg_on_cmp.get('selected_impl_retention_rate'))}",
            f"- Linked retention rate: {_fmt(agg_on_cmp.get('selected_linked_retention_rate'))}",
            f"- Test drift rate: {_fmt(agg_on_cmp.get('selected_test_drift_rate'))}",
            f"- Architecture answer ready rate: {_fmt(agg_on_cmp.get('architecture_answer_ready_rate'))}",
            f"- Selector vs baseline context delta: {_fmt(agg_on_cmp.get('average_selector_vs_baseline_context_delta'))}",
            f"- Architecture tasks lost all links rate: {_fmt(agg_on_cmp.get('architecture_tasks_lost_all_links_rate'))}",
            f"- Useful compaction rate: {_fmt(agg_on_cmp.get('useful_compaction_rate'))}",
            f"- Selector integrity rate: {_fmt(agg_on_cmp.get('selector_integrity_rate'))}",
            f"- Bundle coherence score: {_fmt(agg_on_cmp.get('average_bundle_coherence_score'))}",
            f"- Bridge usage rate: {_fmt(agg_on_cmp.get('average_bridge_usage_rate'))}",
            f"- Multi-hop satisfied rate: {_fmt(agg_on_cmp.get('multi_hop_satisfied_rate'))}",
            f"- Verdict: `{verdict_q}`",
            "",
            "## Interpretation",
            f"- Selector usage rate: {_fmt(agg_on_cmp.get('bundle_selector_usage_rate'))}",
            f"- Avg selected IDs: {_fmt(agg_on_cmp.get('average_selected_id_count'))}",
            f"- Impl-body retention: {_fmt(agg_on_cmp.get('average_selected_impl_body_count'))}",
            f"- Linked-row retention (architecture): {_fmt(agg_on_cmp.get('average_selected_linked_row_count'))}",
            f"- Replanner trigger rate delta: {_fmt(deltas.get('replanner_trigger_rate'))}",
            "",
            f"- OFF run: `{arm_off}`",
            f"- ON run: `{arm_on}`",
            f"- Machine-readable: `{json_path}`",
            "",
        ]
    )
    if args.selector_hard_only and comparison.get("selector_hard_per_task"):
        hard_verdict = comparison.get("verdict_selector_hard", "—")
        hard_q = comparison.get("verdict_selector_quality_hard", "—")
        lines.extend(
            [
                "## Selector-hard slice",
                "",
                f"**Hard-slice verdict:** `{hard_verdict}` | **Quality:** `{hard_q}`",
                "",
                "| task_id | ctx_off | ctx_on | sel_on | linked_ret | impl_ret | arch_ready_off | arch_ready_on | replanner_off | replanner_on |",
                "|---------|---------|--------|--------|------------|----------|----------------|---------------|---------------|--------------|",
            ]
        )
        for row in comparison["selector_hard_per_task"]:
            lines.append(
                f"| {row.get('task_id', '—')} | "
                f"{_fmt(row.get('final_context_count_off'))} | "
                f"{_fmt(row.get('final_context_count_on'))} | "
                f"{_fmt(row.get('selected_id_count_on'))} | "
                f"{_fmt(row.get('linked_retention'))} | "
                f"{_fmt(row.get('impl_retention'))} | "
                f"{_fmt(row.get('architecture_ready_off'))} | "
                f"{_fmt(row.get('architecture_ready_on'))} | "
                f"{_fmt(row.get('replanner_off'))} | "
                f"{_fmt(row.get('replanner_on'))} |"
            )
        lines.append("")
    _write_text(md_path, "\n".join(lines))
    print(f"bundle_selector_ab: verdict={verdict} quality_verdict={verdict_q} json={json_path} md={md_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

