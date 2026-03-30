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


def test_parse_includes_context_feedback_in_prompt_and_input_extra(monkeypatch):
    captured: dict[str, object] = {}

    def fake_exploration_llm_call(
        lf_intent_span,
        lf_exploration_parent,
        *,
        name,
        prompt,
        prompt_registry_key,
        invoke,
        stage,
        model_name,
        input_extra,
        on_complete,
    ):
        captured["prompt"] = prompt
        captured["input_extra"] = input_extra
        raw = invoke()
        on_complete(raw)

    monkeypatch.setattr(query_intent_parser_mod, "exploration_llm_call", fake_exploration_llm_call)

    def llm(_prompt: str) -> str:
        return '{"symbols":["X"],"keywords":["y"],"intents":["debug"]}'

    p = QueryIntentParser(llm_generate=llm)
    p.parse(
        "find X",
        context_feedback={
            "partial_findings": [{"summary": "found handler"}],
            "known_entities": {"symbols": ["PlanExecutor"], "files": ["agent_v2/runtime/plan_executor.py"]},
            "knowledge_gaps": [{"description": "missing caller chain"}],
            "relationships": [{"from": "a", "to": "b", "type": "callers"}],
        },
    )
    prompt = str(captured["prompt"])
    input_extra = captured["input_extra"]
    assert "Context feedback (optional):" in prompt
    assert "missing caller chain" in prompt
    assert input_extra["context_feedback_present"] is True
    assert input_extra["partial_findings_count"] == 1
    assert input_extra["known_symbols_count"] == 1
    assert input_extra["known_files_count"] == 1
    assert input_extra["knowledge_gaps_count"] == 1
    assert input_extra["relationships_count"] == 1


def test_parse_langfuse_generation_receives_structured_output(monkeypatch):
    """When a Langfuse-compatible parent is provided, generation ends with query_intent output."""
    called: dict[str, object] = {}

    def fake_exploration_llm_call(
        lf_intent_span,
        lf_exploration_parent,
        *,
        name,
        prompt,
        prompt_registry_key,
        invoke,
        stage,
        model_name,
        input_extra,
        on_complete,
    ):
        called["name"] = name
        called["prompt"] = prompt
        called["input_extra"] = input_extra
        raw = invoke()
        output, metadata = on_complete(raw)
        called["output"] = output
        called["metadata"] = metadata

    monkeypatch.setattr(query_intent_parser_mod, "exploration_llm_call", fake_exploration_llm_call)

    def llm(_prompt: str) -> str:
        return '{"symbols":["Sym"],"keywords":["kw"],"intents":["locate"]}'

    p = QueryIntentParser(llm_generate=llm)
    fake_span = object()
    p.parse("short instruction", lf_intent_span=fake_span, lf_exploration_parent=None)
    assert called["name"] == "exploration.query_intent"
    assert "prompt" in called
    assert called["input_extra"]["instruction_chars"] == len("short instruction")
    assert called["output"]["query_intent"]["symbols"] == ["Sym"]
    assert called["metadata"]["ok"] is True
