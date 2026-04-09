from agent_v2.exploration.exploration_engine_v2 import ExplorationEngineV2
from agent_v2.schemas.exploration import ExplorationCandidate, ExplorationTarget, SelectorBatchResult


def _candidate(file_path: str, symbol: str) -> ExplorationCandidate:
    return ExplorationCandidate(file_path=file_path, symbol=symbol, source="grep")


def test_build_analyzer_symbol_context_uses_all_selected_symbols_union():
    engine = ExplorationEngineV2.__new__(ExplorationEngineV2)
    outline_map = {
        "a.py": [
            {"name": "A.f", "type": "function", "start_line": "1", "end_line": "3", "code": "def A_f():\n    return 1\n"},
        ],
        "b.py": [
            {"name": "B.f", "type": "function", "start_line": "1", "end_line": "3", "code": "def B_f():\n    return 2\n"},
            {"name": "B.g", "type": "function", "start_line": "4", "end_line": "6", "code": "def B_g():\n    return 3\n"},
        ],
    }
    engine._canonical_path = lambda p, base_root: p  # type: ignore[attr-defined]
    engine._outline_full_for_file = lambda p: outline_map.get(p, [])  # type: ignore[attr-defined]
    batch = SelectorBatchResult(
        selected_candidates=[_candidate("a.py", "A.f"), _candidate("b.py", "B.f")],
        selected_symbols={"0": ["A.f"], "1": ["B.f"]},
        expanded_symbols=["B.g", "A.f"],
        selected_top_indices=[0, 1],
    )
    target = ExplorationTarget(
        file_path="a.py",
        symbol="A.f",
        source="discovery",
        selector_batch=batch,
        selector_top_index=0,
    )
    blocks = engine._build_analyzer_symbol_context_blocks(target, max_chars=10_000)  # type: ignore[attr-defined]
    assert len(blocks) == 1
    text = blocks[0].content
    assert "a.py::A.f" in text
    assert "b.py::B.f" in text
    assert "b.py::B.g" in text


def test_build_analyzer_symbol_context_applies_trim_marker_and_trimmed_prefix():
    engine = ExplorationEngineV2.__new__(ExplorationEngineV2)
    outline_map = {
        "a.py": [
            {"name": "A.f", "type": "function", "start_line": "1", "end_line": "3", "code": "def A_f():\n    return 1\n"},
            {
                "name": "A.big",
                "type": "function",
                "start_line": "4",
                "end_line": "100",
                "code": "def A_big():\n" + ("    work()\n" * 600),
            },
        ],
    }
    engine._canonical_path = lambda p, base_root: p  # type: ignore[attr-defined]
    engine._outline_full_for_file = lambda p: outline_map.get(p, [])  # type: ignore[attr-defined]
    batch = SelectorBatchResult(
        selected_candidates=[_candidate("a.py", "A.f")],
        selected_symbols={"0": ["A.f", "A.big"]},
        expanded_symbols=[],
        selected_top_indices=[0],
    )
    target = ExplorationTarget(
        file_path="a.py",
        symbol="A.f",
        source="discovery",
        selector_batch=batch,
        selector_top_index=0,
    )
    blocks = engine._build_analyzer_symbol_context_blocks(target, max_chars=200)  # type: ignore[attr-defined]
    assert len(blocks) == 1
    text = blocks[0].content
    assert "[CODE TRIMMED IN ANALYZER CONTEXT]" in text
    assert "[trimmed] def A_big" in text
