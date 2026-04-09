from typing import Any

import pytest

from agent_v2.exploration.exploration_scoper import ExplorationScoper
from agent_v2.schemas.exploration import ExplorationCandidate


def _c(path: str, sym: str | None = None) -> ExplorationCandidate:
    return ExplorationCandidate(file_path=path, symbol=sym, snippet="x" * 700, source="grep")


def test_scope_calls_llm_even_for_small_list():
    """Skip-when-trivial is orchestrated in the engine, not here."""
    calls = []

    def _llm(_: str) -> str:
        calls.append(1)
        return '{"selected_indices": [0]}'

    scoper = ExplorationScoper(llm_generate=_llm)
    cands = [_c("a.py"), _c("b.py")]
    out = scoper.scope("instr", cands)
    assert out == [cands[0]]
    assert calls == [1]


def test_scope_raises_on_invalid_json_strict_mode():
    scoper = ExplorationScoper(llm_generate=lambda _: "not json")
    cands = [_c(f"{i}.py") for i in range(6)]
    with pytest.raises(ValueError, match="No valid JSON object found"):
        scoper.scope("instr", cands)


def test_scope_raises_on_empty_selection_strict_mode():
    scoper = ExplorationScoper(llm_generate=lambda _: '{"selected_indices": []}')
    cands = [_c(f"{i}.py") for i in range(6)]
    with pytest.raises(ValueError, match="selected no valid indices"):
        scoper.scope("instr", cands)


def test_scope_dedupe_same_file_path_expands_to_all_original_candidates():
    """Duplicate file_path rows are merged for the LLM; selecting that slot returns every hit."""
    calls: list[str] = []

    def _llm(p: str) -> str:
        calls.append(p)
        assert "dup.py" in p and "snippets_full" in p and "symbols:" in p
        return '{"selected_indices": [0]}'

    scoper = ExplorationScoper(llm_generate=_llm)
    c0 = ExplorationCandidate(file_path="dup.py", symbol="a", snippet="one", source="grep")
    c1 = ExplorationCandidate(file_path="dup.py", symbol="b", snippet="two", source="vector")
    c2 = ExplorationCandidate(file_path="other.py", symbol=None, snippet="x", source="grep")
    out = scoper.scope("task", [c0, c1, c2])
    assert out == [c0, c1]
    assert len(calls) == 1


def test_scope_sorted_indices_not_llm_order():
    scoper = ExplorationScoper(
        llm_generate=lambda _: '{"selected_indices": [9, 2, 5]}',
    )
    cands = [_c(f"{i}.py") for i in range(10)]
    out = scoper.scope("instr", cands)
    assert [c.file_path for c in out] == ["2.py", "5.py", "9.py"]


def test_scope_returns_all_indices_when_model_selects_all():
    """No second output cap — selector limits batch size."""
    scoper = ExplorationScoper(
        llm_generate=lambda _: '{"selected_indices": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14]}',
    )
    cands = [_c(f"{i}.py") for i in range(15)]
    out = scoper.scope("instr", cands)
    assert len(out) == 15


def test_scope_no_llm_raises_strict_mode():
    scoper = ExplorationScoper(llm_generate=None)
    cands = [_c(f"{i}.py") for i in range(6)]
    with pytest.raises(ValueError, match="requires llm_generate in strict mode"):
        scoper.scope("instr", cands)


def test_scope_langfuse_generation_includes_prompt_in_langfuse_input():
    """Generation input must include prompt text (not only prompt_chars) for Langfuse UI."""
    captured: list[dict] = []

    class Span:
        def generation(self, name: str, **kwargs: Any) -> Any:
            inp = kwargs.get("input") or {}
            captured.append(dict(inp))

            class G:
                def end(self, **_: Any) -> None:
                    pass

            return G()

    scoper = ExplorationScoper(llm_generate=lambda _: '{"selected_indices": [0]}')
    cands = [_c("a.py"), _c("b.py")]
    scoper.scope(
        "unique instruction for langfuse",
        cands,
        lf_scope_span=Span(),
        lf_exploration_parent=None,
    )
    assert len(captured) == 1
    inp = captured[0]
    assert "prompt" in inp
    assert "unique instruction for langfuse" in inp["prompt"]
    assert inp.get("prompt_truncated") is False
    assert inp["prompt_chars"] == len(inp["prompt"])


def test_scope_langfuse_generation_falls_back_to_exploration_parent():
    """If ``exploration.scope`` span cannot host a generation, attach under ``exploration``."""
    gen_calls: list[str] = []

    class BadSpan:
        def generation(self, name: str, **kwargs: Any) -> Any:
            raise RuntimeError("sdk glitch")

    class GoodParent:
        def generation(self, name: str, **kwargs: Any) -> Any:
            gen_calls.append(name)

            class G:
                def end(self, **_: Any) -> None:
                    pass

            return G()

    scoper = ExplorationScoper(llm_generate=lambda _: '{"selected_indices": [0]}')
    cands = [_c("a.py"), _c("b.py")]
    out = scoper.scope(
        "instr",
        cands,
        lf_scope_span=BadSpan(),
        lf_exploration_parent=GoodParent(),
    )
    assert out == [cands[0]]
    assert gen_calls == ["exploration.scope"]


def test_scope_snippet_truncated_in_prompt():
    captured: list[str] = []

    def _llm(p: str) -> str:
        captured.append(p)
        return '{"selected_indices": [0]}'

    scoper = ExplorationScoper(
        llm_generate=_llm,
        max_snippet_chars=10,
    )
    cands = [ExplorationCandidate(file_path="a.py", snippet="a" * 100, source="grep")]
    scoper.scope("x", cands)
    assert "aaaaaaaaaa" in captured[0]
    assert "a" * 11 not in captured[0]


def test_scope_lenient_parsing_handles_fenced_non_json_spaces_tabs_newlines():
    raw = (
        "notes...\n"
        "```text\n"
        " selected_indices : [ 0,\\n\\t4 ] \n"
        "```\n"
    )
    scoper = ExplorationScoper(llm_generate=lambda _: raw)
    cands = [_c(f"{i}.py") for i in range(6)]
    out = scoper.scope("instr", cands)
    assert [c.file_path for c in out] == ["0.py", "4.py"]


def test_scope_lenient_parsing_handles_quoted_key_equals_style():
    raw = "'selected_indices' = [1, 3]"
    scoper = ExplorationScoper(llm_generate=lambda _: raw)
    cands = [_c(f"{i}.py") for i in range(6)]
    out = scoper.scope("instr", cands)
    assert [c.file_path for c in out] == ["1.py", "3.py"]


def test_scope_parsing_handles_selected_indices_string_value():
    raw = '{"selected_indices":"[0, 2]"}'
    scoper = ExplorationScoper(llm_generate=lambda _: raw)
    cands = [_c(f"{i}.py") for i in range(6)]
    out = scoper.scope("instr", cands)
    assert [c.file_path for c in out] == ["0.py", "2.py"]
