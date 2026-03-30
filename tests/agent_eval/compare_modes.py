"""
Stage 31 — Compact comparison utility for offline vs live_model suite results.

Usage:
  python3 -m tests.agent_eval.compare_modes <offline_summary.json> <live_summary.json>
  python3 -m tests.agent_eval.compare_modes artifacts/agent_eval_runs/offline_run/summary.json \\
      artifacts/agent_eval_runs/live_run/summary.json

Output: side-by-side comparison of success counts, integrity metrics, and model call totals.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _load_summary(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _fmt(val, default="—"):
    if val is None:
        return default
    return str(val)


def compare(offline_path: Path, live_path: Path) -> str:
    """Produce a compact comparison of two summary.json files."""
    a = _load_summary(offline_path)
    b = _load_summary(live_path)

    lines = [
        "# Offline vs Live-Model Comparison",
        "",
        f"| Metric | Offline ({offline_path.name}) | Live ({live_path.name}) |",
        "|--------|------------------------------|--------------------------|",
    ]

    metrics = [
        ("execution_mode", "Execution mode"),
        ("total_tasks", "Total tasks"),
        ("success_count", "Success count"),
        ("validation_pass_count", "Validation pass"),
        ("structural_success_count", "Structural success"),
        ("run_valid_for_live_eval", "Run valid for live eval"),
        ("invalid_live_model_task_count", "Invalid live-model tasks"),
        ("zero_model_call_task_count", "Zero model-call tasks"),
        ("offline_stubbed_task_count", "Offline-stubbed tasks"),
        ("explain_stubbed_task_count", "Explain-stubbed tasks"),
        ("plan_injection_task_count", "Plan-injection tasks"),
        ("model_call_count_total", "Model calls total"),
        ("small_model_call_count_total", "Small model calls"),
        ("reasoning_model_call_count_total", "Reasoning model calls"),
    ]

    for key, label in metrics:
        va = a.get(key, "—")
        vb = b.get(key, "—")
        lines.append(f"| {label} | {_fmt(va)} | {_fmt(vb)} |")

    lines.extend([
        "",
        "## Per-task alignment",
        "Compare task_ids to ensure same suite. Offline and live runs should use the same task set.",
    ])

    task_ids_a = set(a.get("task_ids", []))
    task_ids_b = set(b.get("task_ids", []))
    if task_ids_a != task_ids_b:
        lines.append(f"- **Mismatch:** Offline has {len(task_ids_a)} tasks, Live has {len(task_ids_b)}")
        only_a = task_ids_a - task_ids_b
        only_b = task_ids_b - task_ids_a
        if only_a:
            lines.append(f"- Only in offline: {sorted(only_a)[:5]}{'...' if len(only_a) > 5 else ''}")
        if only_b:
            lines.append(f"- Only in live: {sorted(only_b)[:5]}{'...' if len(only_b) > 5 else ''}")
    else:
        lines.append(f"- Same {len(task_ids_a)} tasks in both runs.")

    return "\n".join(lines)


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: python3 -m tests.agent_eval.compare_modes <offline_summary.json> <live_summary.json>")
        return 1
    offline_p = Path(sys.argv[1])
    live_p = Path(sys.argv[2])
    if not offline_p.is_file():
        print(f"Error: {offline_p} not found")
        return 1
    if not live_p.is_file():
        print(f"Error: {live_p} not found")
        return 1
    print(compare(offline_p, live_p))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
