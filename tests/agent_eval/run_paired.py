"""
Stage 33 — Paired offline/live_model evaluation runner.

Runs the same task set in both modes and produces a comparison artifact.

Usage:
  python3 -m tests.agent_eval.run_paired --output artifacts/agent_eval_runs/paired_latest
  python3 -m tests.agent_eval.run_paired --output artifacts/agent_eval_runs/paired_latest --task core12_mini_repair_calc
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]


def _ensure_repo_root_on_path() -> None:
    r = str(REPO_ROOT)
    if r not in sys.path:
        sys.path.insert(0, r)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Run paired offline + live_model evaluation (Stage 33 gap audit)."
    )
    p.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/agent_eval_runs/paired_latest"),
        help="Output directory for paired run (offline/ and live_model/ subdirs)",
    )
    p.add_argument(
        "--task",
        type=str,
        default=None,
        help="Run only this task_id (e.g. core12_mini_repair_calc)",
    )
    return p


def run_paired(
    output_arg: Path,
    *,
    task_filter: str | None = None,
    mock_live_model: bool = False,
) -> tuple[Path, Path, dict, str]:
    """
    Run paired4 suite in offline and live_model modes; produce comparison.

    If mock_live_model=True, patch _call_chat for live run (for tests).
    Returns (offline_run_dir, live_run_dir, comparison_json, comparison_md).
    """
    _ensure_repo_root_on_path()
    from tests.agent_eval.runner import run_suite
    from tests.agent_eval.paired_comparison import build_comparison_artifact

    repo_root = REPO_ROOT
    runs_parent = (repo_root / "artifacts" / "agent_eval_runs").resolve()
    output_arg = output_arg.resolve()
    if not output_arg.is_absolute():
        output_arg = runs_parent / output_arg.name

    output_arg.mkdir(parents=True, exist_ok=True)
    offline_dir = output_arg / "offline"
    live_dir = output_arg / "live_model"

    # Run offline
    _, _, _ = run_suite(
        "paired4",
        offline_dir,
        repo_root=repo_root,
        execution_mode="offline",
        task_filter=task_filter,
        output_dir=offline_dir,
    )

    # Run live_model (optionally mocked)
    def _run_live():
        return run_suite(
            "paired4",
            live_dir,
            repo_root=repo_root,
            execution_mode="live_model",
            task_filter=task_filter,
            output_dir=live_dir,
        )

    if mock_live_model:
        with patch(
            "agent.models.model_client._call_chat",
            return_value='{"steps":[{"id":1,"action":"SEARCH","description":"x","reason":"y"}]}',
        ):
            _, _, _ = _run_live()
    else:
        _, _, _ = _run_live()

    artifact, markdown = build_comparison_artifact(offline_dir, live_dir)

    (output_arg / "comparison.json").write_text(
        json.dumps(artifact, indent=2, default=str),
        encoding="utf-8",
    )
    (output_arg / "comparison.md").write_text(markdown, encoding="utf-8")

    return offline_dir, live_dir, artifact, markdown


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    try:
        offline_dir, live_dir, artifact, markdown = run_paired(
            args.output,
            task_filter=getattr(args, "task", None),
        )
        print(markdown)
        print(f"\nOffline run: {offline_dir}", file=sys.stderr)
        print(f"Live run: {live_dir}", file=sys.stderr)
        print(f"Comparison: {args.output / 'comparison.json'}", file=sys.stderr)
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
