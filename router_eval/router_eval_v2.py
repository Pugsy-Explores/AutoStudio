"""
Router v2 evaluation: 4-category taxonomy, confidence metrics, calibration, plots.
Run with: python3 -m router_eval.router_eval_v2
"""

import time
from collections import defaultdict

from router_eval.dataset_v2 import CATEGORIES_V2, load_dataset_v2
from router_eval.routers.router_v2 import ROUTER_NAME, route


def _extract_category(result) -> str:
    """Extract category from route() dict result."""
    if isinstance(result, dict):
        return result.get("category", "EXPLAIN")
    return str(result)


def _calibration_buckets(confidences: list[float], correct_flags: list[bool]) -> dict:
    """Compute per-bucket accuracy for calibration (bucket -> avg_conf, accuracy, count)."""
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


def _plot_metrics_v2(
    metrics: dict,
    confidences: list[float],
    confidence_correct: list[bool],
    categories: tuple[str, ...] = CATEGORIES_V2,
    save_path: str = "router_v2_eval_plots.png",
) -> None:
    """Generate same 4 plots as router_eval; save to file instead of show."""
    try:
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        return

    fig, axes = plt.subplots(2, 2, figsize=(10, 8))
    fig.suptitle("Router v2 evaluation metrics", fontsize=12)

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
    if conf and categories:
        rows = list(categories)
        cols = list(categories)
        matrix = [[conf.get(r, {}).get(c, 0) for c in cols] for r in rows]
        ax.imshow(matrix, cmap="Blues")
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
    plt.savefig(save_path)
    plt.close()


def run_eval_v2(
    dataset_path=None,
    use_golden: bool = False,
    use_adversarial: bool = False,
    verbose: bool = True,
    save_plots: bool = True,
    plots_path: str = "router_v2_eval_plots.png",
) -> dict:
    """Run v2 evaluation; return metrics dict. Optionally save plots.

    use_golden: if True, load the golden dataset file.
    use_adversarial: if True, load the adversarial dataset file.
    Otherwise use the normal built-in dataset (or dataset_path if provided).
    """
    if verbose:
        print(f"Router: {ROUTER_NAME}\n")
        if use_adversarial:
            print("Dataset: adversarial (adversarial_dataset_v2.json)\n")
        elif use_golden:
            print("Dataset: golden (golden_dataset_v2.json)\n")
        elif dataset_path:
            print(f"Dataset: file {dataset_path}\n")
        else:
            print("Dataset: normal (built-in)\n")
    data = load_dataset_v2(
        path=dataset_path,
        use_golden=use_golden,
        use_adversarial=use_adversarial,
    )
    correct = 0
    total = len(data)
    confusion: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    latencies: list[float] = []
    confidences: list[float] = []
    confidence_correct: list[bool] = []
    bar_width = 30

    # Progress bar on one line; logs print below it
    print(f"  Progress: [{'-' * bar_width}] 0% (0/{total})", flush=True)
    for i, item in enumerate(data):
        instruction = item["instruction"]
        expected = item["expected_category"]
        t0 = time.perf_counter()
        pred_result = route(instruction)
        latencies.append(time.perf_counter() - t0)
        pred = _extract_category(pred_result)
        ok = pred == expected
        if ok:
            correct += 1
        if isinstance(pred_result, dict) and "confidence" in pred_result:
            confidences.append(float(pred_result["confidence"]))
            confidence_correct.append(ok)
        confusion[expected][pred] += 1

        pct = (i + 1) * 100 // total if total else 0
        filled = int(bar_width * (i + 1) / total) if total else 0
        bar = "=" * filled + "-" * (bar_width - filled)
        progress_line = f"  Progress: [{bar}] {pct}% ({i + 1}/{total})"

        if verbose:
            # Move cursor up to progress line, redraw and clear to EOL, then back down to column 0 and print log below
            n_up = i + 1
            print(f"\033[{n_up}A\r{progress_line}\033[K\033[{n_up}B\r", end="", flush=True)
            status = "ok" if ok else "FAIL"
            print(f"  [{status}] {instruction[:50]}... -> {pred} (expected {expected})")
        else:
            print(f"\r{progress_line}", end="", flush=True)
    print()

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

    if save_plots and confidences:
        _plot_metrics_v2(metrics, confidences, confidence_correct, save_path=plots_path)
        if verbose:
            print(f"Plots saved to {plots_path}")

    return metrics


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run router v2 evaluation.")
    parser.add_argument(
        "--golden",
        action="store_true",
        help="Use the golden dataset (golden_dataset_v2.json) instead of the normal built-in dataset.",
    )
    parser.add_argument(
        "--adversarial",
        action="store_true",
        help="Use the adversarial dataset (adversarial_dataset_v2.json) for edge-case evaluation.",
    )
    parser.add_argument(
        "--dataset-path",
        type=str,
        default=None,
        help="Path to a custom dataset JSON/JSONL file (ignored if --golden or --adversarial is set).",
    )
    args = parser.parse_args()
    run_eval_v2(
        dataset_path=args.dataset_path,
        use_golden=args.golden,
        use_adversarial=args.adversarial,
    )
