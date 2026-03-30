"""Golden pipeline suite — end-to-end pipeline validation."""

from pathlib import Path

import pytest

from tests.golden.executors.pipeline_executor import run_pipeline
from tests.golden.loader import load
from tests.golden.runner import run_suite


@pytest.mark.slow
def test_pipeline_suite():
    tests = load(Path(__file__).parent / "data" / "pipeline")
    results, summary = run_suite(tests, run_pipeline)
    assert summary["failed"] == 0, summary.get("failure_types", {})
