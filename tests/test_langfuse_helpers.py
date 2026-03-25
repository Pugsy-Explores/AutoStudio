"""Tests for try_langfuse_generation fallback chain."""
from __future__ import annotations

from typing import Any

from agent_v2.observability.langfuse_helpers import (
    LANGFUSE_GENERATION_PROMPT_INPUT_MAX_CHARS,
    langfuse_generation_input_with_prompt,
    try_langfuse_generation,
)


def test_try_langfuse_generation_first_parent_wins():
    calls: list[str] = []

    class A:
        def generation(self, name: str, **kwargs: Any) -> Any:
            calls.append("A")

            class G:
                def end(self, **_: Any) -> None:
                    pass

            return G()

    class B:
        def generation(self, name: str, **kwargs: Any) -> Any:
            calls.append("B")
            raise RuntimeError("no")

    g = try_langfuse_generation(
        A(),
        B(),
        name="x",
        input={},
    )
    assert g is not None
    assert calls == ["A"]


def test_try_langfuse_generation_skips_duplicate_id():
    class P:
        def generation(self, name: str, **kwargs: Any) -> Any:
            class G:
                def end(self, **_: Any) -> None:
                    pass

            return G()

    p = P()
    g = try_langfuse_generation(p, p, name="x", input={})
    assert g is not None


def test_langfuse_generation_input_with_prompt_short():
    inp = langfuse_generation_input_with_prompt("hello", extra={"k": 1})
    assert inp["k"] == 1
    assert inp["prompt"] == "hello"
    assert inp["prompt_chars"] == 5
    assert inp["prompt_truncated"] is False


def test_langfuse_generation_input_with_prompt_truncates():
    cap = LANGFUSE_GENERATION_PROMPT_INPUT_MAX_CHARS
    long_p = "x" * (cap + 50)
    inp = langfuse_generation_input_with_prompt(long_p)
    assert len(inp["prompt"]) == cap
    assert inp["prompt_chars"] == cap + 50
    assert inp["prompt_truncated"] is True


def test_try_langfuse_generation_second_on_failure():
    class Bad:
        def generation(self, name: str, **kwargs: Any) -> Any:
            raise RuntimeError("fail")

    class Good:
        def generation(self, name: str, **kwargs: Any) -> Any:
            class G:
                def end(self, **_: Any) -> None:
                    pass

            return G()

    g = try_langfuse_generation(Bad(), Good(), name="y", input={})
    assert g is not None
