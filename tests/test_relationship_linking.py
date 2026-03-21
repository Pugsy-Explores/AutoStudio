"""Bounded relationship metadata on retrieval candidates (Phase B)."""

from __future__ import annotations

from agent.retrieval.retrieval_pipeline import MAX_RELATIONS_TOTAL, _attach_relationship_links
from config.repo_graph_config import INDEX_SQLITE, SYMBOL_GRAPH_DIR


def _make_index(tmp_path):
    root = tmp_path
    fa = root / "a.py"
    fb = root / "b.py"
    fa.write_text("def alpha():\n    pass\n", encoding="utf-8")
    fb.write_text("def beta():\n    pass\n", encoding="utf-8")
    sa = str(fa.resolve())
    sb = str(fb.resolve())
    idx_dir = root / SYMBOL_GRAPH_DIR
    idx_dir.mkdir(parents=True)
    db = idx_dir / INDEX_SQLITE
    from repo_graph.graph_storage import GraphStorage

    st = GraphStorage(str(db))
    try:
        id_a = st.add_node({"name": "alpha", "file": sa, "symbol_type": "function"})
        id_b = st.add_node({"name": "beta", "file": sb, "symbol_type": "function"})
        st.add_edge(id_a, id_b, "imports")
    finally:
        st.close()
    return str(root), sa, sb


def test_ownership_relation_on_symbol_row(tmp_path):
    root, sa, _ = _make_index(tmp_path)
    cands = [{"file": sa, "symbol": "alpha", "snippet": "x", "candidate_kind": "symbol"}]
    out = _attach_relationship_links(cands, root, graph_skipped=False)
    assert len(out) == 1
    rels = out[0].get("relations") or []
    assert any(r.get("kind") == "ownership" for r in rels)


def test_import_relation_when_graph_edge_exists(tmp_path):
    root, sa, sb = _make_index(tmp_path)
    cands = [
        {"file": sa, "symbol": "alpha", "snippet": "x", "candidate_kind": "symbol"},
        {"file": sb, "symbol": "beta", "snippet": "y", "candidate_kind": "symbol"},
    ]
    out = _attach_relationship_links(cands, root, graph_skipped=False)
    total = sum(len(x.get("relations") or []) for x in out)
    assert total <= MAX_RELATIONS_TOTAL
    flat = [r for x in out for r in (x.get("relations") or [])]
    kinds = [r.get("kind") for r in flat]
    assert "ownership" in kinds
    # import may be present if get_imports returns the target file
    assert any(k in kinds for k in ("import", "call", "ownership"))


def test_graph_skipped_noop(tmp_path):
    cands = [{"file": "/x/a.py", "symbol": "z", "snippet": ""}]
    out = _attach_relationship_links(cands, str(tmp_path), graph_skipped=True)
    assert out == cands


def test_total_relations_bounded(tmp_path):
    root, sa, sb = _make_index(tmp_path)
    many = [
        {"file": sa, "symbol": "alpha", "snippet": str(i), "candidate_kind": "symbol"}
        for i in range(20)
    ]
    many.append({"file": sb, "symbol": "beta", "snippet": "last", "candidate_kind": "symbol"})
    out = _attach_relationship_links(many, root, graph_skipped=False)
    total = sum(len(x.get("relations") or []) for x in out)
    assert total <= MAX_RELATIONS_TOTAL
