"""Tests for editing/patch_validator."""

import pytest

from editing.patch_validator import validate_patch


def test_validate_patch_valid_code_passes():
    """validate_patch returns valid=True for valid Python."""
    result = validate_patch("test.py", "def foo():\n    return 1\n")
    assert result["valid"] is True
    assert "errors" in result


def test_validate_patch_invalid_syntax_fails():
    """validate_patch returns valid=False for syntax error."""
    result = validate_patch("test.py", "def ( invalid")
    assert result["valid"] is False
    assert len(result["errors"]) >= 1
    assert "Syntax" in result["errors"][0] or "syntax" in result["errors"][0].lower()


def test_validate_patch_empty_module_passes():
    """validate_patch accepts empty module."""
    result = validate_patch("test.py", "")
    assert result["valid"] is True


def test_validate_patch_imports_passes():
    """validate_patch accepts valid import."""
    result = validate_patch("test.py", "import os\nfrom pathlib import Path\n")
    assert result["valid"] is True


def test_validate_patch_malformed_fails():
    """validate_patch fails for malformed code that compiles but may not parse."""
    # Unclosed string can sometimes compile in exec mode; use a clear syntax error
    result = validate_patch("test.py", "def x(\n    pass")
    assert result["valid"] is False


def test_validate_patch_code_compiles():
    """validate_patch ensures code compiles (exec mode)."""
    result = validate_patch("mod.py", "def foo():\n    x = 1\n    return x\n")
    assert result["valid"] is True


def test_validate_patch_ast_reparse_succeeds():
    """validate_patch ensures AST re-parse succeeds via tree-sitter."""
    code = "def bar():\n    logger.info('called')\n    return 42\n"
    result = validate_patch("mod.py", code)
    assert result["valid"] is True
