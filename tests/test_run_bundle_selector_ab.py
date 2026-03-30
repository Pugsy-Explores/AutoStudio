"""Unit tests for bundle selector A/B runner (filter logic, no full eval)."""

from __future__ import annotations

import argparse

import pytest

from tests.agent_eval.run_bundle_selector_ab import _get_allowlist
from tests.agent_eval.suites.search_stack import architecture_task_ids, selector_hard_task_ids


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--architecture-only", action="store_true", default=True)
    p.add_argument("--selector-hard-only", action="store_true")
    return p.parse_args(argv)


def test_get_allowlist_selector_hard_only():
    """--selector-hard-only filters to selector_hard_task_ids()."""
    args = _parse_args(["--selector-hard-only"])
    allowlist = _get_allowlist(args)
    assert allowlist is not None
    assert allowlist == selector_hard_task_ids()
    assert len(allowlist) == 6


def test_get_allowlist_architecture_only():
    """Default uses architecture_task_ids()."""
    args = _parse_args([])
    allowlist = _get_allowlist(args)
    assert allowlist is not None
    assert allowlist == architecture_task_ids()


def test_get_allowlist_selector_hard_overrides():
    """--selector-hard-only overrides architecture-only."""
    args = _parse_args(["--selector-hard-only", "--architecture-only"])
    allowlist = _get_allowlist(args)
    assert allowlist == selector_hard_task_ids()
