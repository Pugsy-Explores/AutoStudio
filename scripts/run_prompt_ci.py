#!/usr/bin/env python3
"""
Prompt CI: run evaluation, compare with baseline, exit(1) on regression.

Usage:
  python scripts/run_prompt_ci.py [--prompt NAME] [--dataset PATH]

Pipeline:
  - Load all prompt versions (or specified prompt)
  - Run eval_runner against prompt_eval_dataset.json
  - Compare metrics with dev/prompt_eval_results/baseline.json
  - exit(1) if: task_success drops >5%, json_validity drops >2%, tool_misuse increases >3%
"""

import argparse
import json
import sys
from pathlib import Path

# Add project root to path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from agent.models.model_client import call_reasoning_model
from agent.prompt_eval.eval_runner import run_eval
from agent.prompt_eval.prompt_dataset_loader import load_dataset
from agent.prompt_system import get_registry

RESULTS_DIR = _PROJECT_ROOT / "dev" / "prompt_eval_results"
BASELINE_PATH = RESULTS_DIR / "baseline.json"

# Regression thresholds
TASK_SUCCESS_DROP_THRESHOLD = 0.05
JSON_VALIDITY_DROP_THRESHOLD = 0.02
TOOL_MISUSE_INCREASE_THRESHOLD = 0.03


def _run_planner(task: str) -> str:
    """Run planner prompt for a task. Returns model response."""
    registry = get_registry()
    prompt = registry.get_instructions("planner")
    return call_reasoning_model(
        task,
        system_prompt=prompt,
        max_tokens=1024,
        task_name="planner",
    )


def _load_baseline() -> dict | None:
    if not BASELINE_PATH.exists():
        return None
    with open(BASELINE_PATH, encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run prompt CI")
    parser.add_argument("--prompt", default="planner", help="Prompt name to evaluate")
    parser.add_argument("--dataset", default=None, help="Path to dataset JSON")
    parser.add_argument("--save-baseline", action="store_true", help="Save current run as baseline")
    args = parser.parse_args()

    dataset_path = args.dataset or str(_PROJECT_ROOT / "tests" / "prompt_eval_dataset.json")
    cases = load_dataset(dataset_path)
    if not cases:
        print("[prompt_ci] No test cases in dataset")
        return 1

    _RUN_FNS = {
        "planner": _run_planner,
        # future: "critic": _run_critic, etc.
    }
    run_fn = _RUN_FNS.get(args.prompt, _run_planner)
    result = run_eval(
        prompt_name=args.prompt,
        version="v1",
        run_fn=run_fn,
        dataset_path=dataset_path,
        output_file=f"{args.prompt}_v1.json",
    )

    print(f"[prompt_ci] {result.prompt_name} v{result.version}")
    print(f"  task_success: {result.avg_task_success:.2%}")
    print(f"  json_validity: {result.avg_json_validity:.2%}")
    print(f"  tool_correctness: {result.avg_tool_correctness:.2%}")

    if args.save_baseline:
        baseline_path = RESULTS_DIR / "baseline.json"
        with open(baseline_path, "w", encoding="utf-8") as f:
            json.dump({
                "prompt_name": result.prompt_name,
                "version": result.version,
                "avg_task_success": result.avg_task_success,
                "avg_json_validity": result.avg_json_validity,
                "avg_tool_correctness": result.avg_tool_correctness,
            }, f, indent=2)
        print(f"[prompt_ci] Baseline saved to {baseline_path}")
        return 0

    baseline = _load_baseline()
    if not baseline:
        print("[prompt_ci] No baseline found; run with --save-baseline first")
        return 0

    regressions = []
    task_success_baseline = baseline.get("avg_task_success", 0)
    if result.avg_task_success < task_success_baseline - TASK_SUCCESS_DROP_THRESHOLD:
        regressions.append(
            f"task_success dropped {task_success_baseline - result.avg_task_success:.2%} "
            f"(threshold {TASK_SUCCESS_DROP_THRESHOLD:.2%})"
        )

    json_validity_baseline = baseline.get("avg_json_validity", 0)
    if result.avg_json_validity < json_validity_baseline - JSON_VALIDITY_DROP_THRESHOLD:
        regressions.append(
            f"json_validity dropped {json_validity_baseline - result.avg_json_validity:.2%} "
            f"(threshold {JSON_VALIDITY_DROP_THRESHOLD:.2%})"
        )

    tool_misuse_baseline = 1.0 - baseline.get("avg_tool_correctness", 1.0)
    tool_misuse_current = 1.0 - result.avg_tool_correctness
    if tool_misuse_current > tool_misuse_baseline + TOOL_MISUSE_INCREASE_THRESHOLD:
        regressions.append(
            f"tool_misuse increased {tool_misuse_current - tool_misuse_baseline:.2%} "
            f"(threshold {TOOL_MISUSE_INCREASE_THRESHOLD:.2%})"
        )

    if regressions:
        print("[prompt_ci] REGRESSION DETECTED:")
        for r in regressions:
            print(f"  - {r}")
        return 1

    print("[prompt_ci] No regression")
    return 0


if __name__ == "__main__":
    sys.exit(main())
