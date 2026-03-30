"""Deduplication preserves distinct rows (Phase 5)."""

from __future__ import annotations

from agent.retrieval.reranker.deduplicator import deduplicate_candidates


def test_dedupe_same_snippet_different_files_kept():
    a = {"file": "a.py", "symbol": "", "snippet": "identical"}
    b = {"file": "b.py", "symbol": "", "snippet": "identical"}
    out = deduplicate_candidates([a, b])
    assert len(out) == 2


def test_dedupe_same_snippet_same_file_different_symbol_kept():
    a = {"file": "a.py", "symbol": "X", "snippet": "identical"}
    b = {"file": "a.py", "symbol": "Y", "snippet": "identical"}
    out = deduplicate_candidates([a, b])
    assert len(out) == 2


def test_dedupe_identical_triple_collapses():
    a = {"file": "a.py", "symbol": "X", "snippet": "same", "candidate_kind": "symbol"}
    b = {"file": "a.py", "symbol": "X", "snippet": "same", "candidate_kind": "symbol"}
    out = deduplicate_candidates([a, b])
    assert len(out) == 1


def test_dedupe_same_snippet_different_kind_kept():
    a = {"file": "a.py", "symbol": "X", "snippet": "s", "candidate_kind": "symbol"}
    b = {"file": "a.py", "symbol": "X", "snippet": "s", "candidate_kind": "file"}
    out = deduplicate_candidates([a, b])
    assert len(out) == 2


def test_dedupe_deterministic_order():
    rows = [
        {"file": "a.py", "symbol": "", "snippet": "first"},
        {"file": "b.py", "symbol": "", "snippet": "second"},
    ]
    assert deduplicate_candidates(rows)[0]["file"] == "a.py"


def test_metadata_preserved_on_kept_row():
    a = {
        "file": "a.py",
        "symbol": "S",
        "snippet": "body",
        "retrieval_result_type": "symbol_body",
        "candidate_kind": "symbol",
    }
    out = deduplicate_candidates([a])
    assert out[0]["retrieval_result_type"] == "symbol_body"
