"""
Post-hoc checks for SEARCH stack agent_eval runs (machine-checkable artifacts).

Reads tasks/<task_id>/outcome.json and validates schema + soft SEARCH-relevant audit fields.

Example:
  python3 -m tests.agent_eval.runner --suite search_stack --execution-mode offline --output artifacts/agent_eval_runs/latest
  python3 -m tests.agent_eval.check_search_stack --run-dir artifacts/agent_eval_runs/latest
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tests.agent_eval.suites.search_stack import load_search_stack_specs

SEARCH_STACK_TASK_IDS = frozenset(s.task_id for s in load_search_stack_specs())


def check_search_stack_outcome(audit: dict | None, task_id: str) -> tuple[list[str], list[str]]:
    """Return (strict_violations, soft_warnings)."""
    strict: list[str] = []
    soft: list[str] = []
    if not audit or not isinstance(audit, dict):
        return [f"{task_id}: missing _audit"], []
    if audit.get("execution_mode") is None:
        soft.append(f"{task_id}: execution_mode not recorded")
    if task_id == "ss_negative_miss" and audit.get("structural_success") is True:
        soft.append(f"{task_id}: negative task structurally succeeded (may be ok under stubs)")
    if audit.get("structural_success") is None:
        soft.append(f"{task_id}: structural_success missing")
    return strict, soft


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Check SEARCH stack agent_eval outcomes.")
    p.add_argument("--run-dir", type=Path, required=True, help="Run directory with tasks/<id>/outcome.json")
    p.add_argument("--json", action="store_true", help="Emit JSON summary")
    args = p.parse_args(argv)
    run_dir = args.run_dir.resolve()
    tasks_dir = run_dir / "tasks"
    if not tasks_dir.is_dir():
        print(f"error: missing tasks/: {tasks_dir}", file=sys.stderr)
        return 2

    strict_all: list[str] = []
    per_task: dict[str, dict[str, list[str]]] = {}

    for sub in sorted(tasks_dir.iterdir()):
        if not sub.is_dir() or sub.name not in SEARCH_STACK_TASK_IDS:
            continue
        task_id = sub.name
        path = sub / "outcome.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            strict_all.append(f"{task_id}: invalid outcome.json ({e})")
            per_task[task_id] = {"strict": strict_all[-1:], "soft": []}
            continue
        audit = raw.get("_audit") if isinstance(raw, dict) else None
        st, sf = check_search_stack_outcome(audit if isinstance(audit, dict) else None, task_id)
        strict_all.extend(st)
        per_task[task_id] = {"strict": st, "soft": sf}

    if args.json:
        print(json.dumps({"run_dir": str(run_dir), "per_task": per_task, "strict_failed": len(strict_all) > 0}, indent=2))
    else:
        for tid, buckets in sorted(per_task.items()):
            for v in buckets["strict"]:
                print(f"[STRICT] {tid}: {v}")
            for w in buckets["soft"]:
                print(f"[SOFT] {tid}: {w}")
        if not per_task:
            print("(no search_stack task outcomes found)")

    return 1 if strict_all else 0


if __name__ == "__main__":
    raise SystemExit(main())
