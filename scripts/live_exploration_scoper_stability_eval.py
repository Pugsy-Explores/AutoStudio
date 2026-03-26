#!/usr/bin/env python3
"""
Companion live stability evaluation for ExplorationScoper only.

Runs repeated live scoper trials per case and prints compact per-case
stability profiles (index-set variance + pairwise stability matrix).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agent.models.model_client import call_reasoning_model
from agent.models.model_config import get_prompt_model_name_for_task
from agent_v2.exploration.exploration_scoper import ExplorationScoper
from agent_v2.exploration.exploration_task_names import EXPLORATION_TASK_SCOPER

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from live_exploration_scoper_eval import (  # type: ignore
    _build_bm25,
    _build_candidate_pool,
    _build_cases,
    _extract_signals,
    _iter_python_files,
    _selected_dedupe_indices,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _pairwise_stability_matrix(index_sets: list[list[int]]) -> list[list[float]]:
    sets = [set(x) for x in index_sets]
    out: list[list[float]] = []
    for a in sets:
        row: list[float] = []
        for b in sets:
            if not a and not b:
                row.append(1.0)
                continue
            den = len(a | b)
            row.append(round((len(a & b) / den) if den else 1.0, 4))
        out.append(row)
    return out


def _index_set_variance(index_sets: list[list[int]]) -> dict:
    frozen = [tuple(sorted(x)) for x in index_sets]
    unique = sorted(set(frozen))
    return {
        "unique_index_sets": len(unique),
        "set_frequency": {str(list(u)): frozen.count(u) for u in unique},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=3, help="Live scoper trials per case.")
    ap.add_argument("--max-cases", type=int, default=8, help="Max number of scoper cases.")
    args = ap.parse_args()

    py_files = _iter_python_files()
    signals = _extract_signals(py_files)
    docs, _tokens, bm25 = _build_bm25(py_files)
    cases = _build_cases(signals)[: max(1, int(args.max_cases))]

    scoper = ExplorationScoper(
        llm_generate=lambda prompt: call_reasoning_model(prompt, task_name=EXPLORATION_TASK_SCOPER),
        model_name=get_prompt_model_name_for_task(EXPLORATION_TASK_SCOPER),
    )

    print("# Live ExplorationScoper Stability Evaluation")
    print(f"# Project root: {PROJECT_ROOT}")
    print(f"# Cases: {len(cases)}")
    print(f"# Trials per case: {max(1, int(args.trials))}")

    stable_cases = 0
    for case in cases:
        candidates = _build_candidate_pool(case, docs, bm25)
        trial_indices: list[list[int]] = []
        for _ in range(max(1, int(args.trials))):
            selected = scoper.scope(case.instruction, candidates)
            trial_indices.append(_selected_dedupe_indices(scoper, candidates, selected))

        fully_stable = len({tuple(sorted(x)) for x in trial_indices}) == 1
        if fully_stable:
            stable_cases += 1

        print("=" * 100)
        print(f"BUCKET: {case.bucket}")
        print(f"INSTRUCTION: {case.instruction}")
        print("TRIAL_SELECTED_INDICES:")
        print(json.dumps(trial_indices, ensure_ascii=False))
        print("STABILITY_MATRIX:")
        print(json.dumps(_pairwise_stability_matrix(trial_indices), ensure_ascii=False))
        print("INDEX_SET_VARIANCE:")
        print(json.dumps(_index_set_variance(trial_indices), ensure_ascii=False))
        print(
            "STABILITY_PROFILE:"
            + json.dumps(
                {
                    "fully_stable": fully_stable,
                    "trials": len(trial_indices),
                },
                ensure_ascii=False,
            )
        )

    print("=" * 100)
    print(
        json.dumps(
            {
                "summary": {
                    "total_cases": len(cases),
                    "stable_cases": stable_cases,
                }
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

