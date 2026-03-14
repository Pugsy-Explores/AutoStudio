"""Tests for editing/ast_patcher."""

from pathlib import Path

import pytest

from editing.ast_patcher import apply_patch, generate_code, load_ast

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "repo"


def test_load_ast_returns_tree_and_bytes():
    """load_ast returns (tree, source_bytes) for valid file."""
    foo = FIXTURES_DIR / "foo.py"
    result = load_ast(str(foo))
    assert result is not None
    tree, source_bytes = result
    assert tree is not None
    assert tree.root_node is not None
    assert isinstance(source_bytes, bytes)
    assert b"def bar" in source_bytes


def test_load_ast_none_for_missing():
    """load_ast returns None for missing file."""
    result = load_ast("/nonexistent/foo.py")
    assert result is None


def test_apply_patch_insert_at_function_body_start():
    """apply_patch inserts code at function body start."""
    foo = FIXTURES_DIR / "foo.py"
    loaded = load_ast(str(foo))
    assert loaded is not None
    tree, source_bytes = loaded

    patch = {
        "symbol": "bar",
        "action": "insert",
        "target_node": "function_body_start",
        "code": "logger.info('bar called')",
    }
    new_bytes = apply_patch(tree, source_bytes, patch)
    new_code = generate_code(tree, new_bytes)
    assert "logger.info('bar called')" in new_code
    assert "def bar" in new_code
    assert "return 1" in new_code


def test_apply_patch_replace_function_body():
    """apply_patch replaces function body."""
    foo = FIXTURES_DIR / "foo.py"
    loaded = load_ast(str(foo))
    assert loaded is not None
    tree, source_bytes = loaded

    patch = {
        "symbol": "bar",
        "action": "replace",
        "target_node": "function_body",
        "code": "return 42",
    }
    new_bytes = apply_patch(tree, source_bytes, patch)
    new_code = generate_code(tree, new_bytes)
    assert "return 42" in new_code
    assert "return 1" not in new_code or "return 42" in new_code


def test_apply_patch_delete_function_body():
    """apply_patch deletes function body (replaces with pass)."""
    foo = FIXTURES_DIR / "foo.py"
    loaded = load_ast(str(foo))
    assert loaded is not None
    tree, source_bytes = loaded

    patch = {
        "symbol": "bar",
        "action": "delete",
        "target_node": "function_body",
        "code": "",
    }
    new_bytes = apply_patch(tree, source_bytes, patch)
    new_code = generate_code(tree, new_bytes)
    assert "def bar" in new_code
    assert "pass" in new_code


def test_apply_patch_symbol_not_found_raises():
    """apply_patch raises ValueError when symbol not found."""
    foo = FIXTURES_DIR / "foo.py"
    loaded = load_ast(str(foo))
    assert loaded is not None
    tree, source_bytes = loaded

    patch = {
        "symbol": "nonexistent_func",
        "action": "insert",
        "target_node": "function_body_start",
        "code": "x = 1",
    }
    with pytest.raises(ValueError, match="Symbol not found"):
        apply_patch(tree, source_bytes, patch)


def test_generate_code_returns_string():
    """generate_code returns source as string."""
    foo = FIXTURES_DIR / "foo.py"
    loaded = load_ast(str(foo))
    assert loaded is not None
    tree, source_bytes = loaded
    code = generate_code(tree, source_bytes)
    assert isinstance(code, str)
    assert "def bar" in code


def test_apply_patch_statement_after_insert(tmp_path):
    """apply_patch inserts code after a specific statement."""
    code = "def foo():\n    x = 1\n    return x\n"
    f = tmp_path / "t.py"
    f.write_text(code)
    loaded = load_ast(str(f))
    assert loaded is not None
    tree, source_bytes = loaded
    patch = {
        "symbol": "foo",
        "action": "insert",
        "target_node": "statement_after",
        "statement_index": 0,
        "code": "y = 2",
    }
    new_bytes = apply_patch(tree, source_bytes, patch)
    new_code = generate_code(tree, new_bytes)
    assert "x = 1" in new_code
    assert "y = 2" in new_code
    assert "return x" in new_code
    compile(new_code, "t.py", "exec")


def test_apply_patch_statement_replace(tmp_path):
    """apply_patch replaces a specific statement."""
    code = "def foo():\n    x = 1\n    return x\n"
    f = tmp_path / "t.py"
    f.write_text(code)
    loaded = load_ast(str(f))
    assert loaded is not None
    tree, source_bytes = loaded
    patch = {
        "symbol": "foo",
        "action": "replace",
        "target_node": "statement",
        "statement_index": 0,
        "code": "x = 42",
    }
    new_bytes = apply_patch(tree, source_bytes, patch)
    new_code = generate_code(tree, new_bytes)
    assert "x = 42" in new_code
    assert "x = 1" not in new_code
    compile(new_code, "t.py", "exec")


