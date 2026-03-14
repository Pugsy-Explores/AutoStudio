"""
Planner evaluation: structural validation, action coverage, dependency order, plan length, latency.
Run with: python -m planner.planner_eval
"""

import json
import time
from pathlib import Path

from planner.planner import plan
from planner.planner_prompts import PLANNER_SYSTEM_PROMPT
from planner.planner_utils import ALLOWED_ACTIONS

# Default dataset next to this module
DEFAULT_DATASET_PATH = Path(__file__).parent / "planner_dataset.json"

_ALLOWED_SET = set(ALLOWED_ACTIONS)


def _normalize_action(raw: str | None) -> str:
    """Normalize action string to one of ALLOWED_ACTIONS; unknown becomes EXPLAIN."""
    if raw is None:
        return "EXPLAIN"
    normalized = str(raw).strip().upper()
    return normalized if normalized in _ALLOWED_SET else "EXPLAIN"


def validate_structure(plan_dict: dict) -> bool:
    """
    Check that planner output is structurally valid.
    Plan must have "steps" (list); each step must have id, action, description, reason;
    action must be one of EDIT, SEARCH, EXPLAIN, INFRA.
    """
    if not isinstance(plan_dict, dict):
        return False
    steps = plan_dict.get("steps")
    if not isinstance(steps, list):
        return False
    required_keys = ("id", "action", "description", "reason")
    for step in steps:
        if not isinstance(step, dict):
            return False
        for key in required_keys:
            if key not in step:
                return False
        action = step.get("action")
        if action not in _ALLOWED_SET:
            return False
    return True


def extract_actions(plan_or_steps: dict | list) -> list[str]:
    """
    Extract ordered list of action strings from a plan dict (with "steps") or list of step dicts.
    Actions are normalized to ALLOWED_ACTIONS for consistent comparison.
    """
    if isinstance(plan_or_steps, dict):
        steps = plan_or_steps.get("steps", [])
    elif isinstance(plan_or_steps, list):
        steps = plan_or_steps
    else:
        return []
    if not isinstance(steps, list):
        return []
    out = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        action = _normalize_action(step.get("action"))
        out.append(action)
    return out


def check_action_coverage(expected: list[str], predicted: list[str]) -> bool:
    """True iff every required action appears in the predicted plan (set inclusion)."""
    return set(expected) <= set(predicted)


def is_subsequence(expected: list[str], predicted: list[str]) -> bool:
    """True iff expected is a subsequence of predicted (order preserved; duplicates in predicted allowed)."""
    if not expected:
        return True
    j = 0
    for p in predicted:
        if j < len(expected) and p == expected[j]:
            j += 1
        if j == len(expected):
            return True
    return False


def evaluate_datapoint(
    expected_steps: list[dict],
    predicted_plan: dict,
) -> dict:
    """
    Evaluate a single datapoint: structural validity, action coverage, dependency order.
    Returns dict with structural_valid, action_coverage, order_correct; optional expected_actions, predicted_actions.
    """
    structural_valid = validate_structure(predicted_plan)
    expected_actions = extract_actions(expected_steps)
    predicted_actions = extract_actions(predicted_plan)
    action_coverage = check_action_coverage(expected_actions, predicted_actions)
    order_correct = is_subsequence(expected_actions, predicted_actions)
    return {
        "structural_valid": structural_valid,
        "action_coverage": action_coverage,
        "order_correct": order_correct,
        "expected_actions": expected_actions,
        "predicted_actions": predicted_actions,
    }


def load_dataset(path: Path | str | None = None) -> list[dict]:
    """Load planner dataset: list of {instruction, expected_steps}."""
    p = Path(path) if path is not None else DEFAULT_DATASET_PATH
    data = p.read_text(encoding="utf-8")
    items = json.loads(data)
    out = []
    for item in items:
        if isinstance(item, dict) and "instruction" in item and "expected_steps" in item:
            out.append({
                "instruction": item["instruction"],
                "expected_steps": item["expected_steps"],
            })
    return out


