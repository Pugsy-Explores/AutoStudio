"""Phase 1 golden dataset tests — verify loader, assertions, runner."""

from pathlib import Path

import pytest

from tests.golden.adapter import to_evaluation_view
from tests.golden.assertions import check_invariants, evaluate_constraints
from tests.golden.loader import load, load_from_dir, load_from_file
from tests.golden.runner import GoldenTestRunner, run_suite


def test_load_sample():
    data_dir = Path(__file__).parent / "golden" / "data"
    tests = load_from_file(data_dir / "sample.json")
    assert len(tests) == 1
    assert tests[0]["id"] == "simple_case"
    assert tests[0]["input"]["instruction"] == "Where is load_config defined?"
    assert tests[0]["expected"]["metrics"]["answer_supported"] is True


def test_load_from_dir():
    data_dir = Path(__file__).parent / "golden" / "data"
    tests = load_from_dir(data_dir)
    assert len(tests) >= 1


def test_load_dispatches():
    data_dir = Path(__file__).parent / "golden" / "data"
    from_file = load(data_dir / "sample.json")
    from_dir = load(data_dir)
    assert len(from_file) == 1
    assert len(from_dir) >= 1


def test_evaluate_constraints_pass():
    result = {"metrics": {"answer_supported": True}}
    expected = {"metrics": {"answer_supported": True}}
    assert evaluate_constraints(result, expected) == []


def test_evaluate_constraints_fail():
    result = {"metrics": {"answer_supported": False}}
    expected = {"metrics": {"answer_supported": True}}
    failures = evaluate_constraints(result, expected)
    assert len(failures) == 1
    assert "answer_supported" in failures[0]


def test_evaluate_constraints_missing_metrics():
    result = {}
    expected = {"metrics": {"answer_supported": True}}
    failures = evaluate_constraints(result, expected)
    assert len(failures) >= 1


def test_evaluate_constraints_structure_max_steps():
    result = {"structure": {"max_steps": 3}}
    expected = {"structure": {"max_steps": 5}}
    assert evaluate_constraints(result, expected) == []


def test_evaluate_constraints_structure_max_steps_fail():
    result = {"structure": {"max_steps": 10}}
    expected = {"structure": {"max_steps": 5}}
    failures = evaluate_constraints(result, expected)
    assert len(failures) == 1
    assert "exceeds max" in failures[0]


def test_check_invariants_loop_detected():
    result = {"structure": {"has_loop": True}, "metrics": {}}
    assert "loop_detected" in check_invariants(result)


def test_check_invariants_unsafe_loop_termination():
    result = {"structure": {}, "metrics": {"termination_reason": "LOOP_PROTECTION"}}
    assert "unsafe_loop_termination" in check_invariants(result)


def test_check_invariants_pass():
    result = {"structure": {"has_loop": False}, "metrics": {"termination_reason": None}}
    assert check_invariants(result) == []


def test_evaluate_constraints_no_loops_pass():
    result = {"structure": {"has_loop": False}, "metrics": {"termination_reason": "SUCCESS"}}
    expected = {"structure": {"no_loops": True}}
    assert evaluate_constraints(result, expected) == []


def test_evaluate_constraints_no_loops_fail():
    result = {"structure": {"has_loop": True}, "metrics": {}}
    expected = {"structure": {"no_loops": True}}
    failures = evaluate_constraints(result, expected)
    assert len(failures) >= 1
    assert any("no_loops" in f or "loop" in f for f in failures)


def test_runner_run_test():
    def executor(input_: dict):
        return {"metrics": {"answer_supported": True}}

    tests = load(Path(__file__).parent / "golden" / "data" / "sample.json")
    runner = GoldenTestRunner(executor)
    out = runner.run_test(tests[0])
    assert out["id"] == "simple_case"
    assert out["passed"] is True
    assert out["failures"] == []
    assert "debug" in out
    assert out["debug"]["input"] == tests[0]["input"]
    assert out["debug"]["raw_output"]["metrics"]["answer_supported"] is True


def test_adapter_to_evaluation_view():
    raw = {"metrics": {"x": 1}, "extra": "ignored"}
    view = to_evaluation_view(raw)
    assert view["structure"] == {}
    assert view["metrics"] == {"x": 1}
    assert view["signals"] is raw


def test_pre_hook():
    def executor(input_: dict):
        return {"metrics": {"answer_supported": True}}

    def pre_hook(test):
        test = dict(test)
        test["input"] = {**test["input"], "hooked": True}
        return test

    tests = load(Path(__file__).parent / "golden" / "data" / "sample.json")
    runner = GoldenTestRunner(executor, pre_hook=pre_hook)
    out = runner.run_test(tests[0])
    assert out["debug"]["input"]["hooked"] is True
    assert out["passed"] is True


def test_post_hook():
    def executor(input_: dict):
        return {"metrics": {"answer_supported": False}}

    def post_hook(raw):
        return {**raw, "metrics": {"answer_supported": True}}

    tests = load(Path(__file__).parent / "golden" / "data" / "sample.json")
    runner = GoldenTestRunner(executor, post_hook=post_hook)
    out = runner.run_test(tests[0])
    assert out["passed"] is True


def test_evaluate_constraints_strict():
    result = {}
    expected = {"metrics": {"answer_supported": True}}
    failures = evaluate_constraints(result, expected, strict=True)
    assert any("strict" in f for f in failures)
    assert any("missing required key" in f for f in failures)


def test_run_suite_failure_types():
    def executor(input_: dict):
        return {"metrics": {"answer_supported": False}}

    tests = load(Path(__file__).parent / "golden" / "data" / "sample.json")
    results, summary = run_suite(tests, executor)
    assert summary["failed"] == 1
    assert "failure_types" in summary
    assert len(summary["failure_types"]) >= 1


def test_run_suite():
    def executor(input_: dict):
        return {"metrics": {"answer_supported": True}}

    tests = load(Path(__file__).parent / "golden" / "data" / "sample.json")
    results, summary = run_suite(tests, executor)
    assert summary["total"] == 1
    assert summary["passed"] == 1
    assert summary["failed"] == 0
    assert "failure_types" in summary
    assert summary["failure_types"] == {}  # no failures
    assert "coverage" in summary
    assert summary["coverage"]["total_tests"] == 1
    assert summary["coverage"]["tests_with_metrics"] == 1


def test_run_suite_failure_surface():
    def executor(input_: dict):
        return {"metrics": {"answer_supported": False}}

    tests = load(Path(__file__).parent / "golden" / "data" / "sample.json")
    runner = GoldenTestRunner(executor)
    results = [runner.run_test(t) for t in tests]
    assert len(results) == 1
    assert not results[0]["passed"]
    assert "failure_surface" in results[0]
    assert "metrics" in results[0]["failure_surface"]
    assert results[0]["failure_surface"]["metrics"]["answer_supported"] is False
