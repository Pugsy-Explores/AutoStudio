"""
CLI runner for Stage 12 benchmarks.

Examples:
  python3 -m tests.agent_eval.runner --suite core12 --output artifacts/agent_eval_runs/latest
  python3 -m tests.agent_eval.runner --real --output artifacts/agent_eval_runs/latest
  python3 -m tests.agent_eval.runner --suite routing_contract --execution-mode live_model
  python3 -m tests.agent_eval.check_routing_contract --run-dir artifacts/agent_eval_runs/latest

  # SEARCH stack (retrieval / policy / repo_map contracts; offline or live_model)
  python3 -m tests.agent_eval.runner --suite search_stack --execution-mode offline --output artifacts/agent_eval_runs/latest
  python3 -m tests.agent_eval.check_search_stack --run-dir artifacts/agent_eval_runs/latest
"""

from __future__ import annotations

# Pre-import numpy before mocks/threads to avoid RecursionError in Python 3.12
# (numpy + nested import loader; see Docs/RCA_AUDIT12_RECURSION_AND_STUBS.md)
import numpy  # noqa: F401

import argparse
import json
import shutil
import sys
import time
import uuid
from collections.abc import Collection
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]


def _ensure_repo_root_on_path() -> None:
    r = str(REPO_ROOT)
    if r not in sys.path:
        sys.path.insert(0, r)


