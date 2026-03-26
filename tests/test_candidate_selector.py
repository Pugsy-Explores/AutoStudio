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


def test_select_batch_empty_selected_indices_still_uses_selected_array():
    """Models often emit selected_indices: [] alongside a valid `selected` list; [] must not block fallback."""
    selector = CandidateSelector(
        llm_generate=lambda _: (
            '{"selected_indices":[],"selected":[{"file_path":"src/a.py","symbol":"A"}],'
            '"no_relevant_candidate":false}'
        )
    )
    ranked = selector.select_batch(
        "rank candidates",
        "no intent",
        [_c("src/a.py", "A"), _c("src/b.py", "B")],
        seen_files=set(),
        limit=2,
    )
    assert ranked is not None
    assert len(ranked) >= 1
    assert ranked[0].file_path == "src/a.py"


def test_select_batch_unmatchable_output_falls_back_to_discovery_order():
    """When JSON parses but nothing matches candidates, use top-N discovery order (no hard fail)."""
    selector = CandidateSelector(
        llm_generate=lambda _: (
            '{"selected_indices":[],"selected":[{"file_path":"/wrong/path.py","symbol":"X"}],'
            '"no_relevant_candidate":false}'
        )
    )
    cands = [_c("src/a.py", "A"), _c("src/b.py", "B")]
    ranked = selector.select_batch(
        "rank candidates",
        "no intent",
        cands,
        seen_files=set(),
        limit=2,
    )
    assert ranked is not None
    assert [x.file_path for x in ranked] == ["src/a.py", "src/b.py"]


def test_select_batch_multi_index_uses_zero_based_by_default():
    """[2,3] means third and fourth candidates (0-based), not 1-based positions 1 and 2."""
    selector = CandidateSelector(
        llm_generate=lambda _: '{"selected_indices":[2,3],"no_relevant_candidate":false}'
    )
    ranked = selector.select_batch(
        "rank candidates",
        "no intent",
        [_c("a.py"), _c("b.py"), _c("c.py"), _c("d.py"), _c("e.py")],
        seen_files=set(),
        limit=4,
    )
    assert ranked is not None
    assert [x.file_path for x in ranked[:2]] == ["c.py", "d.py"]


def test_select_batch_one_based_index_maps_to_first_candidate():
    selector = CandidateSelector(
        llm_generate=lambda _: '{"selected_indices":[1],"no_relevant_candidate":false}'
    )
    ranked = selector.select_batch(
        "rank candidates",
        "no intent",
        [_c("src/a.py", "A"), _c("src/b.py", "B")],
        seen_files=set(),
        limit=2,
    )
    assert ranked is not None
    assert ranked[0].file_path == "src/a.py"


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
