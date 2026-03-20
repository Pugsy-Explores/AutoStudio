"""
CLI runner for Stage 12 benchmarks.

Examples:
  python3 -m tests.agent_eval.runner --suite core12 --output artifacts/agent_eval_runs/latest
  python3 -m tests.agent_eval.runner --real --output artifacts/agent_eval_runs/latest
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
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]


def _ensure_repo_root_on_path() -> None:
    r = str(REPO_ROOT)
    if r not in sys.path:
        sys.path.insert(0, r)


def _load_suite(name: str):
    if name == "core12":
        from tests.agent_eval.suites.core12 import load_core12

        return load_core12()
    if name == "audit12":
        from tests.agent_eval.suites.audit12 import load_audit12_specs

        return load_audit12_specs()
    if name == "holdout8":
        from tests.agent_eval.suites.holdout8 import load_holdout8_specs

        return load_holdout8_specs()
    if name == "adversarial12":
        from tests.agent_eval.suites.adversarial12 import load_adversarial12_specs

        return load_adversarial12_specs()
    if name == "external6":
        from tests.agent_eval.suites.external6 import load_external6_specs

        return load_external6_specs()
    raise SystemExit(f"unknown suite: {name!r} (try core12, audit12, holdout8, adversarial12, external6)")


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
        help="Suite name: core12, audit12, holdout8, adversarial12, external6",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/agent_eval_runs/latest"),
        help="Output symlink target directory (default: artifacts/agent_eval_runs/latest)",
    )
    p.add_argument(
        "--execution-mode",
        choices=("mocked", "real"),
        default="mocked",
        help="mocked: stub execution_loop (default). real: real execution_loop (audit6 or audit12 per --suite).",
    )
    p.add_argument(
        "--real",
        action="store_true",
        help="Shortcut for --execution-mode real (runs fixed 6-task audit subset).",
    )
    p.add_argument(
        "--task",
        type=str,
        default=None,
        help="Run only this task_id (e.g. core12_pin_click_multifile).",
    )
    return p


def run_suite(
    suite_name: str,
    output_arg: Path,
    *,
    repo_root: Path | None = None,
    execution_mode: str = "mocked",
    task_filter: str | None = None,
) -> tuple[Path, list, dict[str, Any]]:
    """Execute tasks; return (run_dir, results, summary_dict)."""
    _ensure_repo_root_on_path()
    from tests.agent_eval.harness import run_single_task
    from tests.agent_eval.task_specs import validate_suite

    if execution_mode == "real":
        if suite_name == "audit12":
            from tests.agent_eval.suites.audit12 import load_audit12_specs

            specs = load_audit12_specs()
        elif suite_name == "holdout8":
            from tests.agent_eval.suites.holdout8 import load_holdout8_specs

            specs = load_holdout8_specs()
        elif suite_name == "adversarial12":
            from tests.agent_eval.suites.adversarial12 import load_adversarial12_specs

            specs = load_adversarial12_specs()
        elif suite_name == "external6":
            from tests.agent_eval.suites.external6 import load_external6_specs

            specs = load_external6_specs()
        else:
            from tests.agent_eval.suites.audit6 import load_audit6_specs

            specs = load_audit6_specs()
    else:
        specs = _load_suite(suite_name)
    if task_filter:
        specs = [s for s in specs if s.task_id == task_filter]
        if not specs:
            raise SystemExit(f"No task matching --task {task_filter!r}")
    validate_suite(specs)

    repo_root = repo_root or REPO_ROOT
    ts = time.strftime("%Y%m%d_%H%M%S")
    run_id = uuid.uuid4().hex[:6]
    runs_parent = (repo_root / "artifacts" / "agent_eval_runs").resolve()
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
        em = "real" if execution_mode == "real" else "mocked"
        outcome = run_single_task(spec, ws, trace_id=trace, execution_mode=em)
        results.append(outcome)

        task_dir = run_dir / "tasks" / spec.task_id
        et = outcome.extra.get("edit_telemetry") or {}
        val_cmd = None
        if outcome.validation_logs:
            val_cmd = outcome.validation_logs[0].get("command")
        retrieval_fallback = []
        if isinstance(et, dict):
            if et.get("reranker_failed"):
                retrieval_fallback.append("reranker_failed")
            if et.get("reranker_failed_fallback_used"):
                retrieval_fallback.append("reranker_fallback_used")
            if et.get("bm25_available") is False and et.get("reranker_failed"):
                retrieval_fallback.append("bm25_unavailable")
        pub = outcome.to_public_dict()
        retrieval_telemetry = None
        for pr in (outcome.loop_output_snapshot.get("phase_results") or []):
            if isinstance(pr, dict):
                co = pr.get("context_output") or {}
                if isinstance(co, dict) and "retrieval_telemetry" in co:
                    retrieval_telemetry = co.get("retrieval_telemetry")
                    break
        pub["_audit"] = {
            "structural_success": outcome.structural_success,
            "grading_mode": outcome.grading_mode,
            "exception": outcome.extra.get("exception"),
            "index": outcome.extra.get("index"),
            "failure_bucket": outcome.extra.get("failure_bucket"),
            "first_failing_stage": outcome.extra.get("first_failing_stage"),
            "execution_mode": outcome.extra.get("execution_mode"),
            "retrieval_telemetry": retrieval_telemetry,
            "edit_telemetry": et,
            "patch_reject_reason": et.get("patch_reject_reason") if isinstance(et, dict) else None,
            "validation_scope_kind": et.get("validation_scope_kind") if isinstance(et, dict) else None,
            "changed_files_count": et.get("changed_files_count") if isinstance(et, dict) else None,
            "patches_applied": et.get("patches_applied") if isinstance(et, dict) else None,
            "files_modified": outcome.files_changed,
            "retrieval_fallback_flags": retrieval_fallback,
            "target_selection_telemetry": {
                "attempted_target_files": et.get("attempted_target_files") if isinstance(et, dict) else None,
                "chosen_target_file": et.get("chosen_target_file") if isinstance(et, dict) else None,
                "search_viable_file_hits": et.get("search_viable_file_hits") if isinstance(et, dict) else None,
            }
            if isinstance(et, dict)
            else None,
            "selected_validation_command": val_cmd,
        }
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
        if execution_mode == "real" and diff_unified.strip():
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
    bucket_hist: dict[str, int] = {}
    reject_hist: dict[str, int] = {}
    validation_scope_hist: dict[str, int] = {}
    first_failing_stage_hist: dict[str, int] = {}
    patches_applied_total = 0
    files_modified_total = 0
    attempts_agg = 0
    retries_agg = 0
    replans_agg = 0
    for r in results:
        b = (r.extra or {}).get("failure_bucket")
        if b:
            bucket_hist[str(b)] = bucket_hist.get(str(b), 0) + 1
        ffs = (r.extra or {}).get("first_failing_stage")
        if ffs:
            first_failing_stage_hist[str(ffs)] = first_failing_stage_hist.get(str(ffs), 0) + 1
        et = (r.extra or {}).get("edit_telemetry") or {}
        if isinstance(et, dict):
            vsk = et.get("validation_scope_kind")
            if vsk:
                validation_scope_hist[str(vsk)] = validation_scope_hist.get(str(vsk), 0) + 1
            pr = et.get("patch_reject_reason")
            if pr:
                reject_hist[str(pr)] = reject_hist.get(str(pr), 0) + 1
            try:
                patches_applied_total += int(et.get("patches_applied") or 0)
            except (TypeError, ValueError):
                pass
            try:
                files_modified_total += int(et.get("changed_files_count") or 0)
            except (TypeError, ValueError):
                pass
        if isinstance(r.attempts_total, int):
            attempts_agg += r.attempts_total
        if isinstance(r.retries_used, int):
            retries_agg += r.retries_used
        if isinstance(r.replans_used, int):
            replans_agg += r.replans_used

    suite_label = (
        f"audit12_real"
        if (execution_mode == "real" and suite_name == "audit12")
        else (
            f"holdout8_real"
            if (execution_mode == "real" and suite_name == "holdout8")
            else (
                f"adversarial12_real"
                if (execution_mode == "real" and suite_name == "adversarial12")
                else (
                    f"external6_real"
                    if (execution_mode == "real" and suite_name == "external6")
                    else ("core12_audit6_real" if execution_mode == "real" else suite_name)
                )
            )
        )
    )
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
        "attempts_total_aggregate": attempts_agg,
        "retries_used_aggregate": retries_agg,
        "replans_used_aggregate": replans_agg,
        "task_ids": [r.task_id for r in results],
        "failure_bucket_histogram": bucket_hist,
        "patch_reject_reason_histogram": reject_hist,
        "validation_scope_kind_histogram": validation_scope_hist,
        "first_failing_stage_histogram": first_failing_stage_hist,
        "patches_applied_total": patches_applied_total,
        "files_modified_total": files_modified_total,
        "task_type_histogram": task_type_hist,
        "instruction_explicit_path_count": instruction_explicit_path_count,
        "semantic_rca_cause_histogram": semantic_rca_cause_hist,
    }
    per_task = []
    for r in results:
        et = (r.extra or {}).get("edit_telemetry") or {}
        pt = {
            "task_id": r.task_id,
            "success": r.success,
            "validation_passed": r.validation_passed,
            "structural_success": r.structural_success,
            "attempts_total": r.attempts_total,
            "retries_used": r.retries_used,
            "replans_used": r.replans_used,
            "failure_bucket": (r.extra or {}).get("failure_bucket"),
            "first_failing_stage": (r.extra or {}).get("first_failing_stage"),
            "files_modified": r.files_changed,
        }
        if isinstance(et, dict):
            pt["patch_reject_reason"] = et.get("patch_reject_reason")
            pt["validation_scope_kind"] = et.get("validation_scope_kind")
            pt["changed_files_count"] = et.get("changed_files_count")
            pt["patches_applied"] = et.get("patches_applied")
        per_task.append(pt)
    summary["per_task_outcomes"] = per_task

    _write_json(run_dir / "summary.json", summary)
    _write_text(
        run_dir / "summary.md",
        _markdown_summary(suite_name, run_dir, results, duration, execution_mode, summary),
    )

    return run_dir, results, summary


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.real:
        args.execution_mode = "real"

    run_dir, results, summary = run_suite(
        args.suite,
        args.output,
        execution_mode=args.execution_mode,
        task_filter=getattr(args, "task", None),
    )
    print(json.dumps(summary, indent=2))
    print(f"\nRun directory: {run_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
