"""Unit tests for agent_v2/exploration/llm_input_normalize.py."""

from agent_v2.exploration.llm_input_normalize import (
    normalize_analyzer,
    normalize_scoper,
    normalize_selector_batch,
    normalize_selector_single,
    split_preview_full,
)


def test_split_preview_full_short_text_is_full_only():
    mode, f, l, full = split_preview_full("a\nb", 20, 10)
    assert mode == "full_only"
    assert f == "" and l == ""
    assert full == "a\nb"


def test_split_preview_full_long_text_has_preview_and_full():
    lines = [f"L{i}" for i in range(50)]
    text = "\n".join(lines)
    mode, f, l, full = split_preview_full(text, 20, 10)
    assert mode == "preview_plus_full"
    assert "L0" in f and "L19" in f
    assert "L40" in l
    assert full == text


def test_normalize_scoper_delimiters_and_globals():
    rows = [
        {"index": 0, "file_path": "a.py", "sources": ["graph"], "snippets": ["x"], "symbols": ["s"]}
    ]
    out = normalize_scoper(instruction="do", rows=rows)
    assert "[Global]" in out
    assert "instruction: do" in out
    assert "----- CANDIDATE 0 START ---" in out
    assert "----- CANDIDATE 0 END ---" in out
    assert "snippets_full:" in out


def test_normalize_selector_batch_strict_field_order():
    items = [
        {
            "file_path": "f.py",
            "symbol": "m",
            "source": "grep",
            "symbols": ["m"],
            "snippet_summary": "hi",
            "source_channels": ["grep"],
        }
    ]
    out = normalize_selector_batch(
        instruction="i",
        intent="k",
        limit=2,
        explored_block="",
        items=items,
    )
    assert "[Global]" in out
    assert "intent: k" in out
    assert "----- ITEM 0 START ---" in out
    assert out.index("file_path:") < out.index("snippet_summary")


def test_normalize_selector_single_items_only():
    out = normalize_selector_single(
        items=[{"file_path": "x.py", "source": "vector", "symbols": [], "snippet_summary": ""}]
    )
    assert "[Global]" not in out
    assert "----- ITEM 0 START ---" in out


def test_normalize_analyzer_relationships_section():
    out = normalize_analyzer(
        instruction="in",
        intent="it",
        task_intent_summary="tis",
        file_path="fp",
        snippet="s",
        symbol_relationships_block="rel",
        context_blocks=[{"file_path": "fp", "start": 1, "end": 2, "content": "c"}],
    )
    assert "[Relationships]" in out
    assert "[Global]" in out
    assert "upstream_selection_confidence: (not provided)" in out
    assert "----- CONTEXT BLOCK 0 START ---" in out
    assert "symbol_relationships_block:" in out


def test_normalize_analyzer_upstream_selection_confidence_pass_through():
    out = normalize_analyzer(
        instruction="in",
        intent="it",
        task_intent_summary="tis",
        file_path="fp",
        snippet="s",
        symbol_relationships_block="rel",
        context_blocks=[],
        upstream_selection_confidence="low",
    )
    assert "upstream_selection_confidence: low" in out
