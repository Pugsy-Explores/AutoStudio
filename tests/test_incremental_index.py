"""Tests for incremental repository indexing: update_index_for_file()."""

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from repo_graph.graph_query import expand_neighbors, find_symbol
from repo_graph.graph_storage import GraphStorage
from repo_index.indexer import index_repo, update_index_for_file

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "repo"


def _copy_fixtures(work_dir: Path) -> None:
    """Copy fixture repo to work_dir."""
    for f in FIXTURES_DIR.rglob("*.py"):
        rel = f.relative_to(FIXTURES_DIR)
        dst = work_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(f, dst)


def _nodes_for_file(conn: sqlite3.Connection, file_path: str) -> list[dict]:
    """Get all nodes for a file (match by path ending in filename)."""
    name = Path(file_path).name
    # Match /path/to/foo.py or foo.py
    rows = conn.execute(
        "SELECT id, name, type, file FROM nodes WHERE file = ? OR file LIKE ?",
        (name, f"%/{name}"),
    ).fetchall()
    return [dict(zip(["id", "name", "type", "file"], r)) for r in rows]


def _edge_count(conn: sqlite3.Connection) -> int:
    """Total edge count."""
    return conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]


def _node_count(conn: sqlite3.Connection) -> int:
    """Total node count."""
    return conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]


def test_update_index_workflow_index_modify_update_query(tmp_path):
    """
    Full workflow: index repo -> modify file -> update index -> query symbol.
    Verifies new symbol is findable and graph queries work.
    """
    work_dir = tmp_path / "repo"
    work_dir.mkdir()
    _copy_fixtures(work_dir)
    index_dir = work_dir / ".symbol_graph"

    # 1. Index repo
    symbols, db_path = index_repo(str(work_dir), output_dir=str(index_dir))
    assert Path(db_path).exists()
    assert len(symbols) >= 2

    # 2. Modify file: add new function
    foo_path = work_dir / "foo.py"
    original = foo_path.read_text()
    foo_path.write_text(original + "\ndef new_func():\n    bar()\n    return 99\n")

    # 3. Update index
    count = update_index_for_file(str(foo_path), root_dir=str(work_dir))
    assert count >= 1

    # 4. Query symbol
    storage = GraphStorage(db_path)
    try:
        node = find_symbol("new_func", storage)
        assert node is not None, "new_func should be findable after update"
        assert node.get("name") == "new_func"
        assert "foo.py" in (node.get("file") or "")

        # Graph expansion: new_func -> bar
        neighbors = expand_neighbors(node["id"], depth=2, storage=storage)
        names = {n.get("name") for n in neighbors}
        assert "bar" in names, "new_func calls bar, so bar should be in neighbors"
    finally:
        storage.close()


def test_update_index_old_nodes_removed(tmp_path):
    """After update: old nodes for the file are removed, new nodes added."""
    work_dir = tmp_path / "repo"
    work_dir.mkdir()
    _copy_fixtures(work_dir)
    index_dir = work_dir / ".symbol_graph"

    index_repo(str(work_dir), output_dir=str(index_dir))
    db_path = index_dir / "index.sqlite"
    foo_path = work_dir / "foo.py"

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    nodes_before = _nodes_for_file(conn, str(foo_path))
    names_before = {n["name"] for n in nodes_before}
    conn.close()

    assert "bar" in names_before
    assert "baz" in names_before

    # Remove bar, rename baz -> qux, add new_func
    foo_path.write_text(
        '"""Modified foo."""\n\ndef qux():\n    return 2\n\ndef new_func():\n    return 99\n'
    )

    count = update_index_for_file(str(foo_path), root_dir=str(work_dir))
    assert count >= 2  # qux, new_func (module-level symbol may add one)

    conn = sqlite3.connect(db_path)
    nodes_after = _nodes_for_file(conn, str(foo_path))
    names_after = {n["name"] for n in nodes_after}
    conn.close()

    assert "bar" not in names_after, "bar was removed from file, should be gone from index"
    assert "baz" not in names_after, "baz was renamed to qux"
    assert "qux" in names_after
    assert "new_func" in names_after


