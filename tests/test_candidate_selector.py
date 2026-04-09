from typing import Any

import pytest

from agent_v2.exploration.candidate_selector import (
    CandidateSelector,
    _selector_candidate_payload,
    build_selector_prompt_with_budget,
)
from agent_v2.schemas.exploration import ExplorationCandidate


def _c(path: str, symbol: str | None = None) -> ExplorationCandidate:
    return ExplorationCandidate(file_path=path, symbol=symbol, source="grep")


def test_selector_payload_contract_contains_minimal_fields():
    p = _selector_candidate_payload(
        _c("src/a.py", "A"),
        snippet_compact="def A()",
        outline_for_prompt=[{"name": "A", "type": "function", "code": "def A():\n    pass"}],
    )
    assert set(p.keys()) == {"file_path", "symbol", "source", "snippet_compact", "outline_for_prompt"}


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
        explored_location_keys=None,
        limit=2,
    )
    assert ranked is not None
    assert [f"{x.file_path}:{x.symbol}" for x in ranked.selected_candidates] == [
        "src/b.py:B",
        "src/a.py:A",
    ]


def test_build_selector_prompt_with_budget_keeps_all_top_candidates():
    top = [_c(f"src/{i}.py", f"S{i}") for i in range(6)]
    outlines = [
        [{"name": f"S{i}", "type": "function", "start_line": "1", "end_line": "4", "code": "def x():\n" + ("    pass\n" * 100)}]
        for i in range(6)
    ]
    txt, items, n = build_selector_prompt_with_budget(
        instruction="rank",
        intent="intent",
        limit=3,
        explored_block="",
        top=top,
        outline_rows=outlines,
        max_selector_code_chars=45000,
        prompt_token_budget=100,  # intentionally tiny to force pruning
    )
    assert n == len(top)
    assert len(items) == n
    assert "snippet_summary_full" not in txt


def test_select_batch_supports_no_relevant_candidate_signal():
    selector = CandidateSelector(
        llm_generate=lambda _: '{"selected":[],"no_relevant_candidate":true}'
    )
    ranked = selector.select_batch(
        "rank candidates",
        "no intent",
        [_c("src/a.py", "A"), _c("src/b.py", "B")],
        explored_location_keys=None,
        limit=2,
    )
    assert ranked is not None
    assert ranked.selected_candidates == []


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
        explored_location_keys=None,
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
        explored_location_keys=None,
        limit=2,
    )
    assert ranked is not None
    assert len(ranked.selected_candidates) >= 1
    assert ranked.selected_candidates[0].file_path == "src/a.py"


def test_select_batch_unmatchable_output_returns_empty_fragmented():
    """When JSON parses but nothing matches candidates, emit fragmented empty selection (no silent remap)."""
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
        explored_location_keys=None,
        limit=2,
    )
    assert ranked is not None
    assert ranked.selected_candidates == []
    assert ranked.coverage_signal == "fragmented"


def test_select_batch_multi_index_uses_zero_based_by_default():
    """[2,3] means third and fourth candidates (0-based), not 1-based positions 1 and 2."""
    selector = CandidateSelector(
        llm_generate=lambda _: '{"selected_indices":[2,3],"no_relevant_candidate":false}'
    )
    ranked = selector.select_batch(
        "rank candidates",
        "no intent",
        [_c("a.py"), _c("b.py"), _c("c.py"), _c("d.py"), _c("e.py")],
        explored_location_keys=None,
        limit=4,
    )
    assert ranked is not None
    assert [x.file_path for x in ranked.selected_candidates[:2]] == ["c.py", "d.py"]


def test_select_batch_one_based_index_maps_to_first_candidate():
    selector = CandidateSelector(
        llm_generate=lambda _: '{"selected_indices":[1],"no_relevant_candidate":false}'
    )
    ranked = selector.select_batch(
        "rank candidates",
        "no intent",
        [_c("src/a.py", "A"), _c("src/b.py", "B")],
        explored_location_keys=None,
        limit=2,
    )
    assert ranked is not None
    assert ranked.selected_candidates[0].file_path == "src/a.py"


