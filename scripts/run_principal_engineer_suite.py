#!/usr/bin/env python3
"""
Principal Engineer Recommendation: Immediate Plan
Runs the 5-item suite to expose ~90% of hidden bugs:
  1. 10 explain tasks
  2. 10 small edit tasks
  3. router_eval benchmark
  4. failure tests
  5. 10 SWE-bench issues (placeholder)

Usage:
  python scripts/run_principal_engineer_suite.py              # run all
  python scripts/run_principal_engineer_suite.py --explain    # explain only
  python scripts/run_principal_engineer_suite.py --edit       # edit only
  python scripts/run_principal_engineer_suite.py --router-eval
  python scripts/run_principal_engineer_suite.py --failure-tests
  python scripts/run_principal_engineer_suite.py --swe-bench  # placeholder
  python scripts/run_principal_engineer_suite.py --mock        # router_eval with --mock
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path

# Ensure AutoStudio root is on path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(message)s")

# ---------------------------------------------------------------------------
# Task lists
# ---------------------------------------------------------------------------

EXPLAIN_TASKS = [
    "What functions does the auth service expose to other modules?",
    "Explain what parameters the createOrder API expects.",
    "What does the storage client return when an upload succeeds?",
    "List the public methods available in the payment service client.",
    "What environment variables are required by the worker service?",
    "Explain how the dispatcher routes SEARCH steps.",
    "What does the step_dispatcher do?",
    "Explain the tool graph structure.",
    "How does the instruction router classify queries?",
    "What does the replanner do when a step fails?",
]

SMALL_EDIT_TASKS = [
    "Add a type hint to the route_instruction function return type.",
    "Fix any typo in router_eval README.",
    "Add a docstring to the load_dataset function.",
    "Add an empty line before the first class in dataset.py.",
    "Change a print to logger in router_eval/run_all_routers.py if any exist.",
    "Add a trailing newline to agent/cli/run_agent.py if missing.",
    "Rename a local variable for clarity in one function.",
    "Add a # noqa comment to suppress a known linter warning.",
    "Add an __all__ list to router_eval/__init__.py if it exists.",
    "Add a blank line between two logical sections in a YAML file.",
]

# ---------------------------------------------------------------------------
# Runners
# ---------------------------------------------------------------------------


def run_explain_tasks(count: int = 10) -> dict[str, bool]:
    """Run N explain tasks via run_agent. Returns {instruction: success}."""
    from agent.orchestrator.agent_loop import run_agent

    tasks = EXPLAIN_TASKS[:count]
    results = {}
    for i, instruction in enumerate(tasks, 1):
        print(f"\n--- Explain {i}/{len(tasks)}: {instruction[:60]}... ---")
        try:
            state = run_agent(instruction)
            success = all(r.success for r in state.step_results) if state.step_results else False
            results[instruction] = success
            print(f"  success={success} steps={len(state.step_results)}")
        except Exception as e:
            print(f"  ERROR: {e}")
            results[instruction] = False
    return results


def run_edit_tasks(count: int = 10) -> dict[str, bool]:
    """Run N small edit tasks via run_agent. Returns {instruction: success}."""
    from agent.orchestrator.agent_loop import run_agent

    tasks = SMALL_EDIT_TASKS[:count]
    results = {}
    for i, instruction in enumerate(tasks, 1):
        print(f"\n--- Edit {i}/{len(tasks)}: {instruction[:60]}... ---")
        try:
            state = run_agent(instruction)
            success = all(r.success for r in state.step_results) if state.step_results else False
            results[instruction] = success
            print(f"  success={success} steps={len(state.step_results)}")
        except Exception as e:
            print(f"  ERROR: {e}")
            results[instruction] = False
    return results


def run_router_eval(mock: bool = False) -> bool:
    """Run router_eval benchmark. Returns True if no exception."""
    cmd = [sys.executable, "-m", "router_eval.run_all_routers"]
    if mock:
        cmd.append("--mock")
    print("\n--- Router Eval ---")
    result = subprocess.run(cmd, cwd=ROOT)
    return result.returncode == 0


def run_failure_tests() -> bool:
    """Run failure/repair tests. Returns True if all pass."""
    cmd = [
        sys.executable, "-m", "pytest",
        "tests/test_repair_loop.py",
        "tests/test_agent_robustness.py",
        "-v",
        "--tb=short",
    ]
    print("\n--- Failure Tests ---")
    result = subprocess.run(cmd, cwd=ROOT)
    return result.returncode == 0


def run_swe_bench_placeholder() -> None:
    """Placeholder for SWE-bench. Prints setup instructions."""
    print("\n--- SWE-bench (placeholder) ---")
    print("SWE-bench is not integrated. To run 10 SWE-bench issues:")
    print("  1. Install: pip install swebench")
    print("  2. See: https://github.com/princeton-nlp/SWE-bench")
    print("  3. Create a script that loads 10 instances and calls run_agent(instance.problem_statement)")
    print("  4. Compare patch output against instance.patch")
    print("")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Principal Engineer suite: expose 90% of hidden bugs")
    parser.add_argument("--explain", action="store_true", help="Run 10 explain tasks")
    parser.add_argument("--edit", action="store_true", help="Run 10 small edit tasks")
    parser.add_argument("--router-eval", action="store_true", help="Run router_eval benchmark")
    parser.add_argument("--failure-tests", action="store_true", help="Run failure tests")
    parser.add_argument("--swe-bench", action="store_true", help="SWE-bench placeholder (no-op)")
    parser.add_argument("--mock", action="store_true", help="Use --mock for router_eval (no LLM)")
    parser.add_argument("-n", "--count", type=int, default=10, help="Number of explain/edit tasks (default 10)")
    args = parser.parse_args()

    run_all = not any([args.explain, args.edit, args.router_eval, args.failure_tests, args.swe_bench])

    summary = []
    ok = True

    if run_all or args.explain:
        results = run_explain_tasks(args.count)
        passed = sum(1 for v in results.values() if v)
        summary.append(f"Explain: {passed}/{len(results)} passed")
        if passed < len(results):
            ok = False

    if run_all or args.edit:
        results = run_edit_tasks(args.count)
        passed = sum(1 for v in results.values() if v)
        summary.append(f"Edit: {passed}/{len(results)} passed")
        if passed < len(results):
            ok = False

    if run_all or args.router_eval:
        if run_router_eval(mock=args.mock):
            summary.append("Router eval: OK")
        else:
            summary.append("Router eval: FAILED")
            ok = False

    if run_all or args.failure_tests:
        if run_failure_tests():
            summary.append("Failure tests: OK")
        else:
            summary.append("Failure tests: FAILED")
            ok = False

    if run_all or args.swe_bench:
        run_swe_bench_placeholder()
        summary.append("SWE-bench: (placeholder)")

    print("\n" + "=" * 60)
    print("PRINCIPAL ENGINEER SUITE SUMMARY")
    print("=" * 60)
    for s in summary:
        print(s)
    print("=" * 60)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