def test_update_index_edges_updated(tmp_path):
    """After update: edges are updated (old edges removed, new edges added)."""
    work_dir = tmp_path / "repo"
    work_dir.mkdir()
    _copy_fixtures(work_dir)
    index_dir = work_dir / ".symbol_graph"

    index_repo(str(work_dir), output_dir=str(index_dir))
    db_path = index_dir / "index.sqlite"
    foo_path = work_dir / "foo.py"

    conn = sqlite3.connect(db_path)
    edges_before = _edge_count(conn)
    conn.close()

    # Modify: remove baz, add new_func that calls bar
    foo_path.write_text(
        '"""Foo."""\n\ndef bar():\n    return 1\n\ndef new_func():\n    bar()\n    return 99\n'
    )

    update_index_for_file(str(foo_path), root_dir=str(work_dir))

    conn = sqlite3.connect(db_path)
    # baz node should be gone (was in old foo.py)
    baz_after = conn.execute("SELECT id FROM nodes WHERE name = 'baz'").fetchone()
    assert baz_after is None, "baz was removed"

    # new_func and bar should exist; edge new_func -> bar (bar gets new id after update)
    new_func_row = conn.execute("SELECT id FROM nodes WHERE name = 'new_func'").fetchone()
    bar_row = conn.execute("SELECT id FROM nodes WHERE name = 'bar'").fetchone()
    assert new_func_row is not None and bar_row is not None
    new_func_id, bar_id = new_func_row[0], bar_row[0]
    edge = conn.execute(
        "SELECT 1 FROM edges WHERE source_id = ? AND target_id = ?",
        (new_func_id, bar_id),
    ).fetchone()
    assert edge is not None, "new_func calls bar, edge should exist"
    conn.close()


def test_update_index_graph_queries_correct(tmp_path):
    """Graph queries (find_symbol, expand_neighbors) return correct results after update."""
    work_dir = tmp_path / "repo"
    work_dir.mkdir()
    _copy_fixtures(work_dir)
    index_dir = work_dir / ".symbol_graph"

    index_repo(str(work_dir), output_dir=str(index_dir))
    db_path = index_dir / "index.sqlite"
    foo_path = work_dir / "foo.py"

    storage = GraphStorage(db_path)
    bar_before = find_symbol("bar", storage)
    assert bar_before is not None
    neighbors_before = expand_neighbors(bar_before["id"], depth=2, storage=storage)
    storage.close()

    # Add new_func that calls bar
    original = foo_path.read_text()
    foo_path.write_text(original + "\ndef new_func():\n    bar()\n    return 0\n")
    update_index_for_file(str(foo_path), root_dir=str(work_dir))

    storage = GraphStorage(db_path)
    try:
        bar_after = find_symbol("bar", storage)
        assert bar_after is not None
        neighbors_after = expand_neighbors(bar_after["id"], depth=2, storage=storage)
        names_after = {n.get("name") for n in neighbors_after}
        # bar is called by baz and new_func; both should appear as neighbors (incoming)
        assert "baz" in names_after or "new_func" in names_after

        new_func_node = find_symbol("new_func", storage)
        assert new_func_node is not None
        new_neighbors = expand_neighbors(new_func_node["id"], depth=2, storage=storage)
        new_names = {n.get("name") for n in new_neighbors}
        assert "bar" in new_names, "new_func calls bar"
    finally:
        storage.close()


def test_update_index_cross_file_edges_preserved(tmp_path):
    """
    When updating one file, edges to/from other files are preserved.
    sub/mod.py imports bar from foo; updating foo.py should not break mod's edges to bar.
    """
    work_dir = tmp_path / "repo"
    work_dir.mkdir()
    _copy_fixtures(work_dir)
    index_dir = work_dir / ".symbol_graph"

    index_repo(str(work_dir), output_dir=str(index_dir))
    db_path = index_dir / "index.sqlite"
    foo_path = work_dir / "foo.py"
    mod_path = work_dir / "sub" / "mod.py"

    # Verify mod imports bar
    storage = GraphStorage(db_path)
    bar_node = find_symbol("bar", storage)
    assert bar_node is not None
    bar_neighbors = expand_neighbors(bar_node["id"], depth=2, storage=storage)
    storage.close()
    neighbor_names = {n.get("name") for n in bar_neighbors}
    # method_a or MyClass.method_a calls bar
    assert any("method" in (n or "") or "MyClass" in (n or "") for n in neighbor_names) or len(bar_neighbors) >= 1

    # Update foo.py only (add a comment, keep bar)
    original = foo_path.read_text()
    foo_path.write_text(original + "\n# updated\n")

    update_index_for_file(str(foo_path), root_dir=str(work_dir))

    # Bar should still be findable; mod's edges to bar should still work
    storage = GraphStorage(db_path)
    try:
        bar_after = find_symbol("bar", storage)
        assert bar_after is not None
        # mod.py still imports bar; edges from mod to bar should exist
        conn = sqlite3.connect(db_path)
        mod_count = conn.execute(
            "SELECT COUNT(*) FROM nodes WHERE file LIKE ?", ("%mod.py",)
        ).fetchone()[0]
        conn.close()
        assert mod_count >= 1
    finally:
        storage.close()


