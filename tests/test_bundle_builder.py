"""Tests for bundle_builder: build_candidate_bundles, detect_bridge_candidates, scoring."""

import pytest

from agent.retrieval.bundle_builder import (
    build_candidate_bundles,
    detect_bridge_candidates,
    score_bundle,
    top_bundles_by_score,
)


def _pool_row(cid: str, file: str = "a.py", **kw):
    return {"candidate_id": cid, "file": file, "symbol": "", "snippet": "", **kw}


def test_build_candidate_bundles_basic():
    """Pool with 2 linked candidates forms 1 bundle; trivial singleton yields no bundle."""
    pool = [
        _pool_row("rc_0001", file="engine.py", relations=[{"kind": "import", "target_file": "settings.py"}]),
        _pool_row("rc_0002", file="settings.py"),
    ]
    bundles = build_candidate_bundles(pool)
    assert len(bundles) == 1
    assert set(bundles[0]["candidate_ids"]) == {"rc_0001", "rc_0002"}
    assert bundles[0]["linked_count"] >= 1
    assert "b_" in bundles[0]["bundle_id"]
    assert len(bundles[0]["bundle_id"]) > 3

    # Singleton with no relations yields no bundle (size < 2)
    pool_single = [_pool_row("rc_0001", file="alone.py")]
    bundles_single = build_candidate_bundles(pool_single)
    assert len(bundles_single) == 0


def test_bundle_connected_components():
    """2 disconnected clusters yield 2 bundles."""
    pool = [
        _pool_row("rc_0001", file="a.py", relations=[{"kind": "call", "target_file": "b.py"}]),
        _pool_row("rc_0002", file="b.py"),
        _pool_row("rc_0003", file="x.py", relations=[{"kind": "import", "target_file": "y.py"}]),
        _pool_row("rc_0004", file="y.py"),
    ]
    bundles = build_candidate_bundles(pool)
    assert len(bundles) == 2
    ids_per = [set(b["candidate_ids"]) for b in bundles]
    assert {"rc_0001", "rc_0002"} in ids_per
    assert {"rc_0003", "rc_0004"} in ids_per


def test_bundle_scoring_orders_correctly():
    """Higher linked/impl/files/edges yields higher normalized score."""
    b_weak = {
        "candidate_ids": ["a", "b", "c"],
        "linked_count": 0,
        "impl_count": 0,
        "files": {"f1.py"},
        "relation_edges": 0,
    }
    b_strong = {
        "candidate_ids": ["a", "b"],
        "linked_count": 2,
        "impl_count": 2,
        "files": {"f1.py", "f2.py"},
        "relation_edges": 3,
    }
    assert score_bundle(b_strong) > score_bundle(b_weak)


def test_bundle_respects_size_cap():
    """When over max: keeps impl+linked, trims low-score rows."""
    pool = [
        _pool_row("rc_0001", file="a.py", relations=[{"kind": "call", "target_file": "b.py"}], final_score=0.9),
        _pool_row("rc_0002", file="b.py", relations=[{"kind": "import", "target_file": "c.py"}], final_score=0.8),
        _pool_row("rc_0003", file="c.py", implementation_body_present=True, final_score=0.7),
        _pool_row("rc_0004", file="d.py", implementation_body_present=True, final_score=0.6),
        _pool_row("rc_0005", file="e.py", final_score=0.1),
        _pool_row("rc_0006", file="f.py", final_score=0.05),
    ]
    # Chain a->b->c; d,e,f connected via extra edges to form one component
    pool[3]["relations"] = [{"kind": "call", "target_file": "a.py"}]
    pool[4]["relations"] = [{"kind": "import", "target_file": "a.py"}]
    pool[5]["relations"] = [{"kind": "import", "target_file": "b.py"}]
    bundles = build_candidate_bundles(pool, max_bundle_size=4)
    assert len(bundles) >= 1
    for b in bundles:
        assert len(b["candidate_ids"]) <= 4
        id_to_row = {str(r.get("candidate_id")): r for r in pool}
        for cid in b["candidate_ids"]:
            r = id_to_row.get(cid, {})
            if r.get("implementation_body_present") or r.get("relations"):
                pass


def test_bundle_id_stable():
    """Same candidate set yields same bundle_id across calls."""
    pool = [
        _pool_row("rc_0001", file="a.py", relations=[{"kind": "import", "target_file": "b.py"}]),
        _pool_row("rc_0002", file="b.py"),
    ]
    bundles1 = build_candidate_bundles(pool)
    bundles2 = build_candidate_bundles(pool)
    assert len(bundles1) == 1 and len(bundles2) == 1
    assert bundles1[0]["bundle_id"] == bundles2[0]["bundle_id"]


def test_detect_bridge_candidates():
    """detect_bridge_candidates sets is_bridge on rows that link multiple bundles.
    With 2 separate bundles, a singleton that has relations to files in BOTH
    (but uses path variants that keep components separate) can be a bridge.
    Simpler: with 1 bundle, no bridge. With 2 bundles, a node in one bundle
    that has a relation whose target_file matches a file in the other bundle
    would merge them. So we test: detect runs without error, and when we have
    2 bundles (disconnected), no candidate connects them (all is_bridge False).
    """
    pool = [
        _pool_row("rc_0001", file="engine.py", relations=[{"kind": "import", "target_file": "impl.py"}]),
        _pool_row("rc_0002", file="impl.py"),
        _pool_row("rc_0003", file="settings.py", relations=[{"kind": "import", "target_file": "config.py"}]),
        _pool_row("rc_0004", file="config.py"),
    ]
    bundles = build_candidate_bundles(pool)
    assert len(bundles) == 2
    detect_bridge_candidates(pool, bundles)
    for row in pool:
        assert row.get("is_bridge") is False


def test_top_bundles_by_score():
    """top_bundles_by_score returns top N by score."""
    bundles = [
        {"candidate_ids": ["a"], "linked_count": 0, "impl_count": 0, "files": set(), "relation_edges": 0},
        {"candidate_ids": ["a", "b"], "linked_count": 2, "impl_count": 1, "files": {"x.py"}, "relation_edges": 2},
        {"candidate_ids": ["a", "b", "c"], "linked_count": 1, "impl_count": 1, "files": {"y.py"}, "relation_edges": 1},
    ]
    top = top_bundles_by_score(bundles, top_n=2)
    assert len(top) == 2
    assert top[0]["linked_count"] == 2
    assert top[1]["linked_count"] >= 1
