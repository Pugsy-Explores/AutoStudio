"""
A/B offline evaluation: ENABLE_KIND_AWARE_EXPANSION off vs on.

Runs the same suite twice with a patched flag on ``agent.retrieval.retrieval_expander``
(aligned with tests/test_kind_aware_expansion.py). Writes comparison JSON + markdown under --output-dir.

Example:
  cd /path/to/AutoStudio && python3 -m tests.agent_eval.run_kind_expansion_ab \\
      --output-dir artifacts/kind_expansion_ab \\
      --suite search_stack --execution-mode offline

  Architecture-only slice (isolate explain-style retrieval):
  python3 -m tests.agent_eval.run_kind_expansion_ab --output-dir artifacts/kind_expansion_ab \\
      --suite search_stack --execution-mode offline --architecture-only
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
    split_per_task_diffs_by_architecture,
    verdict_kind_expansion_step1,
)
from tests.agent_eval.runner import REPO_ROOT, run_suite
from tests.agent_eval.suites.search_stack import architecture_task_ids, retrieval_quality_task_ids


def _fmt(v: object) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.4f}".rstrip("0").rstrip(".")
    return str(v)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="A/B ENABLE_KIND_AWARE_EXPANSION offline retrieval metrics.")
    p.add_argument(
        "--output-dir",
        type=Path,
        default=Path("artifacts/kind_expansion_ab"),
        help="Directory for arm subdirs + comparison artifacts",
    )
    p.add_argument("--suite", default="search_stack", help="Suite name (default: search_stack)")
    p.add_argument(
        "--execution-mode",
        default="offline",
        choices=("mocked", "offline", "live_model", "real"),
        help="Default offline (deterministic + stubs)",
    )
    p.add_argument(
        "--full-suite",
        action="store_true",
        help="Run every task in the suite (default: only retrieval_quality-tagged tasks)",
    )
    p.add_argument(
        "--architecture-only",
        action="store_true",
        help="Run only architecture-oriented tasks (isolate explain-style retrieval from symbol lookup)",
    )
    args = p.parse_args(argv)

    out_root = (REPO_ROOT / args.output_dir).resolve() if not args.output_dir.is_absolute() else args.output_dir
    ts = time.strftime("%Y%m%d_%H%M%S")
    batch = out_root / f"run_{ts}"
    arm_off = batch / "ENABLE_KIND_AWARE_EXPANSION_0"
    arm_on = batch / "ENABLE_KIND_AWARE_EXPANSION_1"

    if args.full_suite:
        allowlist = None
    elif args.architecture_only:
        allowlist = architecture_task_ids()
    else:
        allowlist = retrieval_quality_task_ids()

    noop_out = Path("artifacts/agent_eval_runs/_kind_expansion_ab_placeholder")

    with patch("agent.retrieval.retrieval_expander.ENABLE_KIND_AWARE_EXPANSION", False):
        run_suite(
            args.suite,
            noop_out,
            repo_root=REPO_ROOT,
            execution_mode=args.execution_mode,
            task_id_allowlist=allowlist,
            output_dir=arm_off,
        )

    with patch("agent.retrieval.retrieval_expander.ENABLE_KIND_AWARE_EXPANSION", True):
        run_suite(
            args.suite,
            noop_out,
            repo_root=REPO_ROOT,
            execution_mode=args.execution_mode,
            task_id_allowlist=allowlist,
            output_dir=arm_on,
        )

    rec_off = load_retrieval_quality_records_from_run_dir(arm_off)
    rec_on = load_retrieval_quality_records_from_run_dir(arm_on)
    agg_off = aggregate_retrieval_metrics(rec_off)
    agg_on = aggregate_retrieval_metrics(rec_on)
    comparison = compare_ab_runs(agg_off, agg_on)
    verdict = verdict_kind_expansion_step1(agg_off, agg_on)
    comparison["verdict_step1"] = verdict
    comparison["diagnostic"] = diagnostic_verdict_kind_expansion(agg_off, agg_on)
    per_task_diffs = build_per_task_diffs(rec_off, rec_on)
    comparison["per_task_diffs"] = per_task_diffs
    comparison["per_task_diffs_architecture"], comparison["per_task_diffs_generic"] = split_per_task_diffs_by_architecture(per_task_diffs)
    comparison["run_timestamp"] = ts
    comparison["arm_off_run_dir"] = str(arm_off)
    comparison["arm_on_run_dir"] = str(arm_on)

    json_path = batch / "kind_expansion_ab_comparison.json"
    md_path = batch / "kind_expansion_ab_summary.md"
    _write_text(json_path, json.dumps(comparison, indent=2))
    deltas = comparison.get("deltas_on_minus_off") or {}
    diag = comparison.get("diagnostic") or {}
    lines = [
        "# ENABLE_KIND_AWARE_EXPANSION — offline A/B",
        "",
        f"**Verdict (Step 1):** `{verdict}`",
        "",
        "**Diagnostic:** " + (diag.get("summary") or "—"),
        "",
        "| Metric | OFF | ON | Δ (ON − OFF) |",
        "|--------|-----|----|--------------|",
    ]
    keys = (
        "explain_success_rate",
        "symbol_body_present_rate",
        "impl_bias_ok_rate",
        "relation_ok_rate",
        "all_kinds_typed_rate",
        "architecture_task_count",
        "architecture_ok_rate",
        "average_final_context_count",
        "average_final_context_chars",
        "average_distinct_impl_file_count",
        "replanner_trigger_rate",
        "average_implementation_body_present_count",
        "average_linked_row_count",
        "average_symbol_body_count",
        "average_file_header_count",
        "average_region_body_count",
        "average_test_file_row_count",
        "average_impl_file_row_count",
        "average_prune_loss_proxy",
    )
    for k in keys:
        lines.append(
            f"| {k} | {_fmt(agg_off.get(k))} | {_fmt(agg_on.get(k))} | {_fmt(deltas.get(k))} |"
        )
    per_task = comparison.get("per_task_diffs") or []
    arch_diffs, generic_diffs = split_per_task_diffs_by_architecture(per_task)

    def _per_task_table(diffs: list[dict], title: str, cols: str = "ctx impl") -> list[str]:
        out_lines: list[str] = []
        if not diffs:
            return out_lines
        out_lines.extend(["", f"## {title}", ""])
        if "arch_ok" in cols:
            out_lines.append(
                "| task_id | ctx_off | ctx_on | Δ | linked_off | linked_on | impl_off | impl_on | arch_ok_off | arch_ok_on |"
            )
            out_lines.append("|---------|---------|--------|---|------------|-----------|----------|---------|-------------|------------|")
        else:
            out_lines.append(
                "| task_id | ctx_off | ctx_on | Δ | linked_off | linked_on | impl_off | impl_on |"
            )
            out_lines.append("|---------|---------|--------|---|------------|-----------|----------|---------|")
        for d in diffs:
            tid = d.get("task_id", "")
            ctx_off = _fmt(d.get("final_context_count_off"))
            ctx_on = _fmt(d.get("final_context_count_on"))
            ctx_d = _fmt(d.get("final_context_count_delta"))
            ln_off = _fmt(d.get("linked_row_count_off"))
            ln_on = _fmt(d.get("linked_row_count_on"))
            impl_off = _fmt(d.get("impl_file_row_count_off"))
            impl_on = _fmt(d.get("impl_file_row_count_on"))
            row = f"| {tid} | {ctx_off} | {ctx_on} | {ctx_d} | {ln_off} | {ln_on} | {impl_off} | {impl_on} |"
            if "arch_ok" in cols:
                a_off = _fmt(d.get("architecture_ok_off"))
                a_on = _fmt(d.get("architecture_ok_on"))
                row += f" {a_off} | {a_on} |"
            out_lines.append(row)
        return out_lines

    if per_task:
        lines.extend(["", "## Per-task diffs (retrieval_quality)", ""])
        lines.extend(_per_task_table(arch_diffs, "Architecture tasks", "arch_ok"))
        lines.extend(_per_task_table(generic_diffs, "Generic retrieval tasks"))
    lines.extend(
        [
            "",
            f"- OFF run: `{arm_off}`",
            f"- ON run: `{arm_on}`",
            f"- Machine-readable: `{json_path}`",
            "",
        ]
    )
    _write_text(md_path, "\n".join(lines))

    print(
        f"kind_expansion_ab: verdict={verdict} json={json_path} md={md_path}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
