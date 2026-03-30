"""Unit tests for symbol outline ranking and relationships block formatting."""

from agent_v2.exploration.file_symbol_outline import rank_outline_for_selector_query
from agent_v2.exploration.graph_symbol_edges_batch import format_symbol_relationships_block


def test_rank_outline_prefers_query_tokens():
    outline = [
        {"name": "foo", "type": "function"},
        {"name": "explore_inner", "type": "function"},
        {"name": "bar", "type": "class"},
    ]
    out = rank_outline_for_selector_query(outline, "how does explore_inner work", top_k=2)
    names = [x["name"] for x in out]
    assert "explore_inner" in names


def test_format_symbol_relationships_block_truncates():
    big = {"s" * 300: {"callers": ["a"], "callees": ["b"]}}
    text = format_symbol_relationships_block(big, max_chars=200)
    assert "SYMBOL RELATIONSHIPS" in text
    assert "truncated" in text or len(text) <= 220
