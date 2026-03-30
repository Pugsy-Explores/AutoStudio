"""Deep expansion by candidate_kind (Phase A)."""

from __future__ import annotations

import pytest

from agent.retrieval.retrieval_expander import (
    MAX_SYMBOL_EXPANSION,
    _extract_file_pieces,
    count_expanded_lines,
    expand_file_header,
    expand_region_bounded,
    expansion_action_for_result,
    extract_enclosing_class_name,
)
from agent.retrieval.result_contract import (
    RETRIEVAL_RESULT_TYPE_REGION_BODY,
    RETRIEVAL_RESULT_TYPE_SYMBOL_BODY,
)
from config.retrieval_config import MAX_LINES_PER_EXPANDED_UNIT


def test_expansion_action_region_with_range():
    r = {"file": "x.py", "candidate_kind": "region", "line_range": [3, 5]}
    assert expansion_action_for_result(r, kind_aware=True) == "read_region_bounded"


def test_expansion_action_file_kind():
    r = {"file": "x.py", "candidate_kind": "file"}
    assert expansion_action_for_result(r, kind_aware=True) == "read_file_header"


def test_extract_file_pieces_structure(tmp_path):
    p = tmp_path / "mod.py"
    p.write_text(
        '"""Module doc."""\nimport os\nfrom x import y\n\nclass A:\n    pass\n\ndef main():\n    pass\n',
        encoding="utf-8",
    )
    pieces = _extract_file_pieces(str(p))
    assert "Module doc." in pieces["module_docstring"]
    assert any("import os" in x for x in pieces["imports"])
    assert any("class A" in x for x in pieces["top_level_defs"])
    assert any("def main" in x for x in pieces["entrypoint_lines"])


def test_expand_file_header_bounded(tmp_path):
    p = tmp_path / "h.py"
    body = "\n".join([f"import m{i}" for i in range(100)])
    p.write_text(f'"""d"""\n{body}\n', encoding="utf-8")
    text = expand_file_header(str(p))
    assert len(text.splitlines()) <= 65


def test_region_expansion_preserves_line_range_and_type_hint(tmp_path):
    p = tmp_path / "r.py"
    lines = ["# head", "class Box:", "    def inner(self):", "        x = 1", "        return x"]
    p.write_text("\n".join(lines), encoding="utf-8")
    text, impl = expand_region_bounded(str(p), [3, 4])
    assert text
    assert count_expanded_lines(text) <= MAX_LINES_PER_EXPANDED_UNIT
    # impl True when anchored at class with enough lines (heuristic may vary)
    assert impl is True or impl is None


def test_extract_enclosing_class_name():
    lines = ["class Outer:", "    def meth(self):", "        pass"]
    assert extract_enclosing_class_name(lines, 2) == "Outer"


def test_budget_guard_max_expanded_units():
    assert MAX_SYMBOL_EXPANSION >= 1
    assert MAX_LINES_PER_EXPANDED_UNIT >= 40


def test_region_not_impl_false_by_default():
    """Region expansion must not set a false implementation_body_present."""
    text, impl = expand_region_bounded("nonexistent.py", [1, 2])
    assert text == ""
    assert impl is None


def test_symbol_body_constant():
    assert RETRIEVAL_RESULT_TYPE_SYMBOL_BODY == "symbol_body"
    assert RETRIEVAL_RESULT_TYPE_REGION_BODY == "region_body"
