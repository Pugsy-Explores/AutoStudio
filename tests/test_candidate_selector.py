from typing import Any

import pytest

from agent_v2.exploration.candidate_selector import CandidateSelector
from agent_v2.schemas.exploration import ExplorationCandidate


def _c(path: str, symbol: str | None = None) -> ExplorationCandidate:
    return ExplorationCandidate(file_path=path, symbol=symbol, source="grep")


def test_select_batch_returns_ranked_matches_from_llm():
    selector = CandidateSelector(
        llm_generate=lambda _: (
            '{"selected":[{"file_path":"src/b.py","symbol":"B"},{"file_path":"src/a.py","symbol":"A"}]}'
        )
    )
    ranked = selector.select_batch(
        "rank candidates",
        "no intent",
        [_c("src/a.py", "A"), _c("src/b.py", "B"), _c("src/c.py", "C")],
        seen_files=set(),
        limit=2,
    )
    assert ranked is not None
    assert [f"{x.file_path}:{x.symbol}" for x in ranked] == ["src/b.py:B", "src/a.py:A"]


def test_select_batch_supports_no_relevant_candidate_signal():
    selector = CandidateSelector(
        llm_generate=lambda _: '{"selected":[],"no_relevant_candidate":true}'
    )
    ranked = selector.select_batch(
        "rank candidates",
        "no intent",
        [_c("src/a.py", "A"), _c("src/b.py", "B")],
        seen_files=set(),
        limit=2,
    )
    assert ranked is None


def test_select_batch_langfuse_generation_includes_prompt_in_input():
    captured: list[dict] = []

    class Span:
        def generation(self, name: str, **kwargs: Any) -> Any:
            inp = kwargs.get("input") or {}
            captured.append(dict(inp))

            class G:
                def end(self, **_: Any) -> None:
                    pass

            return G()

    selector = CandidateSelector(
        llm_generate=lambda _: (
            '{"selected":[{"file_path":"src/b.py","symbol":"B"},{"file_path":"src/a.py","symbol":"A"}]}'
        )
    )
    selector.select_batch(
        "unique select instruction",
        "no intent",
        [_c("src/a.py", "A"), _c("src/b.py", "B"), _c("src/c.py", "C")],
        seen_files=set(),
        limit=2,
        lf_select_span=Span(),
        lf_exploration_parent=None,
    )
    assert len(captured) == 1
    inp = captured[0]
    assert "prompt" in inp
    assert "unique select instruction" in inp["prompt"]
    assert inp.get("prompt_truncated") is False
    assert inp["prompt_chars"] == len(inp["prompt"])


def test_select_batch_raises_when_llm_output_invalid_strict_mode():
    selector = CandidateSelector(llm_generate=lambda _: "not-json")
    with pytest.raises(ValueError, match="No valid JSON object found"):
        selector.select_batch(
            "rank candidates",
            "no intent",
            [_c("src/a.py", "A"), _c("src/b.py", "B")],
            seen_files=set(),
            limit=1,
        )
