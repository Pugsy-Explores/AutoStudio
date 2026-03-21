"""Golden test runner — execute tests, evaluate constraints, aggregate results."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from tests.golden.adapter import to_evaluation_view
from tests.golden.assertions import evaluate_constraints
from tests.golden.judge import run_llm_judge
from tests.golden.schema import GoldenTest


def classify_result(structural_pass: bool, judge_result: Optional[Dict[str, Any]]) -> str:
    """Decision classification: HARD_FAIL, SOFT_FAIL, UNCERTAIN_PASS, PASS, PASS_NO_JUDGE."""
    if not structural_pass:
        return "HARD_FAIL"
    if judge_result is None:
        return "PASS_NO_JUDGE"
    if not judge_result.get("passed", False):
        return "SOFT_FAIL"
    if judge_result.get("confidence") == "low":
        return "UNCERTAIN_PASS"
    return "PASS"


class GoldenTestRunner:
    def __init__(
        self,
        executor: Callable[[Dict[str, Any]], Dict[str, Any]],
        pre_hook: Optional[Callable[[GoldenTest], GoldenTest]] = None,
        post_hook: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
    ):
        self.executor = executor
        self.pre_hook = pre_hook
        self.post_hook = post_hook

    def run_test(self, test: GoldenTest) -> Dict[str, Any]:
        if self.pre_hook:
            test = self.pre_hook(test)

        raw = self.executor(test["input"])

        if self.post_hook:
            raw = self.post_hook(raw)

        result = to_evaluation_view(raw)
        failures = evaluate_constraints(result, test["expected"])
        structural_pass = len(failures) == 0

        judge_config = test.get("llm_judge") or {}
        judge_enabled = bool(judge_config.get("enabled"))
        judge_result: Dict[str, Any] = {}
        if judge_enabled:
            judge_result = run_llm_judge(test, result, judge_config)

        passed = structural_pass
        if judge_enabled and judge_result:
            passed = passed and judge_result.get("passed", False)

        judge_disagrees_with_structure = (
            judge_enabled
            and judge_result
            and (judge_result.get("passed", False) != structural_pass)
        )

        decision = classify_result(
            structural_pass, judge_result if judge_enabled else None
        )

        out = {
            "id": test["id"],
            "passed": passed,
            "failures": failures,
            "metrics": result.get("metrics", {}),
            "decision": decision,
            "debug": {
                "input": test["input"],
                "raw_output": raw,
            },
        }
        if judge_enabled and judge_result:
            out["llm_judge"] = judge_result
        if judge_enabled:
            out["analysis"] = {
                "judge_vs_structure_mismatch": judge_disagrees_with_structure,
                "confidence_flag": judge_result.get("confidence") == "low" if judge_result else False,
            }
        if not out["passed"]:
            out["failure_surface"] = {
                "metrics": result.get("metrics"),
                "structure": result.get("structure"),
            }
        return out


def run_suite(
    tests: List[GoldenTest], executor: Callable[[Dict[str, Any]], Dict[str, Any]]
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    runner = GoldenTestRunner(executor)
    results = [runner.run_test(t) for t in tests]

    failure_types: Dict[str, int] = {}
    for r in results:
        for f in r["failures"]:
            failure_types[f] = failure_types.get(f, 0) + 1

    count_structure = sum(1 for t in tests if t.get("expected", {}).get("structure"))
    count_metrics = sum(1 for t in tests if t.get("expected", {}).get("metrics"))
    total = len(results)

    judge_enabled_tests = [(t, r) for t, r in zip(tests, results) if (t.get("llm_judge") or {}).get("enabled")]
    judge_results = [r.get("llm_judge") for _, r in judge_enabled_tests if r.get("llm_judge")]
    count_judge_disagreements = sum(1 for j in judge_results if j.get("disagreement"))
    count_low_confidence = sum(1 for j in judge_results if j.get("confidence") == "low")

    decision_counts: Dict[str, int] = {}
    for r in results:
        d = r.get("decision", "PASS_NO_JUDGE")
        decision_counts[d] = decision_counts.get(d, 0) + 1
    for key in ("PASS", "SOFT_FAIL", "HARD_FAIL", "UNCERTAIN_PASS", "PASS_NO_JUDGE"):
        decision_counts.setdefault(key, 0)

    summary = {
        "total": total,
        "passed": sum(1 for r in results if r["passed"]),
        "failed": sum(1 for r in results if not r["passed"]),
        "failure_types": failure_types,
        "coverage": {
            "total_tests": total,
            "tests_with_structure": count_structure,
            "tests_with_metrics": count_metrics,
        },
        "judge": {
            "enabled_tests": len(judge_enabled_tests),
            "disagreements": count_judge_disagreements,
            "low_confidence": count_low_confidence,
        },
        "decisions": {
            "PASS": decision_counts.get("PASS", 0),
            "SOFT_FAIL": decision_counts.get("SOFT_FAIL", 0),
            "HARD_FAIL": decision_counts.get("HARD_FAIL", 0),
            "UNCERTAIN_PASS": decision_counts.get("UNCERTAIN_PASS", 0),
            "PASS_NO_JUDGE": decision_counts.get("PASS_NO_JUDGE", 0),
        },
    }

    _write_run_artifact(results, summary)
    return results, summary


def _write_run_artifact(results: List[Dict[str, Any]], summary: Dict[str, Any]) -> None:
    """Write run artifact to artifacts/golden_runs/run_<timestamp>.json."""
    out_dir = Path("artifacts/golden_runs")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"run_{ts}.json"
    try:
        payload = {"results": results, "summary": summary}
        with open(path, "w") as f:
            json.dump(payload, f, indent=2, default=str)
    except OSError:
        pass
