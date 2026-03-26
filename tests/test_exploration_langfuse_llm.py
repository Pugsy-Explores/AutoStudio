"""Invariant tests for exploration_llm_call (LANGFUSE_EXPLORATION_TRACING_PLAN)."""

from __future__ import annotations

import pytest

from agent_v2.observability.langfuse_helpers import (
    EXPLORATION_LLM_STAGE_VALUES,
    exploration_llm_call,
    get_exploration_llm_counters,
    reset_exploration_llm_counters,
)


class _RecordingGen:
    def __init__(self) -> None:
        self.end_calls = 0

    def end(self, **kwargs: object) -> None:
        del kwargs
        self.end_calls += 1


class _RecordingParent:
    """Minimal Langfuse parent: supports .generation(name, input=...)."""

    def __init__(self) -> None:
        self.generations: list[tuple[str, object, _RecordingGen]] = []

    def generation(self, name: str, input: object = None, **kwargs: object) -> _RecordingGen:
        del kwargs
        g = _RecordingGen()
        self.generations.append((name, input, g))
        return g


@pytest.fixture(autouse=True)
def _reset_counters() -> None:
    reset_exploration_llm_counters()
    yield
    reset_exploration_llm_counters()


def test_exploration_llm_call_invoke_matches_generation_end_with_parent() -> None:
    parent = _RecordingParent()

    out = exploration_llm_call(
        parent,
        name="test.gen",
        prompt="hello",
        prompt_registry_key="exploration.test",
        invoke=lambda: "ok",
        stage="analyze",
        model_name="fixture-model",
        on_complete=lambda r: ({"echo": r}, {"ok": True}),
    )

    assert out == "ok"
    inv, gend = get_exploration_llm_counters()
    assert inv == 1
    assert gend == 1
    assert len(parent.generations) == 1
    name, inp, gen = parent.generations[0]
    assert name == "test.gen"
    assert isinstance(inp, dict)
    assert inp.get("prompt_registry_key") == "exploration.test"
    assert inp.get("stage") == "analyze"
    assert inp.get("model_name") == "fixture-model"
    assert gen.end_calls == 1


def test_exploration_llm_call_missing_prompt_registry_key_raises() -> None:
    with pytest.raises(ValueError, match="prompt_registry_key"):
        exploration_llm_call(
            _RecordingParent(),
            name="x",
            prompt="p",
            prompt_registry_key="",
            invoke=lambda: "x",
            stage="scope",
        )


def test_exploration_llm_call_invalid_stage_raises() -> None:
    with pytest.raises(ValueError, match="stage must be one of"):
        exploration_llm_call(
            _RecordingParent(),
            name="x",
            prompt="p",
            prompt_registry_key="k",
            invoke=lambda: "x",
            stage="not_a_valid_stage",
        )


def test_exploration_llm_stage_values_cover_documented_stages() -> None:
    assert EXPLORATION_LLM_STAGE_VALUES == {
        "query_intent",
        "select",
        "scope",
        "analyze",
        "synthesis",
    }
