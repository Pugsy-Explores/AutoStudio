"""
Run the EXPLAIN injected-retrieval contract suite (pytest).

This is the agent_eval entrypoint for the same tests as:
  tests/test_explain_inject_retrieval.py
  tests/test_explain_inject_integration.py

Examples:
  python3 -m tests.agent_eval.run_explain_inject
  python3 -m tests.agent_eval.run_explain_inject -- -q -k asymmetry
"""

from __future__ import annotations

import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    repo_root = Path(__file__).resolve().parents[2]
    tests = [
        str(repo_root / "tests" / "test_explain_substantive_context.py"),
        str(repo_root / "tests" / "test_explain_inject_retrieval.py"),
        str(repo_root / "tests" / "test_explain_inject_integration.py"),
    ]
    import pytest

    args = list(argv) if argv is not None else sys.argv[1:]
    if args and args[0] == "--":
        args = args[1:]
    return pytest.main([*tests, "-v", *args])


if __name__ == "__main__":
    raise SystemExit(main())
