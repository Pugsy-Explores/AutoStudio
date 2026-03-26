"""QueryIntentParser: LLM JSON coercion and wiring contract (no heuristic path)."""

from unittest.mock import MagicMock

import pytest

import agent_v2.exploration.query_intent_parser as query_intent_parser_mod
from agent_v2.exploration.query_intent_parser import QueryIntentParser, _coerce_for_query_intent
from agent_v2.schemas.exploration import QueryIntent


def test_coerce_alias_keys_to_query_intent_fields():
    raw = {
        "symbol_queries": ["Foo", "Bar"],
        "text_queries": ["baz qux"],
        "intent": "find_definition",
    }
    coerced = _coerce_for_query_intent(raw)
    qi = QueryIntent.model_validate(coerced)
    assert qi.symbols == ["Foo", "Bar"]
    assert qi.keywords == ["baz qux"]
    assert qi.intents == ["find_definition"]


def test_coerce_prefers_canonical_keys_when_present():
    raw = {
        "symbols": ["A"],
        "keywords": ["b"],
        "intents": ["x"],
        "symbol_queries": ["ignored"],
        "text_queries": ["ignored"],
    }
    coerced = _coerce_for_query_intent(raw)
    qi = QueryIntent.model_validate(coerced)
    assert qi.symbols == ["A"]
    assert qi.keywords == ["b"]


def test_parse_requires_llm_generate():
    p = QueryIntentParser(llm_generate=None)
    with pytest.raises(ValueError, match="requires llm_generate"):
        p.parse("any instruction")


def test_parse_with_stub_llm_accepts_alias_json():
    def llm(_prompt: str) -> str:
        return '{"symbol_queries":["X"],"text_queries":["y"],"intent":"debug"}'

    p = QueryIntentParser(llm_generate=llm)
    qi = p.parse("find X")
    assert qi.symbols == ["X"]
    assert qi.keywords == ["y"]
    assert qi.intents == ["debug"]


def test_parse_removes_repeated_queries_from_previous_output():
    def llm(_prompt: str) -> str:
        return (
            '{"symbols":["X","Y"],"keywords":["foo","bar"],'
            '"regex_patterns":["r1"],"intents":["debug","find_definition"]}'
        )

    p = QueryIntentParser(llm_generate=llm)
    qi = p.parse(
        "find X",
        previous_queries={
            "symbols": ["X"],
            "keywords": ["foo"],
            "regex_patterns": ["r1"],
            "intents": ["debug"],
        },
        failure_reason="no_results",
    )
    assert qi.symbols == ["Y"]
    assert qi.keywords == ["bar"]
    assert qi.regex_patterns == []
    assert qi.intents == ["find_definition"]


def test_parse_supports_message_callable():
    def llm_messages(messages: list[dict[str, str]]) -> str:
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        return '{"symbols":["X"],"keywords":["y"],"intents":["debug"]}'

    p = QueryIntentParser(llm_generate_messages=llm_messages)
    qi = p.parse("find X")
    assert qi.symbols == ["X"]
    assert qi.keywords == ["y"]


def test_parse_langfuse_generation_receives_structured_output(monkeypatch):
    """When a Langfuse-compatible parent is provided, generation ends with query_intent output."""
    gen = MagicMock()

    def fake_try_langfuse(*parents, name, input):
        assert name == "exploration.query_intent"
        assert "prompt" in input
        assert input.get("instruction_chars") == len("short instruction")
        return gen if parents[0] is not None else None

    monkeypatch.setattr(query_intent_parser_mod, "try_langfuse_generation", fake_try_langfuse)

    def llm(_prompt: str) -> str:
        return '{"symbols":["Sym"],"keywords":["kw"],"intents":["locate"]}'

    p = QueryIntentParser(llm_generate=llm)
    fake_span = object()
    p.parse("short instruction", lf_intent_span=fake_span, lf_exploration_parent=None)
    gen.end.assert_called_once()
    call_kw = gen.end.call_args.kwargs
    assert "output" in call_kw
    assert call_kw["output"]["query_intent"]["symbols"] == ["Sym"]
    assert call_kw["metadata"]["ok"] is True
