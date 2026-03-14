"""
Router evaluation harness. Swap router by changing the import below.
Run with --mock to use a stub router (no LLM server required).
"""

import argparse
import time
from collections import defaultdict

from router_eval.dataset import CATEGORIES, load_dataset


# --- Swap router here ---
from router_eval.routers.baseline_router import route, ROUTER_NAME
# from router_eval.routers.fewshot_router import route, ROUTER_NAME
# from router_eval.routers.ensemble_router import route, ROUTER_NAME
# from router_eval.routers.confidence_router import route, ROUTER_NAME
# from router_eval.routers.dual_router import route, ROUTER_NAME
# from router_eval.routers.critic_router import route, ROUTER_NAME
# from router_eval.routers.final_router import route, ROUTER_NAME

def _extract_category(result) -> str:
    """Support both route() -> str and route() -> dict with 'category' key."""
    if isinstance(result, dict):
        return result.get("category", result.get("primary", "GENERAL"))
    return str(result)


def _calibration_buckets(confidences: list[float], correct_flags: list[bool]) -> dict:
    """Compute per-bucket accuracy for calibration (bucket -> (avg_conf, accuracy, count))."""
    if not confidences or len(confidences) != len(correct_flags):
        return {}
    buckets = defaultdict(lambda: {"conf_sum": 0.0, "correct": 0, "n": 0})
    for c, ok in zip(confidences, correct_flags):
        bucket = min(int(c * 5), 4)  # 0-0.2, 0.2-0.4, ..., 0.8-1.0
        buckets[bucket]["conf_sum"] += c
        buckets[bucket]["correct"] += 1 if ok else 0
        buckets[bucket]["n"] += 1
    out = {}
    for b in range(5):
        if b in buckets:
            d = buckets[b]
            out[b] = {
                "avg_confidence": d["conf_sum"] / d["n"],
                "accuracy": d["correct"] / d["n"],
                "count": d["n"],
            }
    return out


def run_eval(dataset_path=None, verbose=True, route_fn=None, router_name=None):
    """Run evaluation; return metrics dict. Optionally pass route_fn and router_name (e.g. for run_all)."""
    active_route = route_fn if route_fn is not None else route
    name = router_name if router_name is not None else ROUTER_NAME
    if verbose:
        print(f"Router: {name}\n")
    data = load_dataset(dataset_path)
    correct = 0
    total = len(data)
    confusion: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    latencies: list[float] = []
    confidences: list[float] = []
    confidence_correct: list[bool] = []  # correctness for examples that have confidence

    for item in data:
        instruction = item["instruction"]
        expected = item["expected_category"]
        t0 = time.perf_counter()
        pred_result = active_route(instruction)
        latencies.append(time.perf_counter() - t0)
        pred = _extract_category(pred_result)
        ok = pred == expected
        if ok:
            correct += 1
        if isinstance(pred_result, dict) and "confidence" in pred_result:
            confidences.append(float(pred_result["confidence"]))
            confidence_correct.append(ok)
        confusion[expected][pred] += 1
        if verbose:
            status = "ok" if ok else "FAIL"
            print(f"  [{status}] {instruction[:50]}... -> {pred} (expected {expected})")

    accuracy = correct / total if total else 0.0
    avg_latency = sum(latencies) / len(latencies) if latencies else 0.0
    avg_confidence = sum(confidences) / len(confidences) if confidences else None
    calibration = _calibration_buckets(confidences, confidence_correct) if confidences else {}

    metrics = {
        "accuracy": accuracy,
        "correct": correct,
        "total": total,
        "confusion": {k: dict(v) for k, v in confusion.items()},
        "avg_latency_sec": avg_latency,
        "avg_confidence": avg_confidence,
        "calibration": calibration,
    }
    if verbose:
        print(f"\nAccuracy: {correct}/{total} = {accuracy:.2%}")
        print(f"Avg latency: {avg_latency:.3f}s")
        if avg_confidence is not None:
            print(f"Avg confidence: {avg_confidence:.3f}")
        print("Confusion (expected -> predicted):", dict(metrics["confusion"]))
        if calibration:
            print("Calibration (bucket -> avg_conf, accuracy):", calibration)
        if confidences:
            _plot_metrics(metrics, confidences, confidence_correct)
    return metrics