def test_select_batch_raises_when_llm_output_invalid_strict_mode():
    selector = CandidateSelector(llm_generate=lambda _: "not-json")
    with pytest.raises(ValueError, match="No valid JSON object found"):
        selector.select_batch(
            "rank candidates",
            "no intent",
            [_c("src/a.py", "A"), _c("src/b.py", "B")],
            explored_location_keys=None,
            limit=1,
        )


def test_select_batch_accepts_kv_style_selected_indices_output():
    selector = CandidateSelector(
        llm_generate=lambda _: "selected_indices: [0]\nselection_confidence: \"high\""
    )
    ranked = selector.select_batch(
        "rank candidates",
        "no intent",
        [_c("src/a.py", "A"), _c("src/b.py", "B")],
        explored_location_keys=None,
        limit=1,
    )
    assert ranked is not None
    assert [f"{x.file_path}:{x.symbol}" for x in ranked.selected_candidates] == ["src/a.py:A"]


def test_select_batch_accepts_fenced_kv_selected_indices_output():
    selector = CandidateSelector(
        llm_generate=lambda _: "```text\nselected_indices : [ 0,\\n\\t1 ]\nselection_confidence: high\n```"
    )
    ranked = selector.select_batch(
        "rank candidates",
        "no intent",
        [_c("src/a.py", "A"), _c("src/b.py", "B")],
        explored_location_keys=None,
        limit=2,
    )
    assert ranked is not None
    assert [f"{x.file_path}:{x.symbol}" for x in ranked.selected_candidates] == [
        "src/a.py:A",
        "src/b.py:B",
    ]


def test_select_batch_empty_json_object_normalizes_to_empty_selection():
    selector = CandidateSelector(llm_generate=lambda _: "{}")
    ranked = selector.select_batch(
        "find nothing",
        "no intent",
        [_c("src/a.py", "A")],
        explored_location_keys=None,
        limit=2,
    )
    assert ranked.selected_candidates == []
    assert ranked.selection_confidence == "low"


def test_select_batch_explicit_empty_indices_without_selected_is_empty():
    selector = CandidateSelector(
        llm_generate=lambda _: '{"selected_indices":[],"selected_symbols":{}}'
    )
    ranked = selector.select_batch(
        "irrelevant pool",
        "no intent",
        [_c("src/a.py", "A")],
        explored_location_keys=None,
        limit=2,
    )
    assert ranked.selected_candidates == []


def test_select_batch_populates_expanded_symbols_from_selected_symbols():
    selector = CandidateSelector(
        llm_generate=lambda _: (
            '{"selected_indices":[0,1],'
            '"selected":[{"file_path":"src/a.py","symbol":"A"},{"file_path":"src/b.py","symbol":"B"}],'
            '"selected_symbols":{"0":["Foo.run","Foo"],"1":["Bar.run"]}}'
        )
    )
    ranked = selector.select_batch(
        "rank candidates",
        "no intent",
        [_c("src/a.py", "A"), _c("src/b.py", "B")],
        explored_location_keys=None,
        limit=2,
    )
    assert ranked.expanded_symbols == ["Foo.run", "Foo", "Bar.run"]


def test_select_batch_does_not_fallback_to_selected_when_indices_present_but_unmatchable():
    selector = CandidateSelector(
        llm_generate=lambda _: (
            '{"selected_indices":[99],'
            '"selected":[{"file_path":"src/a.py","symbol":"A"}],'
            '"no_relevant_candidate":false}'
        )
    )
    ranked = selector.select_batch(
        "rank candidates",
        "no intent",
        [_c("src/a.py", "A"), _c("src/b.py", "B")],
        explored_location_keys=None,
        limit=2,
    )
    assert ranked.selected_candidates == []
    assert ranked.coverage_signal == "fragmented"
