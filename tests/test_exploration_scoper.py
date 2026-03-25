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


def test_scope_pass_through_invalid_json():
    scoper = ExplorationScoper(llm_generate=lambda _: "not json")
    cands = [_c(f"{i}.py") for i in range(6)]
    out = scoper.scope("instr", cands)
    assert out == cands


def test_scope_pass_through_empty_selection():
    scoper = ExplorationScoper(llm_generate=lambda _: '{"selected_indices": []}')
    cands = [_c(f"{i}.py") for i in range(6)]
    out = scoper.scope("instr", cands)
    assert out == cands


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


def test_scope_no_llm_pass_through():
    scoper = ExplorationScoper(llm_generate=None)
    cands = [_c(f"{i}.py") for i in range(6)]
    out = scoper.scope("instr", cands)
    assert out == cands


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
