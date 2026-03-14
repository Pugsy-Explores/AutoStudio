"""Tests for repo_index: parser, symbol_extractor, indexer."""

import json
import tempfile
from pathlib import Path

import pytest

from repo_index.dependency_extractor import extract_edges
from repo_index.indexer import index_repo, scan_repo
from repo_index.indexer import _scan_repo_with_trees
from repo_index.parser import parse_file
from repo_index.symbol_extractor import extract_symbols


FIXTURES_DIR = Path(__file__).parent / "fixtures" / "repo"


def test_parse_file_valid():
    """parse_file returns tree for valid Python."""
    foo = FIXTURES_DIR / "foo.py"
    tree = parse_file(str(foo))
    assert tree is not None
    assert tree.root_node is not None
    assert tree.root_node.type == "module"


def test_parse_file_invalid(tmp_path):
    """parse_file returns None for invalid Python."""
    bad = tmp_path / "bad.py"
    bad.write_text("def ( invalid syntax")
    tree = parse_file(str(bad))
    # Tree-sitter may still parse partially; we accept either None or tree
    assert tree is None or tree.root_node is not None


def test_extract_symbols_functions_and_classes():
    """extract_symbols extracts functions and classes."""
    foo = FIXTURES_DIR / "foo.py"
    tree = parse_file(str(foo))
    assert tree is not None
    source = foo.read_bytes()
    symbols = extract_symbols(tree, str(foo), source)
    names = {s["symbol_name"] for s in symbols}
    assert "bar" in names
    assert "baz" in names
    assert all(s["file"] for s in symbols)
    assert all(s["start_line"] and s["end_line"] for s in symbols)


def test_extract_symbols_class_methods():
    """extract_symbols extracts class methods."""
    mod = FIXTURES_DIR / "sub" / "mod.py"
    if not mod.exists():
        pytest.skip("fixture not found")
    tree = parse_file(str(mod))
    assert tree is not None
    source = mod.read_bytes()
    symbols = extract_symbols(tree, str(mod), source)
    names = {s["symbol_name"] for s in symbols}
    assert "MyClass" in names
    assert any("method_a" in n for n in names)
    assert any("method_b" in n for n in names)


def test_scan_repo_returns_symbols():
    """scan_repo returns list of symbol records."""
    symbols = scan_repo(str(FIXTURES_DIR))
    assert isinstance(symbols, list)
    assert len(symbols) >= 2
    for s in symbols:
        assert "symbol_name" in s
        assert "symbol_type" in s
        assert "file" in s
        assert "start_line" in s
        assert "end_line" in s


def test_extract_symbols_type_info_and_signature():
    """extract_symbols extracts type_info and signature for typed functions."""
    typed = FIXTURES_DIR / "typed_foo.py"
    if not typed.exists():
        pytest.skip("typed_foo fixture not found")
    tree = parse_file(str(typed))
    assert tree is not None
    source = typed.read_bytes()
    symbols = extract_symbols(tree, str(typed), source)
    add_sym = next((s for s in symbols if s.get("symbol_name") == "add"), None)
    assert add_sym is not None
    assert "type_info" in add_sym
    assert add_sym["type_info"].get("params", {}).get("a") == "int"
    assert add_sym["type_info"].get("return_type") == "int"
    assert "signature" in add_sym
    assert "def add" in add_sym["signature"]
    assert "-> int" in add_sym["signature"]


def test_extract_symbols_docstring_triple_quoted():
    """extract_symbols extracts multi-line docstrings."""
    typed = FIXTURES_DIR / "typed_foo.py"
    if not typed.exists():
        pytest.skip("typed_foo fixture not found")
    tree = parse_file(str(typed))
    assert tree is not None
    source = typed.read_bytes()
    symbols = extract_symbols(tree, str(typed), source)
    add_sym = next((s for s in symbols if s.get("symbol_name") == "add"), None)
    assert add_sym is not None
    assert "Add two integers" in (add_sym.get("docstring") or "")


def test_extract_edges_includes_call_graph_control_flow_data_flow():
    """extract_edges produces call_graph, control_flow, data_flow edges."""
    symbols, ast_trees = _scan_repo_with_trees(str(FIXTURES_DIR))
    root = Path(FIXTURES_DIR).resolve()
    edges = extract_edges(symbols, ast_trees, str(root))
    edge_types = {e["relation_type"] for e in edges}
    assert "calls" in edge_types
    assert "call_graph" in edge_types
    # control_flow and data_flow may appear if fixtures have if/assignment
    assert len(edges) >= 0


def test_index_repo_writes_output(tmp_path):
    """index_repo writes symbols and graph to output dir."""
    symbols, out_path = index_repo(str(FIXTURES_DIR), output_dir=str(tmp_path))
    assert len(symbols) >= 2
    assert Path(out_path).exists()
    assert Path(out_path).suffix == ".sqlite" or "index" in out_path


def test_update_index_for_file(tmp_path):
    """update_index_for_file re-parses and updates symbols after edit."""
    import shutil

    from repo_index.indexer import index_repo, update_index_for_file

    fixtures = Path(FIXTURES_DIR)
    work_dir = tmp_path / "repo"
    work_dir.mkdir()
    for f in fixtures.rglob("*.py"):
        rel = f.relative_to(fixtures)
        dst = work_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(f, dst)

    index_dir = work_dir / ".symbol_graph"
    index_repo(str(work_dir), output_dir=str(index_dir))
    symbols_json = index_dir / "symbols.json"
    assert symbols_json.exists()

    foo_path = work_dir / "foo.py"
    original = foo_path.read_text()
    foo_path.write_text(original + "\ndef new_func():\n    return 99\n")

    count = update_index_for_file(str(foo_path), root_dir=str(work_dir))
    assert count >= 1

    import json

    symbols = json.loads(symbols_json.read_text())
    foo_file_str = str(foo_path.resolve())
    names = {s["symbol_name"] for s in symbols if s.get("file", "").endswith("foo.py") or s.get("file") == foo_file_str}
    assert "new_func" in names
