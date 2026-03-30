"""Optional multi-repository retrieval harness (clone + index git repos first).

Set ``AGENT_V2_RUN_EXTERNAL_REPO_RETRIEVAL_TESTS=1`` and
``AGENT_V2_APPEND_EXPLORATION_TEST_REPOS_TO_RETRIEVAL=1``, then index:

  python3 scripts/index_exploration_test_repos.py

Uses only ``run_retrieval_pipeline`` — not full exploration.
"""

from __future__ import annotations

import os
import warnings
from pathlib import Path

import pytest

from tests.retrieval.case_generation import build_multi_repo_eval_cases

_REPO_ROOT = Path(__file__).resolve().parents[2]

pytestmark = [pytest.mark.retrieval, pytest.mark.external_repo]

_RUN = os.environ.get("AGENT_V2_RUN_EXTERNAL_REPO_RETRIEVAL_TESTS", "").strip().lower() in (
    "1",
    "true",
    "yes",
)

if _RUN:
    os.environ.setdefault("AGENT_V2_APPEND_EXPLORATION_TEST_REPOS_TO_RETRIEVAL", "1")


@pytest.mark.skipif(not _RUN, reason="set AGENT_V2_RUN_EXTERNAL_REPO_RETRIEVAL_TESTS=1 to run")
@pytest.mark.parametrize(
    "case",
    build_multi_repo_eval_cases(_REPO_ROOT, max_per_repo=4, max_total=12),
    ids=lambda c: c.case_id,
)
def test_retrieval_multi_repo_mid_stage(retrieval_engine, case):
    """Cross-repo + ranking band + repo label (when candidate.repo is set)."""
    from agent_v2.schemas.exploration import QueryIntent
    import agent_v2.config as cfg

    intent = QueryIntent(
        symbols=[case.expected_symbol],
        keywords=case.keywords,
        intents=["find_definition"],
    )
    candidates = retrieval_engine.run_retrieval_pipeline(case.instruction, intent)

    limit = cfg.EXPLORATION_DISCOVERY_POST_RERANK_TOP_K
    assert len(candidates) <= limit

    all_paths = [c.file_path for c in candidates]
    found = any(case.expected_file_hint in p for p in all_paths)
    if not found:
        pytest.fail(f"[{case.case_id}] expected file hint not in candidates")

    rank = next(
        (i + 1 for i, p in enumerate(all_paths) if case.expected_file_hint in p),
        None,
    )
    assert rank is not None
    cap = case.rank_fail_after
    if rank > cap:
        pytest.fail(f"[{case.case_id}] rank {rank} > cap {cap}")
    if rank > 3 and rank <= cap:
        warnings.warn(
            f"[{case.case_id}] rank {rank} (within top-{cap}, not top-3)",
            UserWarning,
            stacklevel=1,
        )

    row = next((c for c in candidates if case.expected_file_hint in c.file_path), None)
    if row is not None and getattr(row, "repo", None) is not None:
        assert getattr(row, "repo", None) == case.repo, (
            f"repo label mismatch: got {getattr(row, 'repo', None)!r} expected {case.repo!r}"
        )

    for c in candidates:
        assert getattr(c, "snippet_summary", None)
        assert getattr(c, "symbols", None)
