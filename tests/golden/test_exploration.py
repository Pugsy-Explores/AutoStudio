"""Golden exploration suite — component-level exploration validation."""

from pathlib import Path

import pytest

from tests.golden.executors.exploration_executor import run_exploration
from tests.golden.loader import load
from tests.golden.runner import GoldenTestRunner


@pytest.mark.slow
def test_exploration_suite():
    tests = load(Path(__file__).parent / "data" / "exploration")
    runner = GoldenTestRunner(run_exploration)
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
