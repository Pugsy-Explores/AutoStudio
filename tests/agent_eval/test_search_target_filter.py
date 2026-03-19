"""Unit tests for Stage 13 search target filtering."""

from __future__ import annotations

from pathlib import Path

from agent.retrieval.search_target_filter import filter_and_rank_search_results


def test_filter_drops_symbol_graph_and_directories(tmp_path: Path):
    d = tmp_path / "pkg"
    d.mkdir()
    py = d / "mod.py"
    py.write_text("x = 1\n", encoding="utf-8")
    sym = tmp_path / ".symbol_graph"
    sym.mkdir()

    results = [
        {"file": str(sym), "symbol": "", "snippet": "idx"},
        {"file": str(d), "symbol": "", "snippet": "dir"},
        {"file": str(py), "symbol": "x", "snippet": "x = 1"},
    ]
    out = filter_and_rank_search_results(results, "pkg mod", str(tmp_path))
    assert len(out) == 1
    assert Path(out[0]["file"]) == py.resolve()


def test_filter_prefers_py_over_readme(tmp_path: Path):
    readme = tmp_path / "README.md"
    readme.write_text("# hi", encoding="utf-8")
    py = tmp_path / "src" / "calc.py"
    py.parent.mkdir(parents=True, exist_ok=True)
    py.write_text("def f():\n    pass\n", encoding="utf-8")

    results = [
        {"file": str(readme), "snippet": "hi", "score": 0.9},
        {"file": str(py), "snippet": "def f", "score": 0.5},
    ]
    out = filter_and_rank_search_results(results, "calc", str(tmp_path))
    assert Path(out[0]["file"]) == py.resolve()