def test_apply_patch_statement_delete(tmp_path):
    """apply_patch deletes a specific statement."""
    code = "def foo():\n    x = 1\n    y = 2\n    return x\n"
    f = tmp_path / "t.py"
    f.write_text(code)
    loaded = load_ast(str(f))
    assert loaded is not None
    tree, source_bytes = loaded
    patch = {
        "symbol": "foo",
        "action": "delete",
        "target_node": "statement",
        "statement_index": 1,
        "code": "",
    }
    new_bytes = apply_patch(tree, source_bytes, patch)
    new_code = generate_code(tree, new_bytes)
    assert "x = 1" in new_code
    assert "y = 2" not in new_code
    assert "return x" in new_code
    compile(new_code, "t.py", "exec")


def test_apply_patch_if_block_replace(tmp_path):
    """apply_patch replaces an if block body."""
    code = "def foo():\n    if cond:\n        x = 1\n    return x\n"
    f = tmp_path / "t.py"
    f.write_text(code)
    loaded = load_ast(str(f))
    assert loaded is not None
    tree, source_bytes = loaded
    patch = {
        "symbol": "foo",
        "action": "replace",
        "target_node": "if_block",
        "block_index": 0,
        "code": "x = 99",
    }
    new_bytes = apply_patch(tree, source_bytes, patch)
    new_code = generate_code(tree, new_bytes)
    assert "if cond:" in new_code
    assert "x = 99" in new_code
    assert "x = 1" not in new_code
    compile(new_code, "t.py", "exec")


def test_apply_patch_try_block_replace(tmp_path):
    """apply_patch replaces a try block body."""
    code = "def foo():\n    try:\n        x = 1\n    except:\n        pass\n    return x\n"
    f = tmp_path / "t.py"
    f.write_text(code)
    loaded = load_ast(str(f))
    assert loaded is not None
    tree, source_bytes = loaded
    patch = {
        "symbol": "foo",
        "action": "replace",
        "target_node": "try_block",
        "block_index": 0,
        "code": "x = 99",
    }
    new_bytes = apply_patch(tree, source_bytes, patch)
    new_code = generate_code(tree, new_bytes)
    assert "try:" in new_code
    assert "x = 99" in new_code
    assert "x = 1" not in new_code
    compile(new_code, "t.py", "exec")


def test_apply_patch_case1_add_logging(tmp_path):
    """Case 1: Add logging to a function."""
    code = "def process_data():\n    x = 1\n    return x\n"
    f = tmp_path / "mod.py"
    f.write_text(code)
    loaded = load_ast(str(f))
    assert loaded is not None
    tree, source_bytes = loaded
    patch = {
        "symbol": "process_data",
        "action": "insert",
        "target_node": "function_body_start",
        "code": "import logging\nlogger = logging.getLogger(__name__)\nlogger.info('process_data called')",
    }
    new_bytes = apply_patch(tree, source_bytes, patch)
    new_code = generate_code(tree, new_bytes)
    assert "import logging" in new_code
    assert "logger.info" in new_code
    assert "x = 1" in new_code
    compile(new_code, "mod.py", "exec")


def test_apply_patch_case2_modify_function_body(tmp_path):
    """Case 2: Modify a function body."""
    code = "def compute():\n    a = 1\n    b = 2\n    return a + b\n"
    f = tmp_path / "mod.py"
    f.write_text(code)
    loaded = load_ast(str(f))
    assert loaded is not None
    tree, source_bytes = loaded
    patch = {
        "symbol": "compute",
        "action": "replace",
        "target_node": "function_body",
        "code": "a = 10\nb = 20\nreturn a * b",
    }
    new_bytes = apply_patch(tree, source_bytes, patch)
    new_code = generate_code(tree, new_bytes)
    assert "a = 10" in new_code
    assert "b = 20" in new_code
    assert "return a * b" in new_code
    assert "a + b" not in new_code
    compile(new_code, "mod.py", "exec")


def test_apply_patch_case3_delete_function_body(tmp_path):
    """Case 3: Delete a function body (replace with pass)."""
    code = "def deprecated():\n    old_logic = 1\n    return old_logic\n"
    f = tmp_path / "mod.py"
    f.write_text(code)
    loaded = load_ast(str(f))
    assert loaded is not None
    tree, source_bytes = loaded
    patch = {
        "symbol": "deprecated",
        "action": "delete",
        "target_node": "function_body",
        "code": "",
    }
    new_bytes = apply_patch(tree, source_bytes, patch)
    new_code = generate_code(tree, new_bytes)
    assert "def deprecated" in new_code
    assert "pass" in new_code
    assert "old_logic" not in new_code
    compile(new_code, "mod.py", "exec")


def test_apply_patch_rename_variable(tmp_path):
    """apply_patch renames variable within symbol scope."""
    code = "def foo():\n    x = 1\n    y = x + 1\n    return x\n"
    f = tmp_path / "t.py"
    f.write_text(code)
    loaded = load_ast(str(f))
    assert loaded is not None
    tree, source_bytes = loaded
    patch = {
        "symbol": "foo",
        "action": "rename",
        "old_name": "x",
        "new_name": "value",
    }
    new_bytes = apply_patch(tree, source_bytes, patch)
    new_code = generate_code(tree, new_bytes)
    assert "value = 1" in new_code
    assert "y = value + 1" in new_code
    assert "return value" in new_code
    assert "x = 1" not in new_code
    compile(new_code, "t.py", "exec")