def run_eval(
    dataset_path: Path | str | None = None,
    verbose: bool = True,
) -> dict:
    """
    Run planner evaluation. Returns metrics dict with:
    - structural_valid_rate, action_coverage_accuracy, dependency_order_accuracy
    - average_plan_length, step_count_mae, mean_latency_sec, p95_latency_sec
    """
    data = load_dataset(dataset_path)
    total = len(data)
    structural_valid_count = 0
    action_coverage_count = 0
    order_correct_count = 0
    plan_lengths: list[int] = []
    expected_lengths: list[int] = []
    latencies: list[float] = []

    if verbose:
        print("=" * 80)
        print("PLANNER SYSTEM PROMPT")
        print("=" * 80)
        print(PLANNER_SYSTEM_PROMPT.strip())
        print()

    for i, item in enumerate(data):
        instruction = item["instruction"]
        expected_steps = item["expected_steps"]
        expected_lengths.append(len(expected_steps))

        t0 = time.perf_counter()
        result = plan(instruction)
        latencies.append(time.perf_counter() - t0)

        pred_steps = result.get("steps", [])
        plan_lengths.append(len(pred_steps))

        eval_result = evaluate_datapoint(expected_steps, result)
        if eval_result["structural_valid"]:
            structural_valid_count += 1
        if eval_result["action_coverage"]:
            action_coverage_count += 1
        if eval_result["order_correct"]:
            order_correct_count += 1

        if verbose:
            elapsed = latencies[-1]
            s = "ok" if eval_result["structural_valid"] else "fail"
            c = "ok" if eval_result["action_coverage"] else "fail"
            o = "ok" if eval_result["order_correct"] else "fail"
            print("=" * 80)
            print(f"DATAPOINT {i + 1}/{total}  [structure: {s} / coverage: {c} / order: {o}]  time: {elapsed:.3f}s")
            print("=" * 80)
            print("Datapoint (full):")
            print(json.dumps({"instruction": instruction, "expected_steps": expected_steps}, indent=2))
            print()
            print("Output (full):")
            print(json.dumps(result, indent=2))
            print()

    # Aggregate metrics
    structural_valid_rate = structural_valid_count / total if total else 0.0
    action_coverage_accuracy = action_coverage_count / total if total else 0.0
    dependency_order_accuracy = order_correct_count / total if total else 0.0
    average_plan_length = sum(plan_lengths) / len(plan_lengths) if plan_lengths else 0.0
    step_count_mae = (
        sum(abs(a - b) for a, b in zip(plan_lengths, expected_lengths)) / total
        if total else 0.0
    )
    mean_latency = sum(latencies) / len(latencies) if latencies else 0.0
    latencies_sorted = sorted(latencies)
    p95_idx = int(0.95 * len(latencies_sorted)) if latencies_sorted else 0
    p95_latency = latencies_sorted[p95_idx] if latencies_sorted else 0.0

    metrics = {
        "structural_valid_rate": structural_valid_rate,
        "action_coverage_accuracy": action_coverage_accuracy,
        "dependency_order_accuracy": dependency_order_accuracy,
        "average_plan_length": average_plan_length,
        "step_count_mae": step_count_mae,
        "mean_latency_sec": mean_latency,
        "p95_latency_sec": p95_latency,
        "total": total,
    }

    if verbose:
        print()
        print("Structural validity: {:.0%}".format(structural_valid_rate))
        print("Action coverage accuracy: {:.0%}".format(action_coverage_accuracy))
        print("Dependency order accuracy: {:.0%}".format(dependency_order_accuracy))
        print("Average plan length: {:.1f}".format(average_plan_length))
        print("Step count MAE: {:.1f}".format(step_count_mae))
        print("Mean latency: {:.1f}s".format(mean_latency))
        print("P95 latency: {:.1f}s".format(p95_latency))

    return metrics


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run planner evaluation.")
    parser.add_argument(
        "--dataset-path",
        type=str,
        default=None,
        help="Path to planner_dataset.json (default: package default).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print final metrics.",
    )
    args = parser.parse_args()
    run_eval(dataset_path=args.dataset_path, verbose=not args.quiet)
