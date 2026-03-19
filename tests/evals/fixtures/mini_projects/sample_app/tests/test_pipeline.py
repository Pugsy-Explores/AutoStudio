"""Tests for sample_app.pipeline."""

from sample_app.pipeline import add, multiply, run


def test_add():
    assert add(1, 2) == 3


def test_multiply_wrong_on_purpose():
    # Stage 12 fixture: fails until multiply() is fixed
    assert multiply(2, 3) == 6


def test_run_smoke():
    assert run(0) == 2
