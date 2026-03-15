"""Run single prompt against full dataset, return aggregate PromptBenchmarkResult."""

from collections.abc import Callable
from dataclasses import dataclass

from agent.prompt_eval.prompt_dataset_loader import load_dataset
from agent.prompt_eval.prompt_score import PromptScore, compute_score


@dataclass
class PromptBenchmarkResult:
    """Aggregate benchmark result for a prompt."""

    prompt_name: str
    version: str
    num_cases: int
    avg_action_accuracy: float
    avg_json_validity: float
    avg_tool_correctness: float
    avg_task_success: float
    scores: list[PromptScore]


def run_benchmark(
    prompt_name: str,
    version: str,
    run_fn: Callable[[str], str],
    dataset_path: str | None = None,
) -> PromptBenchmarkResult:
    """
    Run prompt against dataset.
    run_fn(task: str) -> str  # takes task, returns model response
    """
    cases = load_dataset(dataset_path)
    scores: list[PromptScore] = []

    for case in cases:
        task = case.get("task", "")
        expected = case.get("expected_actions", [])
        case_id = case.get("id", "unknown")
        try:
            response = run_fn(task)
        except Exception:
            response = ""
        scores.append(compute_score(case_id, response, expected))

    n = len(scores)
    if n == 0:
        return PromptBenchmarkResult(
            prompt_name=prompt_name,
            version=version,
            num_cases=0,
            avg_action_accuracy=0.0,
            avg_json_validity=0.0,
            avg_tool_correctness=0.0,
            avg_task_success=0.0,
            scores=[],
        )

    return PromptBenchmarkResult(
        prompt_name=prompt_name,
        version=version,
        num_cases=n,
        avg_action_accuracy=sum(s.action_accuracy for s in scores) / n,
        avg_json_validity=sum(s.json_validity for s in scores) / n,
        avg_tool_correctness=sum(s.tool_correctness for s in scores) / n,
        avg_task_success=sum(s.task_success for s in scores) / n,
        scores=scores,
    )
