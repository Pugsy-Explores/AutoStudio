#!/usr/bin/env python3
"""Clone and index repos listed in ``tests/retrieval/pattern_sources.json``.

Usage:
  python3 scripts/index_pattern_coverage_repos.py
  python3 scripts/index_pattern_coverage_repos.py -v

Requires network on first clone.  Output ``.symbol_graph`` under each clone root.
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
    parser = argparse.ArgumentParser(description="Index pattern coverage retrieval repos")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    os.environ.setdefault("SERENA_PROJECT_DIR", str(_REPO_ROOT))
    os.chdir(str(_REPO_ROOT))

    from tests.retrieval.pattern_coverage import index_pattern_repos  # noqa: PLC0415

    results = index_pattern_repos(_REPO_ROOT, verbose=args.verbose)
    for name, db in results:
        print(f"  [{name}] -> {db}")
    print(f"Done: {len(results)} repo(s) indexed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
