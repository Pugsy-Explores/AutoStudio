"""
Run all routers on the evaluation dataset and print a summary table.
"""

from router_eval.router_eval import run_eval
from router_eval.routers import baseline_router
from router_eval.routers import fewshot_router
from router_eval.routers import ensemble_router
from router_eval.routers import confidence_router
from router_eval.routers import dual_router
from router_eval.routers import critic_router
from router_eval.routers import final_router

ROUTERS = [
    (baseline_router.ROUTER_NAME, baseline_router.route),
    (fewshot_router.ROUTER_NAME, fewshot_router.route),
    (ensemble_router.ROUTER_NAME, ensemble_router.route),
    (confidence_router.ROUTER_NAME, confidence_router.route),
    (dual_router.ROUTER_NAME, dual_router.route),
    (critic_router.ROUTER_NAME, critic_router.route),
    (final_router.ROUTER_NAME, final_router.route),
]


def main():
    dataset_path = None
    results = []
    for name, route_fn in ROUTERS:
        print(f"Running {name}...", flush=True)
        try:
            metrics = run_eval(
                dataset_path=dataset_path,
                verbose=True,
                route_fn=route_fn,
                router_name=name,
            )
            results.append((name, metrics, None))
        except Exception as e:
            results.append((name, None, e))

    # Summary table
    print("\n" + "=" * 70)
    print("ROUTER EVAL SUMMARY")
    print("=" * 70)
    fmt = "{:<12} {:>8} {:>10} {:>10} {:>12}"
    print(fmt.format("Router", "Accuracy", "Correct", "Latency(s)", "Avg Conf"))
    print("-" * 70)
    for name, metrics, err in results:
        if err is not None:
            print(f"{name:<12} ERROR: {err}")
            continue
        acc = metrics["accuracy"]
        correct = metrics["correct"]
        total = metrics["total"]
        lat = metrics["avg_latency_sec"]
        conf = metrics.get("avg_confidence")
        conf_str = f"{conf:.3f}" if conf is not None else "—"
        print(fmt.format(name, f"{acc:.1%}", f"{correct}/{total}", f"{lat:.3f}", conf_str))
    print("=" * 70)


if __name__ == "__main__":
    main()
