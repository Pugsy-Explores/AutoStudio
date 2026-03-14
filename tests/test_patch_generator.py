"""Tests for editing/patch_generator."""

import pytest

from editing.patch_generator import to_structured_patches, _looks_like_code, _first_symbol_from_context


def test_to_structured_patches_returns_changes():
    """to_structured_patches converts plan to structured format."""
    plan = {
        "changes": [
            {"file": "foo.py", "symbol": "bar", "action": "modify", "patch": "return 42", "reason": "test"},
        ],
    }
    result = to_structured_patches(plan, "Change return value", {})
    assert "changes" in result
    assert len(result["changes"]) == 1
    change = result["changes"][0]
    assert change["file"] == "foo.py"
    assert "patch" in change
    patch = change["patch"]
    assert patch["symbol"] == "bar"
    assert patch["action"] in ("insert", "delete")
    assert "target_node" in patch
    assert "code" in patch


def test_to_structured_patches_code_like_patch_uses_patch_text():
    """When patch text looks like code, it is used as code."""
    plan = {
        "changes": [
            {"file": "foo.py", "symbol": "bar", "action": "modify", "patch": "def bar():\n    return 42", "reason": "test"},
        ],
    }
    result = to_structured_patches(plan, "Change function", {})
    patch = result["changes"][0]["patch"]
    assert "def bar" in patch["code"]
    assert "return 42" in patch["code"]


def test_to_structured_patches_non_code_uses_instruction():
    """When patch text is not code-like, instruction is used."""
    plan = {
        "changes": [
            {"file": "foo.py", "symbol": "bar", "action": "modify", "patch": "Apply changes", "reason": "test"},
        ],
    }
    result = to_structured_patches(plan, "Add logging here", {})
    patch = result["changes"][0]["patch"]
    assert "# Add logging here" in patch["code"] or "Add logging" in patch["code"]
    assert "pass" in patch["code"] or "TODO" in patch["code"]


def test_to_structured_patches_delete_action():
    """delete action maps to ast delete."""
    plan = {
        "changes": [
            {"file": "foo.py", "symbol": "bar", "action": "delete", "patch": "", "reason": "remove"},
        ],
    }
    result = to_structured_patches(plan, "Delete", {})
    assert result["changes"][0]["patch"]["action"] == "delete"


def test_to_structured_patches_resolves_symbol_from_context():
    """When symbol is empty, first symbol from context is used."""
    plan = {
        "changes": [
            {"file": "executor.py", "symbol": "", "action": "modify", "patch": "pass", "reason": "caller"},
        ],
    }
    context = {
        "ranked_context": [{"file": "executor.py", "symbol": "execute_step", "snippet": "..."}],
    }
    result = to_structured_patches(plan, "Modify", context)
    assert result["changes"][0]["patch"]["symbol"] == "execute_step"


def test_looks_like_code_def():
    """_looks_like_code returns True for def."""
    assert _looks_like_code("def foo(): pass") is True


def test_looks_like_code_return():
    """_looks_like_code returns True for return."""
    assert _looks_like_code("return 1") is True


def test_looks_like_code_short_text():
    """_looks_like_code returns False for very short text."""
    assert _looks_like_code("ab") is False
    assert _looks_like_code("") is False


def test_looks_like_code_plain_instruction():
    """_looks_like_code returns False for plain instruction."""
    assert _looks_like_code("Add logging to the function") is False


def test_looks_like_code_logger_and_print():
    """_looks_like_code returns True for logger.info and print()."""
    assert _looks_like_code("logger.info('step executed')") is True
    assert _looks_like_code("print('hello')") is True


def test_first_symbol_from_context():
    """_first_symbol_from_context returns first symbol for file."""
    context = {
        "ranked_context": [
            {"file": "a.py", "symbol": "foo", "snippet": "..."},
            {"file": "b.py", "symbol": "bar", "snippet": "..."},
        ],
    }
    assert _first_symbol_from_context("a.py", context) == "foo"
    assert _first_symbol_from_context("b.py", context) == "bar"
    assert _first_symbol_from_context("c.py", context) == ""
