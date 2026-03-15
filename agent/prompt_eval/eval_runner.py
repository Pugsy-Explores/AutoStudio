"""Orchestrate benchmark, write results to dev/prompt_eval_results/."""

import json
from collections.abc import Callable
from pathlib import Path

from agent.prompt_eval.prompt_benchmark import PromptBenchmarkResult, run_benchmark

_RESULTS_DIR = Path(__file__).resolve().parent.parent.parent / "dev" / "prompt_eval_results"


def _result_to_dict(r: PromptBenchmarkResult) -> dict:
    return {
        "prompt_name": r.prompt_name,
        "version": r.version,
        "num_cases": r.num_cases,
        "avg_action_accuracy": r.avg_action_accuracy,
        "avg_json_validity": r.avg_json_validity,
        "avg_tool_correctness": r.avg_tool_correctness,
        "avg_task_success": r.avg_task_success,
        "scores": [
            {
                "case_id": s.case_id,
                "action_accuracy": s.action_accuracy,
                "json_validity": s.json_validity,
                "tool_correctness": s.tool_correctness,
                "task_success": s.task_success,
            }
            for s in r.scores
        ],
    }


def run_eval(
    prompt_name: str,
    version: str,
    run_fn: Callable[[str], str],
    dataset_path: str | None = None,
    output_file: str | None = None,
) -> PromptBenchmarkResult:
    """
    Run evaluation and write results to dev/prompt_eval_results/.
    Returns PromptBenchmarkResult.
    """
    result = run_benchmark(prompt_name, version, run_fn, dataset_path)

    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = _RESULTS_DIR / (output_file or f"{prompt_name}_{version}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(_result_to_dict(result), f, indent=2)

    return result