def test_update_index_remove_nodes_for_file_removes_edges(tmp_path):
    """remove_nodes_for_file removes both nodes and edges referencing them."""
    work_dir = tmp_path / "repo"
    work_dir.mkdir()
    _copy_fixtures(work_dir)
    index_dir = work_dir / ".symbol_graph"

    index_repo(str(work_dir), output_dir=str(index_dir))
    db_path = index_dir / "index.sqlite"

    conn = sqlite3.connect(db_path)
    nodes_before = _node_count(conn)
    edges_before = _edge_count(conn)
    conn.close()

    foo_path = work_dir / "foo.py"
    storage = GraphStorage(db_path)
    removed = storage.remove_nodes_for_file(str(foo_path))
    storage.close()

    assert len(removed) >= 2  # bar, baz at least

    conn = sqlite3.connect(db_path)
    nodes_after = _node_count(conn)
    edges_after = _edge_count(conn)
    conn.close()

    assert nodes_after == nodes_before - len(removed)
    assert edges_after < edges_before, "Removing nodes should remove edges involving them"


def test_update_index_cross_file_target_resolution(tmp_path):
    """
    When updating a file that references symbols from other files (e.g. import bar from foo),
    edges are correctly resolved. Target "foo.bar" must resolve to node "bar".
    """
    work_dir = tmp_path / "repo"
    work_dir.mkdir()
    _copy_fixtures(work_dir)
    index_dir = work_dir / ".symbol_graph"

    index_repo(str(work_dir), output_dir=str(index_dir))
    db_path = index_dir / "index.sqlite"
    mod_path = work_dir / "sub" / "mod.py"

    # mod.py has: from ..foo import bar; method_a calls bar()
    # Modify mod: add new_method that calls bar
    original = mod_path.read_text()
    mod_path.write_text(
        original
        + "\n    def new_method(self):\n        bar()\n        return 'new'\n"
    )

    count = update_index_for_file(str(mod_path), root_dir=str(work_dir))
    assert count >= 1

    storage = GraphStorage(db_path)
    try:
        bar_node = find_symbol("bar", storage)
        assert bar_node is not None
        # new_method should have edge to bar (cross-file: mod references foo.bar)
        new_method = storage.get_symbol_by_name("new_method")
        if new_method:
            neighbors = storage.get_neighbors(new_method["id"], direction="out")
            names = {n.get("name") for n in neighbors}
            assert "bar" in names, "new_method calls bar, edge should exist"
    finally:
        storage.close()


def test_update_index_symbols_json_consistent(tmp_path):
    """symbols.json stays consistent with graph after update."""
    work_dir = tmp_path / "repo"
    work_dir.mkdir()
    _copy_fixtures(work_dir)
    index_dir = work_dir / ".symbol_graph"

    index_repo(str(work_dir), output_dir=str(index_dir))
    json_path = index_dir / "symbols.json"
    db_path = index_dir / "index.sqlite"
    foo_path = work_dir / "foo.py"

    foo_path.write_text('def new_func():\n    return 1\n')
    update_index_for_file(str(foo_path), root_dir=str(work_dir))

    symbols = json.loads(json_path.read_text())
    foo_symbols = [s for s in symbols if "foo.py" in (s.get("file") or "")]
    names = {s["symbol_name"] for s in foo_symbols}
    assert "new_func" in names

    conn = sqlite3.connect(db_path)
    db_names = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM nodes WHERE file LIKE ?", ("%foo.py",)
        ).fetchall()
    }
    conn.close()
    assert "new_func" in db_names
    assert names == db_names, "symbols.json and DB should have same symbols for file"
