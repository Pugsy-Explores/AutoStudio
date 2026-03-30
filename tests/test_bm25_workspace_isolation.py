"""BM25 must rebuild when project_root changes; otherwise hits reference the first workspace only."""

from __future__ import annotations

from agent.retrieval import bm25_retriever as br


def test_search_bm25_rebuilds_when_project_root_changes(monkeypatch, tmp_path):
    br._reset_for_testing()
    roots: list[str] = []

    class _FakeBM25:
        def get_scores(self, _q):
            return [1.0]

    def fake_build(root: str | None) -> bool:
        roots.append(str(root))
        br._BM25_INDEX = _FakeBM25()
        br._REPO_SYMBOLS = [{"name": "fn", "file": f"{root}/x.py", "line": 1, "docstring": ""}]
        br._PROJECT_ROOT = str(root)
        return True

    monkeypatch.setattr(br, "build_bm25_index", fake_build)
    a = str((tmp_path / "ws_a").resolve())
    b = str((tmp_path / "ws_b").resolve())
    br.search_bm25("fn", a, top_k=3)
    br.search_bm25("fn", b, top_k=3)
    assert roots == [a, b]
