"""Golden selector suite — component-level selector validation."""

from pathlib import Path

import pytest

from tests.golden.executors.selector_executor import run_selector
from tests.golden.loader import load
from tests.golden.runner import GoldenTestRunner


@pytest.mark.slow
def test_selector_suite(indexed_autostudio):
    project_root, _ = indexed_autostudio

    def pre_hook(test):
        t = dict(test)
        t["input"] = {**t["input"], "project_root": project_root}
        return t

    tests = load(Path(__file__).parent / "data" / "selector")
    runner = GoldenTestRunner(run_selector, pre_hook=pre_hook)
    results = [runner.run_test(t) for t in tests]
    summary = {
        "total": len(results),
        "passed": sum(1 for r in results if r["passed"]),
        "failed": sum(1 for r in results if not r["passed"]),
        "failure_types": {},
    }
    for r in results:
        for f in r["failures"]:
            summary["failure_types"][f] = summary["failure_types"].get(f, 0) + 1
    assert summary["failed"] == 0, summary.get("failure_types", {})
