"""Prompt A/B testing scaffold. Run two versions against the same dataset; pick the winner."""

from collections.abc import Callable
from dataclasses import dataclass

from agent.prompt_eval.prompt_benchmark import run_benchmark
from agent.prompt_eval.prompt_dataset_loader import load_dataset


@dataclass
class ABTestResult:
    """Result of A/B test between two prompt versions."""

    prompt_name: str
    variant_a: str
    variant_b: str
    winner: str | None
    a_task_success: float
    b_task_success: float


def run_ab_test(
    prompt_name: str,
    variant_a: str,
    variant_b: str,
    run_fn: Callable[[str], str],
    dataset_path: str | None = None,
) -> ABTestResult:
    """
    Run two versions against the same dataset; pick the winner.

    run_fn(task: str) -> str must produce model output for the given task.
    For variant-specific execution, pass a factory that returns (task)->response
    bound to each variant, e.g. run_fn=lambda t: run_planner(t, version=variant).

    This scaffold wires into eval_runner and run_prompt_ci. The actual
    automated optimization loop is Phase 14.
    """
    cases = load_dataset(dataset_path)
    if not cases:
        return ABTestResult(
            prompt_name=prompt_name,
            variant_a=variant_a,
            variant_b=variant_b,
            winner=None,
            a_task_success=0.0,
            b_task_success=0.0,
        )

    # Run variant_a and variant_b with same run_fn (caller binds variant externally)
    # Phase 14: add run_fn_factory(variant) -> Callable[[str], str] for proper A/B
    result_a = run_benchmark(prompt_name, variant_a, run_fn, dataset_path)
    result_b = run_benchmark(prompt_name, variant_b, run_fn, dataset_path)

    a_success = result_a.avg_task_success
    b_success = result_b.avg_task_success
    winner = variant_a if a_success > b_success else (variant_b if b_success > a_success else None)

    return ABTestResult(
        prompt_name=prompt_name,
        variant_a=variant_a,
        variant_b=variant_b,
        winner=winner,
        a_task_success=a_success,
        b_task_success=b_success,
    )
