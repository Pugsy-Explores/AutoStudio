"""Unit tests for safe_json_loads (Phase 3 JSON recovery)."""

import pytest

from agent.utils.json_utils import safe_json_loads


def test_valid_json_passes_unchanged():
    """Valid JSON passes unchanged."""
    data, err, repaired = safe_json_loads('{"a": 1}')
    assert data == {"a": 1}
    assert err is None
    assert repaired is False


def test_missing_closing_brace_repaired():
    """Missing closing brace is repaired."""
    data, err, repaired = safe_json_loads('{"a": 1')
    assert data == {"a": 1}
    assert err is None
    assert repaired is True


def test_markdown_wrapped_json_extracted():
    """Markdown-wrapped JSON is extracted and parsed."""
    text = '''```json
{"a": 1}
```'''
    data, err, repaired = safe_json_loads(text)
    assert data == {"a": 1}
    assert err is None
    assert repaired is False


def test_garbage_input_fails():
    """Garbage input fails."""
    data, err, repaired = safe_json_loads("hello world")
    assert data is None
    assert err is not None
    assert repaired is False


def test_partial_json_cannot_be_safely_repaired_fails():
    """Partial JSON that cannot be safely repaired fails."""
    # Trailing comma - brace count is balanced, but JSON is invalid
    data, err, repaired = safe_json_loads('{"a": 1,')
    assert data is None
    assert err is not None
    assert repaired is False


def test_empty_input_fails():
    """Empty input returns empty error."""
    data, err, repaired = safe_json_loads("")
    assert data is None
    assert err == "empty"
    assert repaired is False


def test_nested_missing_brace_repaired():
    """Nested object with missing closing braces is repaired."""
    data, err, repaired = safe_json_loads('{"a": {"b": 1')
    assert data is not None
    assert err is None
    assert repaired is True
    assert data == {"a": {"b": 1}}
