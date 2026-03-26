"""Unit tests for exploration working memory (dedupe, merge, gap filter)."""

from __future__ import annotations

from agent_v2.exploration.exploration_working_memory import ExplorationWorkingMemory, file_symbol_key
from agent_v2.schemas.exploration import ExplorationCandidate


def test_file_symbol_key():
    assert file_symbol_key("/a/b.py", "Foo") == "/a/b.py::Foo"
    assert file_symbol_key("/a/b.py", None) == "/a/b.py::__file__"


def test_add_evidence_dedupe_merges_line_range():
    m = ExplorationWorkingMemory(min_confidence=0.35, max_evidence=6)
    m.add_evidence(
        "Foo",
        "/x.py",
        (10, 20),
        "first",
        confidence=0.85,
        source="analyzer",
        tier=0,
        tool_name="read_snippet",
    )
    m.add_evidence(
        "Foo",
        "/x.py",
        (15, 30),
        "second",
        confidence=0.7,
        source="analyzer",
        tier=0,
        tool_name="read_snippet",
    )
    snap = m.get_summary()
    assert len(snap["evidence"]) == 1
    ev = snap["evidence"][0]
    assert ev["line_range"]["start"] == 10
    assert ev["line_range"]["end"] == 30
    assert "first" in ev["summary"] or "second" in ev["summary"]


def test_gap_generic_skipped():
    m = ExplorationWorkingMemory()
    assert not m.add_gap("none", "need more context", confidence=0.8, source="analyzer")
    assert m.add_gap("caller", "missing caller path for FooBar", confidence=0.8, source="analyzer")
    assert len(m.get_summary()["gaps"]) == 1


def test_gap_duplicate_skipped():
    m = ExplorationWorkingMemory()
    assert m.add_gap("caller", "missing caller path", confidence=0.8, source="analyzer")
    assert not m.add_gap("caller", "missing caller path", confidence=0.8, source="analyzer")
    assert len(m.get_summary()["gaps"]) == 1


def test_relationship_dedupe():
    m = ExplorationWorkingMemory()
    m.add_relationship("a::X", "b::Y", "callers", confidence=0.9, source="expansion")
    m.add_relationship("a::X", "b::Y", "callers", confidence=0.9, source="expansion")
    assert len(m.get_summary()["relationships"]) == 1


def test_ingest_discovery_candidates():
    m = ExplorationWorkingMemory(min_confidence=0.35, max_evidence=6)
    c = ExplorationCandidate(
        symbol="Z",
        file_path="/z.py",
        snippet="hit",
        source="graph",
    )
    object.__setattr__(c, "_discovery_max_score", 0.9)
    m.ingest_discovery_candidates([c], limit=6)
    snap = m.get_summary()
    assert len(snap["evidence"]) == 1
    assert snap["evidence"][0]["tier"] == 2
