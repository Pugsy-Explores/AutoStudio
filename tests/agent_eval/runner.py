"""
CLI runner for Stage 12 benchmarks.

Examples:
  python3 -m tests.agent_eval.runner --suite core12 --output artifacts/agent_eval_runs/latest
  python3 -m tests.agent_eval.runner --real --output artifacts/agent_eval_runs/latest
"""

from __future__ import annotations

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
    raise SystemExit(f"unknown suite: {name!r} (try core12)")


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
) -> str:
    lines = [
        f"# Agent eval run: `{suite}`",
        "",
        f"- **Run directory:** `{run_dir}`",
        f"- **Execution mode:** `{execution_mode}`",
        f"- **Duration (wall):** {duration_s:.2f}s",
        f"- **Tasks:** {len(results)}",
        "",
        "| task_id | success | validation_passed | structural_success | failure_class | failure_bucket |",
        "|---------|---------|-------------------|--------------------|--------------|----------------|",
    ]
    for r in results:
        fb = (r.extra or {}).get("failure_bucket")
        lines.append(
            f"| {r.task_id} | {r.success} | {r.validation_passed} | {r.structural_success} | {r.failure_class!s} | {fb!s} |"
        )
    ok = sum(1 for r in results if r.success)
    lines.extend(["", f"**Overall success:** {ok}/{len(results)}", ""])
    return "\n".join(lines)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run agent_eval benchmark suites.")
    p.add_argument("--suite", default="core12", help="Suite name when using mocked mode (default: core12)")
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
        help="mocked: stub execution_loop (default). real: real execution_loop + offline LLM stubs (audit6 only).",
    )
    p.add_argument(
        "--real",
        action="store_true",
        help="Shortcut for --execution-mode real (runs fixed 6-task audit subset).",
    )
    return p


def run_suite(
    suite_name: str,
    output_arg: Path,
    *,
    repo_root: Path | None = None,
    execution_mode: str = "mocked",
) -> tuple[Path, list, dict[str, Any]]:
    """Execute tasks; return (run_dir, results, summary_dict)."""
    _ensure_repo_root_on_path()
    from tests.agent_eval.harness import run_single_task
    from tests.agent_eval.task_specs import validate_suite

    if execution_mode == "real":
        from tests.agent_eval.suites.audit6 import load_audit6_specs

        specs = load_audit6_specs()
    else:
        specs = _load_suite(suite_name)
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
        pub = outcome.to_public_dict()
        pub["_audit"] = {
            "structural_success": outcome.structural_success,
            "grading_mode": outcome.grading_mode,
            "exception": outcome.extra.get("exception"),
            "index": outcome.extra.get("index"),
            "failure_bucket": outcome.extra.get("failure_bucket"),
            "execution_mode": outcome.extra.get("execution_mode"),
            "edit_telemetry": outcome.extra.get("edit_telemetry") or {},
        }
        _write_json(task_dir / "outcome.json", pub)
        _write_json(task_dir / "edit_telemetry.json", outcome.extra.get("edit_telemetry") or {})
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
    patches_applied_total = 0
    files_modified_total = 0
    for r in results:
        b = (r.extra or {}).get("failure_bucket")
        if b:
            bucket_hist[str(b)] = bucket_hist.get(str(b), 0) + 1
        et = (r.extra or {}).get("edit_telemetry") or {}
        if isinstance(et, dict):
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

    summary = {
        "suite": suite_name if execution_mode != "real" else "core12_audit6_real",
        "run_dir": str(run_dir),
        "timestamp": ts,
        "duration_seconds": duration,
        "execution_mode": execution_mode,
        "total_tasks": len(results),
        "success_count": sum(1 for r in results if r.success),
        "validation_pass_count": sum(1 for r in results if r.validation_passed),
        "structural_success_count": sum(1 for r in results if r.structural_success),
        "task_ids": [r.task_id for r in results],
        "failure_bucket_histogram": bucket_hist,
        "patch_reject_reason_histogram": reject_hist,
        "patches_applied_total": patches_applied_total,
        "files_modified_total": files_modified_total,
    }
    _write_json(run_dir / "summary.json", summary)
    _write_text(run_dir / "summary.md", _markdown_summary(suite_name, run_dir, results, duration, execution_mode))

    return run_dir, results, summary


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.real:
        args.execution_mode = "real"

    run_dir, results, summary = run_suite(args.suite, args.output, execution_mode=args.execution_mode)
    print(json.dumps(summary, indent=2))
    print(f"\nRun directory: {run_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
