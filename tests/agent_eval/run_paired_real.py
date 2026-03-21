"""
Stage 34/35 — Real paired evaluation with hosted model.

Runs 1 offline baseline + N live_model runs (default 4, range 3-5) using the actual
configured model endpoint. Produces policy-grade gap audit.

Usage:
  python3 -m tests.agent_eval.run_paired_real --output artifacts/agent_eval_runs/stage35_paired
  python3 -m tests.agent_eval.run_paired_real --suite paired4 --live-repeats 3
  python3 -m tests.agent_eval.run_paired_real --suite paired8 --live-repeats 4

Suites: paired4 (4 tasks), paired8 (8 tasks, policy-grade cross-section).
If model endpoint is missing or broken, fails transparently and records that as stage outcome.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _ensure_repo_root_on_path() -> None:
    r = str(REPO_ROOT)
    if r not in sys.path:
        sys.path.insert(0, r)


def _is_endpoint_error(exc: BaseException) -> bool:
    """True if error indicates missing/broken model endpoint."""
    err = str(exc).lower()
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return True
    for s in ("connection", "refused", "timeout", "unreachable", "404", "502", "503"):
        if s in err:
            return True
    return False


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Stage 34/35: Real paired offline + live_model evaluation (policy-grade gap audit)."
    )
    p.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/agent_eval_runs/stage35_paired"),
        help="Output directory for paired run",
    )
    p.add_argument(
        "--suite",
        type=str,
        default="paired8",
        choices=("paired4", "paired8"),
        help="Suite: paired4 (4 tasks), paired8 (8 tasks, policy-grade cross-section)",
    )
    p.add_argument(
        "--live-repeats",
        type=int,
        default=4,
        help="Number of live_model runs (default 4, range 3-5 for policy-grade)",
    )
    p.add_argument(
        "--task",
        type=str,
        default=None,
        help="Run only this task_id",
    )
    return p


def run_paired_real(
    output_arg: Path,
    *,
    suite_name: str = "paired8",
    live_repeats: int = 4,
    task_filter: str | None = None,
) -> tuple[dict, str, int]:
    """
    Run 1 offline + N live_model; produce decision-grade comparison.

    Returns (artifact_dict, markdown_str, exit_code).
    exit_code: 0 success, 1 model unreachable, 2 other error
    """
    _ensure_repo_root_on_path()
    from tests.agent_eval.runner import run_suite
    from tests.agent_eval.paired_comparison import build_multi_live_comparison_artifact
    from tests.agent_eval.suite_loader import load_specs_for_mode

    repo_root = REPO_ROOT
    runs_parent = (repo_root / "artifacts" / "agent_eval_runs").resolve()
    output_arg = Path(output_arg).resolve()
    if not output_arg.is_absolute():
        output_arg = runs_parent / output_arg.name

    output_arg.mkdir(parents=True, exist_ok=True)
    offline_dir = output_arg / "offline"
    live_dirs: list[Path] = []

    # Run offline baseline
    _, _, _ = run_suite(
        suite_name,
        offline_dir,
        repo_root=repo_root,
        execution_mode="offline",
        task_filter=task_filter,
        output_dir=offline_dir,
    )

    # Run live_model N times; first failure with endpoint error -> record and exit
    for i in range(live_repeats):
        live_dir = output_arg / f"live_model_{i + 1}"
        try:
            _, _, _ = run_suite(
                suite_name,
                live_dir,
                repo_root=repo_root,
                execution_mode="live_model",
                task_filter=task_filter,
                output_dir=live_dir,
            )
            live_dirs.append(live_dir)
        except Exception as e:
            if _is_endpoint_error(e) and not live_dirs:
                # No successful live runs; model endpoint missing/broken
                failure_artifact = {
                    "stage": "stage34",
                    "outcome": "model_endpoint_missing_or_broken",
                    "message": str(e)[:500],
                    "offline_run_dir": str(offline_dir),
                    "live_run_dirs": [],
                    "decision_recommendation": "live_too_unstable_to_gate",
                }
                (output_arg / "comparison.json").write_text(
                    json.dumps(failure_artifact, indent=2),
                    encoding="utf-8",
                )
                md = (
                    f"# Stage 34 — Model Endpoint Unavailable\n\n"
                    f"**Outcome:** {str(e)[:300]}\n\n"
                    "Cannot run live_model evaluation. Recorded as stage outcome.\n"
                )
                (output_arg / "comparison.md").write_text(md, encoding="utf-8")
                return failure_artifact, md, 1
            # Partial failure or non-endpoint error: record and re-raise
            failure_artifact = {
                "stage": "stage34",
                "outcome": "live_run_failed",
                "message": str(e)[:500],
                "offline_run_dir": str(offline_dir),
                "live_run_dirs": [str(d) for d in live_dirs],
                "failed_at_run": i + 1,
            }
            (output_arg / "comparison.json").write_text(
                json.dumps(failure_artifact, indent=2),
                encoding="utf-8",
            )
            raise

    if not live_dirs:
        raise RuntimeError("No live runs completed")

    # Build comparison
    specs = load_specs_for_mode(suite_name, "live_model")
    specs_by_id = {s.task_id: s for s in specs}
    artifact, markdown = build_multi_live_comparison_artifact(
        offline_dir, live_dirs, specs_by_id=specs_by_id
    )

    (output_arg / "comparison.json").write_text(
        json.dumps(artifact, indent=2, default=str),
        encoding="utf-8",
    )
    (output_arg / "comparison.md").write_text(markdown, encoding="utf-8")

    return artifact, markdown, 0


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    try:
        artifact, markdown, exit_code = run_paired_real(
            args.output,
            suite_name=args.suite,
            live_repeats=max(3, min(5, args.live_repeats or 4)),
            task_filter=getattr(args, "task", None),
        )
        print(markdown)
        print(f"\nOutput: {args.output}", file=sys.stderr)
        print(f"Gating policy: {artifact.get('gating_policy', 'N/A')}", file=sys.stderr)
        print(f"Policy support: {artifact.get('policy_support', 'N/A')}", file=sys.stderr)
        print(f"Usefulness: {artifact.get('usefulness_judgment', 'N/A')}", file=sys.stderr)
        print(f"Recommendation: {artifact.get('decision_recommendation', 'N/A')}", file=sys.stderr)
        return exit_code
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
