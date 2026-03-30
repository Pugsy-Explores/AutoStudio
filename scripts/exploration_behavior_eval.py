#!/usr/bin/env python3
"""
Trace-based exploration behavior harness (expand/refine/memory + 3-layer graders).

Usage:
  python3 scripts/exploration_behavior_eval.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from agent_v2.exploration.exploration_behavior_eval_harness import run_eval_suite
from tests.fixtures.exploration_behavior_eval_cases import build_eval_suites


def main() -> int:
    suites = build_eval_suites()
    all_cases = []
    for v in suites.values():
        all_cases.extend(v)

    out = run_eval_suite(all_cases)
    print(json.dumps(out, indent=2, ensure_ascii=False))
    if not all(row["final_case_pass"] for row in out["cases"]):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
