"""Indexer path exclusions (junk dirs) for scan quality."""

from __future__ import annotations

from pathlib import Path

from repo_index.indexer import _relative_path_has_excluded_component, index_repo


def test_relative_path_excludes_pycache_and_symbol_graph(tmp_path: Path) -> None:
    root = tmp_path
    good = root / "pkg" / "good.py"
    bad = root / "pkg" / "__pycache__" / "bad.py"
    good.parent.mkdir(parents=True)
    bad.parent.mkdir(parents=True)
    good.write_text("x = 1\n", encoding="utf-8")
    bad.write_text("y = 2\n", encoding="utf-8")

    assert not _relative_path_has_excluded_component(good, root)
    assert _relative_path_has_excluded_component(bad, root)

    out = root / ".symbol_graph"
    symbols, _ = index_repo(str(root), output_dir=str(out))
    files = {s.get("file") for s in symbols}
    assert str(good.resolve()) in files
    assert str(bad.resolve()) not in files
    assert (out / "repo_map.json").exists()
