"""
CLI: scan an agent_eval run directory and validate routing contract telemetry.

Example:
  python3 -m tests.agent_eval.runner --suite routing_contract --execution-mode live_model
  python3 -m tests.agent_eval.check_routing_contract --run-dir artifacts/agent_eval_runs/latest
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tests.agent_eval.routing_contract_checker import ROUTING_CONTRACT_TASK_IDS, check_outcome_audit


def _load_outcome(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except OSError:
        return None
    except json.JSONDecodeError:
        return None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Check routing contract telemetry in agent_eval outcome.json files.")
    p.add_argument(
        "--run-dir",
        type=Path,
        required=True,
        help="Run directory (contains tasks/<task_id>/outcome.json)",
    )
    p.add_argument(
        "--all-tasks",
        action="store_true",
        help="Process every tasks/* folder; default limits to routing_contract task_ids",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Print one JSON object with strict/soft results per task",
    )
    args = p.parse_args(argv)

    run_dir: Path = args.run_dir.resolve()
    tasks_dir = run_dir / "tasks"
    if not tasks_dir.is_dir():
        print(f"error: not a directory or missing tasks/: {tasks_dir}", file=sys.stderr)
        return 2

    strict_all: list[str] = []
    soft_all: list[str] = []
    per_task: dict[str, dict[str, list[str]]] = {}

    for sub in sorted(tasks_dir.iterdir()):
        if not sub.is_dir():
            continue
        task_id = sub.name
        if not args.all_tasks and task_id not in ROUTING_CONTRACT_TASK_IDS:
            continue

        outcome_path = sub / "outcome.json"
        raw = _load_outcome(outcome_path)
        if raw is None:
            strict_all.append(f"{task_id}: missing or invalid outcome.json")
            per_task[task_id] = {"strict": [f"{task_id}: missing or invalid outcome.json"], "soft": []}
            continue

        audit = raw.get("_audit")
        strict, soft = check_outcome_audit(audit if isinstance(audit, dict) else None, task_id)
        for v in strict:
            strict_all.append(f"{task_id}: {v}")
        for w in soft:
            soft_all.append(f"{task_id}: {w}")
        per_task[task_id] = {"strict": strict, "soft": soft}

    if args.json:
        print(
            json.dumps(
                {"run_dir": str(run_dir), "per_task": per_task, "strict_failed": len(strict_all) > 0},
                indent=2,
            )
        )
    else:
        for task_id, buckets in sorted(per_task.items()):
            if buckets["strict"]:
                print(f"[STRICT] {task_id}")
                for v in buckets["strict"]:
                    print(f"  - {v}")
            if buckets["soft"]:
                print(f"[SOFT] {task_id}")
                for w in buckets["soft"]:
                    print(f"  - {w}")
        if not per_task:
            print("(no routing_contract task outcomes found)")
        if soft_all and not strict_all:
            print(f"\nSummary: {len(soft_all)} soft warning(s), 0 strict failure(s)")
        elif strict_all:
            print(f"\nSummary: {len(strict_all)} strict failure(s), {len(soft_all)} soft warning(s)")
        else:
            print("\nSummary: all strict checks passed.")

    return 1 if strict_all else 0


if __name__ == "__main__":
    raise SystemExit(main())
