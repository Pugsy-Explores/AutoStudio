#!/usr/bin/env python3
"""
Principal Engineer Recommendation: Immediate Plan
Runs the 5-item suite to expose ~90% of hidden bugs:
  1. 10 explain tasks
  2. 10 small edit tasks
  3. router_eval benchmark
  4. failure tests
  5. 10 SWE-bench issues (placeholder)
  6. Phase 3 scenario evaluation (--scenarios)

Usage:
  python scripts/run_principal_engineer_suite.py              # run all
  python scripts/run_principal_engineer_suite.py --explain    # explain only
  python scripts/run_principal_engineer_suite.py --edit       # edit only
  python scripts/run_principal_engineer_suite.py --router-eval
  python scripts/run_principal_engineer_suite.py --failure-tests
  python scripts/run_principal_engineer_suite.py --swe-bench  # placeholder
  python scripts/run_principal_engineer_suite.py --scenarios  # Phase 3 scenario eval
  python scripts/run_principal_engineer_suite.py --failure-mining  # Phase 4: run scenarios 10x, aggregate failures
  python scripts/run_principal_engineer_suite.py --stress       # Phase 4: run scenarios with varied models/seeds
  python scripts/run_principal_engineer_suite.py --mock        # router_eval with --mock
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
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


def _classify_failure_reason(reason: str | None) -> tuple[str, str]:
    """Map failure_reason to (pattern, cause) for failure_patterns.md."""
    if not reason:
        return ("unknown", "no reason captured")
    r = reason.lower()
    # LOOP_PROTECTION / PLANNING_LOOP (from failure attribution layer)
    if "loop_protection" in r or r == "planning_loop":
        return ("planning_loop", "LOOP_PROTECTION / repeated planning failures")
    # NO_SIGNAL_FAILURE
    if r == "no_signal_failure":
        return ("no_signal", "retrieval worked but pool has no useful signal")
    if "empty" in r or "retrieval" in r or "0 results" in r:
        return ("retrieval_empty", "query rewrite / repo_map / symbol expansion")
    if "selection" in r or "context" in r or "explain" in r:
        return ("context_explosion", "context pruning / ranking weights")
    if "exploration" in r:
        return ("exploration_failure", "graph expansion / exploration")
    if "grounding" in r:
        return ("grounding_failure", "edit grounding / anchor")
    if "invalid" in r or "step" in r or "planner" in r:
        return ("planner_hallucination", "prompt constraints / few-shot / step validation")
    if "patch" in r or "validation" in r or "reject" in r:
        return ("editing_failure", "diff planner constraints / AST patch rules")
    if "timeout" in r or "runtime" in r:
        return ("timeout", "max_runtime / max_steps")
    return ("other", reason[:80])


def run_stress_test(scenarios_path: Path, project_root: Path, reps: int = 5) -> dict:
    """
    Phase 4: Run scenario suite with randomness (varied models, seeds, queries).
    Measure variance, stability, repeatability. Outputs to reports/stress_report.json.
    """
    import random

    all_summaries = []
    for run in range(reps):
        seed = random.randint(0, 2**31 - 1)
        random.seed(seed)
        print(f"\n=== Stress run {run + 1}/{reps} (seed={seed}) ===")
        report = run_scenarios(scenarios_path, project_root, use_agent_loop=True)
        all_summaries.append({
            "run": run + 1,
            "seed": seed,
            "task_success_rate": report["summary"].get("task_success_rate"),
            "failure_rate": report["summary"].get("failure_rate"),
            "replan_rate": report["summary"].get("replan_rate"),
            "latency_avg": report["summary"].get("latency_avg"),
        })

    # Aggregate
    success_rates = [s["task_success_rate"] for s in all_summaries if s["task_success_rate"] is not None]
    failure_rates = [s["failure_rate"] for s in all_summaries if s["failure_rate"] is not None]
    replan_rates = [s["replan_rate"] for s in all_summaries if s["replan_rate"] is not None]
    latencies = [s["latency_avg"] for s in all_summaries if s["latency_avg"] is not None]

    stress_summary = {
        "runs": reps,
        "task_success_rate_mean": sum(success_rates) / len(success_rates) if success_rates else 0,
        "task_success_rate_std": (sum((x - sum(success_rates) / len(success_rates)) ** 2 for x in success_rates) / len(success_rates)) ** 0.5 if len(success_rates) > 1 else 0,
        "failure_rate_mean": sum(failure_rates) / len(failure_rates) if failure_rates else 0,
        "replan_rate_mean": sum(replan_rates) / len(replan_rates) if replan_rates else 0,
        "latency_avg_mean": sum(latencies) / len(latencies) if latencies else 0,
    }

    reports_dir = project_root / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    stress_path = reports_dir / "stress_report.json"
    with open(stress_path, "w", encoding="utf-8") as f:
        json.dump({"summary": stress_summary, "runs": all_summaries}, f, indent=2)
    print(f"\nStress report written to {stress_path}")
    return stress_summary


def run_failure_mining(scenarios_path: Path, project_root: Path, reps: int = 10) -> dict:
    """
    Phase 4: Run scenario suite N times, aggregate failures, update failure_patterns.md.
    """
    from collections import Counter

    pattern_counts: Counter = Counter()
    pattern_causes: dict[str, str] = {}
    all_failures: list[dict] = []

    for run in range(reps):
        print(f"\n=== Failure mining run {run + 1}/{reps} ===")
        report = run_scenarios(scenarios_path, project_root)
        for r in report.get("results", []):
            if not r.get("task_success") and r.get("failure_reason"):
                pattern, cause = _classify_failure_reason(r["failure_reason"])
                pattern_counts[pattern] += 1
                pattern_causes[pattern] = cause
                all_failures.append({
                    "id": r.get("id"),
                    "pattern": pattern,
                    "reason": r["failure_reason"][:200],
                })

    # Update failure_patterns.md
    patterns_md = ROOT / "dev" / "evaluation" / "failure_patterns.md"
    lines = [
        "# Failure Patterns",
        "",
        "<!-- Document recurring failure patterns and root causes to prevent repeating mistakes -->",
        "",
        "## Phase 4 Mining (aggregated from scenario runs)",
        "",
        "| Pattern | Count | Cause |",
        "|---------|-------|-------|",
    ]
    for pattern, count in pattern_counts.most_common():
        cause = pattern_causes.get(pattern, "?")
        lines.append(f"| {pattern} | {count} | {cause} |")

    lines.extend([
        "",
        "## Known Patterns (reference)",
        "",
        "| Pattern | Cause |",
        "|---------|-------|",
        "| Retrieval Empty | query rewrite incorrect |",
        "| Planner Hallucinated Tool | missing step constraint |",
    ])

    patterns_md.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nUpdated {patterns_md}")

    return {"pattern_counts": dict(pattern_counts), "total_failures": len(all_failures)}


def run_scenarios(scenarios_path: Path, project_root: Path, use_agent_loop: bool = False) -> dict:
    """
    Phase 3 scenario evaluation: load scenarios, run via run_controller,
    collect task_success, retrieval_success, edit_success, latency_sec.
    Returns report dict for eval_report.json.
    """
    from agent.memory.task_memory import load_task
    from agent.meta.failure_attribution import ensure_failure_reason
    from agent.orchestrator.agent_controller import run_controller

    with open(scenarios_path, encoding="utf-8") as f:
        scenarios = json.load(f)

    project_root_str = str(project_root)
    results = []

    for i, scenario in enumerate(scenarios, 1):
        sid = scenario.get("id", f"S{i}")
        instruction = scenario.get("instruction", "")
        expected_min_steps = scenario.get("expected_min_steps", 2)
        expect_edit = scenario.get("expect_edit", False)
        expected_actions = scenario.get("expected_actions", [])

        print(f"\n--- Scenario {i}/{len(scenarios)} [{sid}]: {instruction[:60]}... ---")

        task_success = False
        retrieval_success = None
        edit_success = None
        latency_sec = 0.0
        failure_reason = None
        replan_count = None
        tool_calls = None
        termination_reason = None
        edit_failure_reason = None
        errors = []
        result = {}

        try:
            start = time.perf_counter()
            if use_agent_loop:
                from agent.orchestrator.agent_loop import run_agent

                os.environ["SERENA_PROJECT_DIR"] = project_root_str
                state = run_agent(instruction)
                latency_sec = time.perf_counter() - start
                counts = state.context.get("execution_counts", {})
                replan_count = counts.get("replan_count")
                tool_calls = counts.get("tool_calls")
                completed_steps = len(state.completed_steps)
                errors = [] if all(r.success for r in state.step_results) else [r.error or "step failed" for r in state.step_results if not r.success]
                termination_reason = state.context.get("termination_reason")
                # For expect_edit: check EDIT step outputs for files_modified
                files_modified = []
                for step, res in zip(state.completed_steps, state.step_results):
                    if (step.get("action") or "").upper() == "EDIT" and res.success and isinstance(res.output, dict):
                        files_modified.extend(res.output.get("files_modified", []))
                result = {"task_id": None, "errors": errors, "_files_modified": files_modified}
            else:
                result = run_controller(instruction, project_root=project_root_str)
                latency_sec = time.perf_counter() - start
                completed_steps = result.get("completed_steps", 0)
                errors = result.get("errors", [])
                termination_reason = result.get("termination_reason")
                lo = result.get("loop_output") or {}
                et = lo.get("edit_telemetry") or {}
                edit_failure_reason = et.get("edit_failure_reason")

            task_success = len(errors) == 0 and completed_steps >= expected_min_steps

            if not task_success and errors:
                failure_reason = "; ".join(str(e) for e in errors[:3])

            if "SEARCH" in expected_actions:
                retrieval_success = task_success

            if expect_edit:
                if result.get("_files_modified"):
                    edit_success = len(result["_files_modified"]) >= 1
                elif result.get("files_modified"):
                    edit_success = len(result["files_modified"]) >= 1
                elif result.get("task_id"):
                    task = load_task(result["task_id"], project_root=project_root_str)
                    if task:
                        edit_success = len(task.get("files_modified", [])) >= 1

            print(f"  task_success={task_success} retrieval={retrieval_success} edit={edit_success} latency={latency_sec:.2f}s")
        except Exception as e:
            failure_reason = str(e)
            errors = [str(e)]
            print(f"  ERROR: {e}")

        record = {
            "id": sid,
            "task_id": result.get("task_id"),
            "instruction": instruction,
            "task_success": task_success,
            "retrieval_success": retrieval_success,
            "edit_success": edit_success,
            "latency_sec": round(latency_sec, 2),
            "failure_reason": failure_reason,
            "errors": errors,
            "termination_reason": termination_reason,
            "edit_failure_reason": edit_failure_reason,
            "expect_edit": expect_edit,
            "replan_count": replan_count,
            "tool_calls": tool_calls,
        }
        ensure_failure_reason(record, task_id=record.get("task_id") or sid)
        results.append(record)

    task_ok = sum(1 for r in results if r["task_success"])
    retrieval_ok = sum(1 for r in results if r["retrieval_success"] is True)
    retrieval_total = sum(1 for r in results if r["retrieval_success"] is not None)
    edit_total = sum(1 for r in results if r["expect_edit"])
    edit_ok = sum(1 for r in results if r["expect_edit"] and r["edit_success"] is True)
    latencies = [r["latency_sec"] for r in results]
    mean_latency = sum(latencies) / len(latencies) if latencies else 0
    replan_counts = [r["replan_count"] for r in results if r.get("replan_count") is not None]
    replan_rate = sum(1 for c in replan_counts if c and c > 0) / len(replan_counts) if replan_counts else None

    summary = {
        "total": len(results),
        "task_success_rate": round(task_ok / len(results), 2) if results else 0,
        "failure_rate": round(1 - task_ok / len(results), 2) if results else 0,
        "retrieval_recall": round(retrieval_ok / retrieval_total, 2) if retrieval_total else 0,
        "edit_success_rate": round(edit_ok / edit_total, 2) if edit_total else 0,
        "mean_latency_sec": round(mean_latency, 2),
        "latency_avg": round(mean_latency, 2),
    }
    if replan_rate is not None:
        summary["replan_rate"] = round(replan_rate, 2)

    report = {"summary": summary, "results": results}

    reports_dir = project_root / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = reports_dir / "eval_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\nReport written to {report_path}")

    return report


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
    parser.add_argument("--scenarios", action="store_true", help="Phase 3 scenario evaluation (tests/agent_scenarios.json)")
    parser.add_argument("--failure-mining", action="store_true", help="Phase 4: run scenarios 10x, aggregate failures to failure_patterns.md")
    parser.add_argument("--mining-reps", type=int, default=10, help="Runs for failure mining (default 10)")
    parser.add_argument("--stress", action="store_true", help="Phase 4: run scenarios with varied seeds, measure variance/stability")
    parser.add_argument("--stress-reps", type=int, default=5, help="Runs for stress test (default 5)")
    parser.add_argument("--use-agent-loop", action="store_true", help="Use run_agent (agent_loop) for scenarios to get Phase 4 metrics")
    parser.add_argument("--mock", action="store_true", help="Use --mock for router_eval (no LLM)")
    parser.add_argument("-n", "--count", type=int, default=10, help="Number of explain/edit tasks (default 10)")
    args = parser.parse_args()

    run_all = not any([
        args.explain, args.edit, args.router_eval, args.failure_tests, args.swe_bench,
        args.scenarios, args.failure_mining, args.stress,
    ])

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

    if args.failure_mining:
        scenarios_path = ROOT / "tests" / "agent_scenarios.json"
        if scenarios_path.exists():
            mining_report = run_failure_mining(scenarios_path, ROOT, reps=args.mining_reps)
            summary.append(
                f"Failure mining: {mining_report['total_failures']} failures, "
                f"{len(mining_report['pattern_counts'])} patterns"
            )
        else:
            summary.append("Failure mining: SKIPPED (tests/agent_scenarios.json not found)")
            ok = False

    if args.stress:
        scenarios_path = ROOT / "tests" / "agent_scenarios.json"
        if scenarios_path.exists():
            stress_report = run_stress_test(scenarios_path, ROOT, reps=args.stress_reps)
            summary.append(
                f"Stress: success_rate={stress_report.get('task_success_rate_mean', 0):.2f} "
                f"failure_rate={stress_report.get('failure_rate_mean', 0):.2f} "
                f"replan_rate={stress_report.get('replan_rate_mean', 0):.2f} "
                f"latency_avg={stress_report.get('latency_avg_mean', 0):.2f}s"
            )
        else:
            summary.append("Stress: SKIPPED (tests/agent_scenarios.json not found)")
            ok = False

    if run_all or args.scenarios:
        scenarios_path = ROOT / "tests" / "agent_scenarios.json"
        if scenarios_path.exists():
            report = run_scenarios(scenarios_path, ROOT, use_agent_loop=args.use_agent_loop)
            s = report["summary"]
            summary.append(
                f"Scenarios: {s['task_success_rate']*100:.0f}% task success, "
                f"{s['retrieval_recall']*100:.0f}% retrieval, {s['edit_success_rate']*100:.0f}% edit "
                f"(n={s['total']})"
            )
            if s["task_success_rate"] < 0.7:
                ok = False
        else:
            summary.append("Scenarios: SKIPPED (tests/agent_scenarios.json not found)")
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