def _copy_fixture_workspace(fixture_src: Path, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(
        fixture_src,
        dest,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", ".git"),
        symlinks=False,
    )


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _markdown_summary(
    suite: str,
    run_dir: Path,
    results: list,
    duration_s: float,
    execution_mode: str,
    summary: dict | None = None,
) -> str:
    lines = [
        f"# Agent eval run: `{suite}`",
        "",
        f"- **Run directory:** `{run_dir}`",
        f"- **Execution mode:** `{execution_mode}`",
        f"- **Duration (wall):** {duration_s:.2f}s",
        f"- **Tasks:** {len(results)}",
        "",
        "| task_id | success | validation_passed | structural_success | failure_bucket | first_failing_stage | attempts | retries | replans |",
        "|---------|---------|-------------------|--------------------|---------------|---------------------|----------|---------|---------|",
    ]
    for r in results:
        fb = (r.extra or {}).get("failure_bucket")
        ffs = (r.extra or {}).get("first_failing_stage")
        at = r.attempts_total if isinstance(r.attempts_total, int) else "-"
        ru = r.retries_used if isinstance(r.retries_used, int) else "-"
        rp = r.replans_used if isinstance(r.replans_used, int) else "-"
        lines.append(
            f"| {r.task_id} | {r.success} | {r.validation_passed} | {r.structural_success} | {fb!s} | {ffs!s} | {at} | {ru} | {rp} |"
        )
    ok = sum(1 for r in results if r.success)
    lines.extend(["", f"**Overall success:** {ok}/{len(results)}", ""])
    if summary:
        lines.extend(
            [
                "## Aggregates",
                f"- attempts_total_aggregate: {summary.get('attempts_total_aggregate', 'N/A')}",
                f"- retries_used_aggregate: {summary.get('retries_used_aggregate', 'N/A')}",
                f"- replans_used_aggregate: {summary.get('replans_used_aggregate', 'N/A')}",
                "",
                "## Integrity (Stage 31)",
                f"- execution_mode: {summary.get('execution_mode', 'N/A')}",
                f"- run_valid_for_live_eval: {summary.get('run_valid_for_live_eval', 'N/A')}",
                f"- invalid_live_model_task_count: {summary.get('invalid_live_model_task_count', 'N/A')}",
                f"- zero_model_call_task_count: {summary.get('zero_model_call_task_count', 'N/A')}",
                f"- offline_stubbed_task_count: {summary.get('offline_stubbed_task_count', 'N/A')}",
                f"- explain_stubbed_task_count: {summary.get('explain_stubbed_task_count', 'N/A')}",
                f"- plan_injection_task_count: {summary.get('plan_injection_task_count', 'N/A')}",
                f"- model_call_count_total: {summary.get('model_call_count_total', 'N/A')}",
                f"- small_model_call_count_total: {summary.get('small_model_call_count_total', 'N/A')}",
                f"- reasoning_model_call_count_total: {summary.get('reasoning_model_call_count_total', 'N/A')}",
                "",
                "## Histograms",
                f"- failure_bucket: {summary.get('failure_bucket_histogram', {})}",
                f"- patch_reject_reason: {summary.get('patch_reject_reason_histogram', {})}",
                f"- validation_scope_kind: {summary.get('validation_scope_kind_histogram', {})}",
                f"- first_failing_stage: {summary.get('first_failing_stage_histogram', {})}",
                "",
            ]
        )
    return "\n".join(lines)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run agent_eval benchmark suites.")
    p.add_argument(
        "--suite",
        default="core12",
        help="Suite name: core12, audit12, audit6, holdout8, adversarial12, external6, live4, paired4, paired8, routing_contract",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/agent_eval_runs/latest"),
        help="Output symlink target directory (default: artifacts/agent_eval_runs/latest)",
    )
    p.add_argument(
        "--execution-mode",
        choices=("mocked", "offline", "live_model", "real"),
        default="mocked",
        help="mocked: stub execution_loop. offline: deterministic+stubs, no real model. "
        "live_model: real model required. real: deprecated, maps to offline.",
    )
    p.add_argument(
        "--real",
        action="store_true",
        help="Deprecated: shortcut for --execution-mode offline. Use --execution-mode live_model for real model.",
    )
    p.add_argument(
        "--task",
        type=str,
        default=None,
        help="Run only this task_id (e.g. core12_pin_click_multifile).",
    )
    p.add_argument(
        "--task-timeout",
        type=int,
        default=None,
        help="Per-task timeout in seconds. Tasks exceeding this are marked as task_timeout failures.",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max number of tasks to run (default: all).",
    )
    return p


def run_suite(
    suite_name: str,
    output_arg: Path,
    *,
    repo_root: Path | None = None,
    execution_mode: str = "mocked",
    task_filter: str | None = None,
    task_id_allowlist: Collection[str] | None = None,
    task_limit: int | None = None,
    output_dir: Path | None = None,
    task_timeout_seconds: int | None = None,
) -> tuple[Path, list, dict[str, Any]]:
    """Execute tasks; return (run_dir, results, summary_dict)."""
    _ensure_repo_root_on_path()
    from tests.agent_eval.harness import run_single_task
    from tests.agent_eval.suite_loader import load_specs_for_mode
    from tests.agent_eval.task_specs import validate_suite

    specs = load_specs_for_mode(suite_name, execution_mode)
    if task_filter:
        specs = [s for s in specs if s.task_id == task_filter]
        if not specs:
            raise SystemExit(f"No task matching --task {task_filter!r}")
    if task_limit is not None and task_limit > 0:
        specs = specs[:task_limit]
    if task_id_allowlist is not None:
        specs = [s for s in specs if s.task_id in task_id_allowlist]
        if not specs:
            raise SystemExit("No tasks left after task_id_allowlist filter")
    validate_suite(specs)

    repo_root = repo_root or REPO_ROOT
    runs_parent = (repo_root / "artifacts" / "agent_eval_runs").resolve()
    if output_dir is not None:
        run_dir = output_dir.resolve()
        run_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
    else:
        ts = time.strftime("%Y%m%d_%H%M%S")
        run_id = uuid.uuid4().hex[:6]
        run_dir = runs_parent / f"{ts}_{run_id}"
        run_dir.mkdir(parents=True, exist_ok=True)

    out_resolved = output_arg.resolve()
    want_latest_link = out_resolved.name == "latest" or str(output_arg).rstrip("/").endswith(
        "agent_eval_runs/latest"
    )
    if want_latest_link:
        latest = runs_parent / "latest"
        if latest.is_symlink():
            latest.unlink()
        elif latest.is_dir():
            shutil.rmtree(latest)
        elif latest.exists():
            latest.unlink()
        try:
            latest.symlink_to(run_dir, target_is_directory=True)
        except OSError:
            pass

    t0 = time.time()
    results = []
    fixtures = repo_root / "tests" / "agent_eval" / "fixtures"

    for spec in specs:
        src = (fixtures / spec.repo_path).resolve()
        ws = run_dir / "workspaces" / spec.task_id
        _copy_fixture_workspace(src, ws)

        trace = f"{suite_name}-{spec.task_id}-{uuid.uuid4().hex[:8]}"
        from tests.agent_eval.execution_mode import resolve_execution_mode

        em = resolve_execution_mode(execution_mode)

        if task_timeout_seconds is not None and task_timeout_seconds > 0:
            from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

            def _run():
                return run_single_task(spec, ws, trace_id=trace, execution_mode=em)

            with ThreadPoolExecutor(max_workers=1) as ex:
                fut = ex.submit(_run)
                try:
                    outcome = fut.result(timeout=task_timeout_seconds)
                except FuturesTimeoutError:
                    from tests.agent_eval.harness import TaskOutcome

                    outcome = TaskOutcome(
                        task_id=spec.task_id,
                        success=False,
                        validation_passed=False,
                        retries_used=None,
                        replans_used=None,
                        attempts_total=None,
                        failure_class="task_timeout",
                        files_changed=[],
                        diff_stat={},
                        unrelated_files_changed=[],
                        bad_edit_patterns=[],
                        retrieval_miss_signals=[],
                        notes=f"Task timed out after {task_timeout_seconds}s",
                        structural_success=False,
                        grading_mode=getattr(spec, "grading_mode", ""),
                        loop_output_snapshot={},
                        validation_logs=[],
                        extra={
                            "failure_bucket": "task_timeout",
                            "first_failing_stage": "run_single_task",
                            "timeout_seconds": task_timeout_seconds,
                            "retrieval_quality_bundle": {"timeout": True, "task_id": spec.task_id},
                        },
                    )
        else:
            outcome = run_single_task(spec, ws, trace_id=trace, execution_mode=em)
        results.append(outcome)

        task_dir = run_dir / "tasks" / spec.task_id
        from tests.agent_eval.task_audit import build_task_audit_dict

        pub = outcome.to_public_dict()
        pub["_audit"] = build_task_audit_dict(outcome, spec)
        rqb = outcome.extra.get("retrieval_quality_bundle")
        if isinstance(rqb, dict) and rqb:
            pub["retrieval_quality"] = rqb
        _write_json(task_dir / "outcome.json", pub)
        _write_json(task_dir / "edit_telemetry.json", outcome.extra.get("edit_telemetry") or {})
        # Stage 22: semantic_rca.json for failed EDIT tasks
        if not outcome.success and spec.grading_mode != "explain_artifact":
            try:
                from tests.agent_eval.semantic_rca import build_semantic_rca_dict

                task_type = "EDIT" if spec.grading_mode in ("validation_exit_code", "structural_loop") else str(spec.grading_mode)
                rca = build_semantic_rca_dict(
                    task_id=outcome.task_id,
                    task_type=task_type,
                    instruction=spec.instruction,
                    success=outcome.success,
                    structural_success=outcome.structural_success,
                    validation_passed=outcome.validation_passed,
                    failure_bucket=outcome.extra.get("failure_bucket"),
                    first_failing_stage=outcome.extra.get("first_failing_stage"),
                    loop_snapshot=outcome.loop_output_snapshot,
                    validation_logs=outcome.validation_logs,
                    edit_telemetry=None,  # Use merge from loop_snapshot (handles hierarchical)
                )
                _write_json(task_dir / "semantic_rca.json", rca)
            except Exception as e:
                _write_json(task_dir / "semantic_rca.json", {"_error": str(e)[:200]})
        _write_json(task_dir / "indexing.json", outcome.extra.get("index") or {})
        _write_json(task_dir / "loop_output_snapshot.json", outcome.loop_output_snapshot)
        _write_text(task_dir / "validation_logs.json", json.dumps(outcome.validation_logs, indent=2))
        lo_txt = json.dumps(outcome.loop_output_snapshot, indent=2, default=str)
        summary_line = (
            f"task_id={outcome.task_id} success={outcome.success} "
            f"validation_passed={outcome.validation_passed} "
            f"failure_bucket={outcome.extra.get('failure_bucket')!s}\n"
        )
        _write_text(task_dir / "transcript.txt", summary_line + "\n" + lo_txt)
        diff_unified = (outcome.extra or {}).get("diff_unified") or ""
        _uses_real_workspace = em in ("offline", "live_model")
        if _uses_real_workspace and diff_unified.strip():
            _write_text(task_dir / "patch.diff", diff_unified)
        else:
            _write_text(
                task_dir / "patch.diff",
                diff_unified
                if diff_unified.strip()
                else "# Mocked structural run — no real edits applied by harness.\n",
            )
        _write_text(task_dir / "changed_files.txt", "\n".join(outcome.files_changed) + ("\n" if outcome.files_changed else ""))
        _write_text(task_dir / "task_summary_snippet.txt", summary_line)

    duration = time.time() - t0
    from tests.agent_eval.summary_aggregation import (
        aggregate_histograms,
        aggregate_integrity_metrics,
        build_per_task_outcomes,
        build_suite_label,
    )

    histograms = aggregate_histograms(results)
    integrity_metrics = aggregate_integrity_metrics(results, execution_mode)

    # Edit failure stage histogram (from retrieval_quality diagnostics)
    rq_records = [
        r.extra.get("retrieval_quality_bundle")
        for r in results
        if r.extra and isinstance(r.extra.get("retrieval_quality_bundle"), dict)
    ]
    edit_failure_stage_hist: dict[str, int] = {}
    if rq_records:
        try:
            from tests.agent_eval.check_retrieval_quality import aggregate_retrieval_metrics

            rq_agg = aggregate_retrieval_metrics(rq_records)
            edit_failure_stage_hist = rq_agg.get("edit_failure_stage_histogram") or {}
        except Exception:
            pass
    suite_label = build_suite_label(suite_name, execution_mode)
    # Stage 22: semantic RCA cause histogram for failed EDIT tasks
    semantic_rca_cause_hist: dict[str, int] = {}
    spec_by_id = {s.task_id: s for s in specs}
    try:
        from tests.agent_eval.semantic_rca import classify_wrong_patch_root_cause

        for r in results:
            spec = spec_by_id.get(r.task_id)
            if not spec or spec.grading_mode == "explain_artifact" or r.success:
                continue
            cause = classify_wrong_patch_root_cause(
                success=r.success,
                structural_success=r.structural_success,
                validation_passed=r.validation_passed,
                failure_bucket=(r.extra or {}).get("failure_bucket"),
                loop_snapshot=r.loop_output_snapshot,
                validation_logs=r.validation_logs or [],
                instruction=spec.instruction,
            )
            semantic_rca_cause_hist[cause] = semantic_rca_cause_hist.get(cause, 0) + 1
    except Exception:
        pass

    # Task-type breakdown (from tags)
    task_type_hist: dict[str, int] = {}
    instruction_explicit_path_count = 0
    for r in results:
        spec = spec_by_id.get(r.task_id)
        if spec:
            tags = getattr(spec, "tags", ()) or ()
            for t in ("repair", "feature", "docs", "explain", "multi_file", "refactor", "consistency"):
                if t in tags:
                    task_type_hist[t] = task_type_hist.get(t, 0) + 1
                    break
            # instruction_has_explicit_path: contains path like x/y/z.py or module/sub
            inst = (spec.instruction or "").lower()
            if any(p in inst for p in (".py", ".md", "/")) and any(c in inst for c in ("in ", " at ", " from ")):
                instruction_explicit_path_count += 1

    summary = {
        "suite": suite_label,
        "run_dir": str(run_dir),
        "timestamp": ts,
        "duration_seconds": duration,
        "execution_mode": execution_mode,
        "total_tasks": len(results),
        "success_count": sum(1 for r in results if r.success),
        "validation_pass_count": sum(1 for r in results if r.validation_passed),
        "structural_success_count": sum(1 for r in results if r.structural_success),
        "task_ids": [r.task_id for r in results],
        **histograms,
        **integrity_metrics,
        "task_type_histogram": task_type_hist,
        "instruction_explicit_path_count": instruction_explicit_path_count,
        "semantic_rca_cause_histogram": semantic_rca_cause_hist,
        "edit_failure_stage_histogram": edit_failure_stage_hist,
    }
    summary["per_task_outcomes"] = build_per_task_outcomes(results)

    _write_json(run_dir / "summary.json", summary)
    _write_text(
        run_dir / "summary.md",
        _markdown_summary(suite_name, run_dir, results, duration, execution_mode, summary),
    )

    return run_dir, results, summary


def main(argv: list[str] | None = None) -> int:
    import warnings

    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.real:
        warnings.warn(
            "--real is deprecated; use --execution-mode offline. "
            "For real model evaluation use --execution-mode live_model --suite live4",
            DeprecationWarning,
            stacklevel=2,
        )
        args.execution_mode = "offline"

    run_dir, results, summary = run_suite(
        args.suite,
        args.output,
        execution_mode=args.execution_mode,
        task_filter=getattr(args, "task", None),
        task_limit=getattr(args, "limit", None),
        task_timeout_seconds=getattr(args, "task_timeout", None),
    )
    print(json.dumps(summary, indent=2))
    print(f"\nRun directory: {run_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
