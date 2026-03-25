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
        [_c("src/a.py", "A"), _c("src/b.py", "B")],
        seen_files=set(),
        limit=2,
    )
    assert ranked is None


def test_select_batch_falls_back_when_llm_output_invalid():
    selector = CandidateSelector(llm_generate=lambda _: "not-json")
    ranked = selector.select_batch(
        "rank candidates",
        [_c("src/a.py", "A"), _c("src/b.py", "B")],
        seen_files=set(),
        limit=1,
    )
    assert ranked is not None
    assert len(ranked) == 1
