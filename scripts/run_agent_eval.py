#!/usr/bin/env python3
"""
Stage 12: run the software-agent benchmark corpus and write JSON artifacts.

Preferred entrypoint (core12 suite + timestamped runs):
  python3 -m tests.agent_eval.runner --suite core12 --output artifacts/agent_eval_runs/latest

Legacy harness (tests/evals mini-project corpus, mocked loop):
  python3 scripts/run_agent_eval.py --legacy

Requires no network: execution_loop is mocked inside the harness.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Stage 12 agent benchmark.")
    parser.add_argument(
        "--legacy",
        action="store_true",
        help="Run tests/evals harness (12 tasks on sample_app) instead of tests/agent_eval/core12.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="[legacy] Directory for this run (default: artifacts/agent_eval/run_<timestamp>_<id>)",
    )
    args = parser.parse_args()

    if args.legacy:
        from tests.evals.agent_eval_harness import run_full_benchmark

        _results, summary, written = run_full_benchmark(run_dir=args.output_dir, repo_root=ROOT)
        print(json.dumps(asdict(summary), indent=2, default=str))
        print(f"Artifacts written to: {written}", file=sys.stderr)
        return 0

    from tests.agent_eval.runner import run_suite

    out = Path("artifacts/agent_eval_runs/latest")
    _run_dir, _results, summary = run_suite("core12", out, repo_root=ROOT)
    print(json.dumps(summary, indent=2, default=str))
    print(f"Run directory: {summary.get('run_dir')}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