def _plot_metrics(
    metrics: dict,
    confidences: list[float],
    confidence_correct: list[bool],
) -> None:
    """Optional matplotlib plots: confidence distribution, confidence vs correctness, calibration, confusion matrix."""
    try:
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        return

    n_plots = 4
    fig, axes = plt.subplots(2, 2, figsize=(10, 8))
    fig.suptitle("Router evaluation metrics", fontsize=12)

    # 1. Confidence distribution
    ax = axes[0, 0]
    ax.hist(confidences, bins=10, edgecolor="black", alpha=0.7)
    ax.set_title("Router confidence distribution")
    ax.set_xlabel("Confidence")
    ax.set_ylabel("Count")

    # 2. Confidence vs correctness (binned)
    ax = axes[0, 1]
    bins = np.linspace(0, 1, 11)
    bin_indices = np.digitize(confidences, bins) - 1
    bin_indices = np.clip(bin_indices, 0, len(bins) - 2)
    bin_acc = []
    bin_centers = []
    for i in range(len(bins) - 1):
        mask = bin_indices == i
        if mask.sum() > 0:
            bin_acc.append(np.array(confidence_correct)[mask].mean())
            bin_centers.append((bins[i] + bins[i + 1]) / 2)
    if bin_centers:
        ax.plot(bin_centers, bin_acc, "o-", label="Accuracy")
        ax.plot([0, 1], [0, 1], "k--", alpha=0.5, label="Perfect calibration")
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1.05)
    ax.set_title("Confidence vs correctness")
    ax.set_xlabel("Confidence (bin center)")
    ax.set_ylabel("Accuracy")
    ax.legend()

    # 3. Calibration curve (from metrics)
    ax = axes[1, 0]
    cal = metrics.get("calibration", {})
    if cal:
        x = [cal[b]["avg_confidence"] for b in sorted(cal)]
        y = [cal[b]["accuracy"] for b in sorted(cal)]
        ax.plot(x, y, "o-")
        ax.plot([0, 1], [0, 1], "k--", alpha=0.5)
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1.05)
    ax.set_title("Calibration curve")
    ax.set_xlabel("Avg confidence")
    ax.set_ylabel("Accuracy")

    # 4. Confusion matrix (expected -> predicted)
    ax = axes[1, 1]
    conf = metrics.get("confusion", {})
    if conf and CATEGORIES:
        rows = list(CATEGORIES)
        cols = list(CATEGORIES)
        matrix = [[conf.get(r, {}).get(c, 0) for c in cols] for r in rows]
        im = ax.imshow(matrix, cmap="Blues")
        ax.set_xticks(range(len(cols)))
        ax.set_yticks(range(len(rows)))
        ax.set_xticklabels(cols)
        ax.set_yticklabels(rows)
        ax.set_xlabel("Predicted")
        ax.set_ylabel("Expected")
        ax.set_title("Confusion matrix")
        for i in range(len(rows)):
            for j in range(len(cols)):
                ax.text(j, i, matrix[i][j], ha="center", va="center")

    plt.tight_layout()
    plt.show()


def _mock_route(instruction: str) -> str:
    """Stub router for --mock mode: returns EDIT for all (no LLM call)."""
    return "EDIT"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Router evaluation harness")
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use stub router (no LLM server required); verifies dataset load and metrics.",
    )
    parser.add_argument(
        "--dataset",
        type=str,
        default=None,
        help="Path to JSON/JSONL dataset; default uses built-in dataset.",
    )
    args = parser.parse_args()

    if args.mock:
        run_eval(
            dataset_path=args.dataset,
            verbose=True,
            route_fn=_mock_route,
            router_name="mock",
        )
    else:
        run_eval(dataset_path=args.dataset)
