#!/usr/bin/env python3
"""Index all repos declared in EXPLORATION_TEST_REPOS (clone git URLs if missing).

Usage:
  python3 scripts/index_exploration_test_repos.py
  python3 scripts/index_exploration_test_repos.py --verbose

Requires network for first-time git clones. Paths are resolved from agent_v2.config only.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Index exploration test repos")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    os.environ.setdefault("SERENA_PROJECT_DIR", str(_REPO_ROOT))
    os.chdir(str(_REPO_ROOT))

    from agent_v2.exploration_test_repos import index_all_configured  # noqa: PLC0415

    results = index_all_configured(_REPO_ROOT, verbose=args.verbose)
    for name, db in results:
        print(f"  [{name}] -> {db}")
    print(f"Done: {len(results)} repo(s) indexed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
