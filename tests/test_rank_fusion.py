"""Tests for agent/retrieval/rank_fusion.reciprocal_rank_fusion."""

import pytest

from agent.retrieval.rank_fusion import RRF_K, reciprocal_rank_fusion


def _doc(file: str, symbol: str, line: int = 0) -> dict:
    return {"file": file, "symbol": symbol, "line": line}


def test_document_in_multiple_lists_ranks_higher():
    """Document appearing in 2 lists ranks higher than one in 1 list."""
    doc_a = _doc("a.py", "foo", 1)
    doc_b = _doc("b.py", "bar", 2)
    doc_c = _doc("c.py", "baz", 3)

    list1 = [doc_a, doc_b]
    list2 = [doc_a, doc_c]
    list3 = [doc_b]

    result = reciprocal_rank_fusion([list1, list2, list3], k=60, top_n=10)
    assert len(result) >= 2
    assert result[0]["symbol"] == "foo"
    assert result[0]["file"] == "a.py"


def test_document_in_all_lists_ranks_highest():
    """Document appearing in all 3 lists ranks highest."""
    doc_a = _doc("a.py", "top", 1)
    doc_b = _doc("b.py", "mid", 2)
    doc_c = _doc("c.py", "low", 3)

    list1 = [doc_a, doc_b, doc_c]
    list2 = [doc_a, doc_c, doc_b]
    list3 = [doc_a, doc_b, doc_c]

    result = reciprocal_rank_fusion([list1, list2, list3], k=60, top_n=10)
    assert result[0]["symbol"] == "top"
    assert result[0]["file"] == "a.py"


def test_top_n_respected():
    """top_n slicing is respected."""
    lists = [
        [_doc(f"f{i}.py", f"s{i}", i) for i in range(50)],
    ]
    result = reciprocal_rank_fusion(lists, k=60, top_n=10)
    assert len(result) == 10


def test_deduplication_by_key():
    """Same (file, symbol, line) across lists gets score summed, not duplicated."""
    doc = _doc("a.py", "foo", 1)
    list1 = [doc]
    list2 = [dict(doc)]
    result = reciprocal_rank_fusion([list1, list2], k=60, top_n=10)
    assert len(result) == 1
    assert result[0]["symbol"] == "foo"


def test_empty_list_returns_empty():
    """Empty list input returns []."""
    assert reciprocal_rank_fusion([]) == []
    assert reciprocal_rank_fusion([[]]) == []


def test_single_list_degrades_gracefully():
    """Single-list input returns sorted by rank (best first)."""
    docs = [_doc("a.py", "a", 1), _doc("b.py", "b", 2), _doc("c.py", "c", 3)]
    result = reciprocal_rank_fusion([docs], k=60, top_n=10)
    assert len(result) == 3
    assert result[0]["symbol"] == "a"
    assert result[1]["symbol"] == "b"
    assert result[2]["symbol"] == "c"


def test_rrf_k_constant():
    """RRF_K is 60."""
    assert RRF_K == 60
