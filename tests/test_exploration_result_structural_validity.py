"""
FinalExplorationSchema must remain structurally valid (evidence + exploration_summary + metadata)
for all engine termination paths.

Run with output:
  pytest -s tests/test_exploration_result_structural_validity.py -v
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from agent_v2.exploration.exploration_engine_v2 import ExplorationEngineV2
from agent_v2.schemas.exploration import QueryIntent, ReadPacket, UnderstandingResult
from agent_v2.schemas.final_exploration import FinalExplorationSchema
from agent_v2.schemas.execution import ExecutionResult
from agent_v2.schemas.tool import ToolResult
from agent_v2.runtime.tool_mapper import map_tool_result_to_execution_result


def _ok(tool_name: str, data: dict | None = None, summary: str = "ok") -> ExecutionResult:
    tr = ToolResult(
        tool_name=tool_name,
        success=True,
        data=data or {},
        duration_ms=1,
    )
    result = map_tool_result_to_execution_result(tr, step_id="s1")
    return result.model_copy(update={"output": result.output.model_copy(update={"summary": summary})})


def _assert_exploration_result_valid(result: FinalExplorationSchema) -> None:
    assert isinstance(result.evidence, list)
    assert len(result.evidence) <= 6
    assert result.exploration_summary is not None
    assert result.metadata is not None
    assert result.exploration_id
    assert isinstance(result.instruction, str)
    es = result.exploration_summary
    assert isinstance(es.overall, str) and es.overall
    assert isinstance(es.key_findings, list)
    assert isinstance(es.knowledge_gaps, list)
    assert result.metadata.termination_reason
    assert result.metadata.completion_status in ("complete", "incomplete")
    assert result.metadata.total_items == len(result.evidence)
    assert isinstance(result.metadata.created_at, str) and result.metadata.created_at
    assert isinstance(result.metadata.source_summary, dict)
    if not es.knowledge_gaps:
        assert es.knowledge_gaps_empty_reason
        assert str(es.knowledge_gaps_empty_reason).strip()
    else:
        assert es.knowledge_gaps_empty_reason is None
    for it in result.evidence:
        assert it.item_id
        assert it.content.summary
        assert it.relevance.reason is not None
        assert it.metadata.tool_name is not None
    assert result.trace.llm_used is False
    assert result.confidence in ("high", "medium", "low")
    FinalExplorationSchema.model_validate(result.model_dump())


def _print_case(title: str, result: FinalExplorationSchema) -> None:
    es = result.exploration_summary
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")
    print("termination_reason:", result.metadata.termination_reason)
    print("completion_status:", result.metadata.completion_status)
    print("evidence count:", len(result.evidence))
    print("exploration_summary.overall:", es.overall[:500])
    print("knowledge_gaps:", json.dumps(es.knowledge_gaps, indent=2))
    print("knowledge_gaps_empty_reason:", es.knowledge_gaps_empty_reason)


def test_no_discovery_candidates_early_exit(monkeypatch):
    """Discovery yields no candidates → pending queue empty; no inspection."""
    import agent_v2.exploration.exploration_engine_v2 as emod

    monkeypatch.setattr(emod, "ENABLE_UTILITY_STOP", False)

    class _Parser:
        def parse(self, instruction: str, **kwargs):
            return QueryIntent(symbols=[], keywords=[], intents=["debug"])

    class _Dispatcher:
        def execute(self, step, state):
            return _ok("search", data={"results": []})

        def search_batch(self, queries, state, *, mode, step_id_prefix, max_workers=4):
            return [_ok("search", data={"results": []}) for _q in queries]

    engine = ExplorationEngineV2(
        dispatcher=_Dispatcher(),
        intent_parser=_Parser(),
        selector=object(),
        inspection_reader=object(),
        analyzer=object(),
        graph_expander=type("_G", (), {"expand": lambda *a, **k: ([], _ok("graph_query", data={}))})(),
    )
    result = engine.explore("nothing to find", state=SimpleNamespace(context={}))
    _print_case("no candidates (empty discovery)", result)
    _assert_exploration_result_valid(result)
    assert result.evidence == []
    assert result.metadata.termination_reason == "pending_exhausted"


def test_single_step_sufficient(monkeypatch):
    """One inspect + sufficient analyzer → primary_symbol_sufficient."""
    import agent_v2.exploration.exploration_engine_v2 as emod

    monkeypatch.setattr(emod, "ENABLE_UTILITY_STOP", False)

    class _Parser:
        def parse(self, instruction: str, **kwargs):
            return QueryIntent(symbols=["SufSym"], keywords=[], intents=["find_definition"])

    class _Dispatcher:
        def execute(self, step, state):
            return _ok("read_snippet", data={})

        def search_batch(self, queries, state, *, mode, step_id_prefix, max_workers=4):
            return [
                _ok(
                    "search",
                    data={
                        "results": [
                            {
                                "file_path": "/tmp/suf_one.py",
                                "symbol": "SufSym",
                                "score": 0.9,
                                "source": "grep",
                            }
                        ]
                    },
                )
                for _q in queries
            ]

    class _Selector:
        def select_batch(self, instruction, intent_text, scoped, seen_files, *, limit, **kwargs):
            return scoped[:limit]

    class _Reader:
        def inspect_packet(self, selected, *, symbol, line, window, state):
            fp = str(selected.file_path)
            res = _ok(
                "read_snippet",
                data={"mode": "symbol_body", "content": "def SufSym(): pass", "file_path": fp},
                summary="read ok",
            )
            return ReadPacket(file_path=fp, symbol=symbol, line_start=1, line_end=5), res

    class _Analyzer:
        def analyze(self, instruction, intent, context_blocks, **kwargs):
            return UnderstandingResult(
                relevance="high",
                confidence=0.95,
                sufficient=True,
                evidence_sufficiency="sufficient",
                knowledge_gaps=[],
                summary="Definition located.",
            )

    engine = ExplorationEngineV2(
        dispatcher=_Dispatcher(),
        intent_parser=_Parser(),
        selector=_Selector(),
        inspection_reader=_Reader(),
        analyzer=_Analyzer(),
        graph_expander=type("_G", (), {"expand": lambda *a, **k: ([], _ok("graph_query", data={}))})(),
    )
    result = engine.explore("find definition of SufSym", state=SimpleNamespace(context={}))
    _print_case("single-step sufficient", result)
    _assert_exploration_result_valid(result)
    assert len(result.evidence) >= 1
    assert result.metadata.termination_reason == "primary_symbol_sufficient"


def test_utility_stop_no_improvement(monkeypatch):
    """Repeated identical utility signature → no_improvement_streak after streak threshold."""
    import agent_v2.exploration.exploration_engine_v2 as emod

    monkeypatch.setattr(emod, "ENABLE_UTILITY_STOP", True)
    monkeypatch.setattr(emod, "EXPLORATION_UTILITY_NO_IMPROVEMENT_STREAK", 2)
    monkeypatch.setattr(emod, "EXPLORATION_MAX_STEPS", 12)

    u = UnderstandingResult(
        relevance="high",
        confidence=0.5,
        sufficient=False,
        evidence_sufficiency="partial",
        knowledge_gaps=["stable gap for utility probe"],
        summary="same each time",
    )

    class _Parser:
        def parse(self, instruction: str, **kwargs):
            return QueryIntent(symbols=["A"], keywords=[], intents=["debug"])

    class _Dispatcher:
        def execute(self, step, state):
            return _ok("read_snippet", data={})

        def search_batch(self, queries, state, *, mode, step_id_prefix, max_workers=4):
            return [
                _ok(
                    "search",
                    data={
                        "results": [
                            {"file_path": f"/tmp/util_{i}.py", "symbol": f"Sym{i}", "score": 0.9, "source": "grep"}
                            for i in range(3)
                        ]
                    },
                )
                for _q in queries
            ]

    class _Selector:
        def select_batch(self, instruction, intent_text, scoped, seen_files, *, limit, **kwargs):
            return scoped[:limit]

    class _Reader:
        def inspect_packet(self, selected, *, symbol, line, window, state):
            fp = str(selected.file_path)
            res = _ok(
                "read_snippet",
                data={"mode": "symbol_body", "content": "body", "file_path": fp},
                summary="read",
            )
            return ReadPacket(file_path=fp, symbol=symbol, line_start=1, line_end=3), res

    class _Analyzer:
        def analyze(self, instruction, intent, context_blocks, **kwargs):
            return u

    class _Graph:
        def expand(self, symbol, file_path, state, *, max_nodes, max_depth, **kwargs):
            return [], _ok("graph_query", data={})

    engine = ExplorationEngineV2(
        dispatcher=_Dispatcher(),
        intent_parser=_Parser(),
        selector=_Selector(),
        inspection_reader=_Reader(),
        analyzer=_Analyzer(),
        graph_expander=_Graph(),
    )
    result = engine.explore("utility plateau", state=SimpleNamespace(context={}))
    _print_case("utility stop (no improvement streak)", result)
    _assert_exploration_result_valid(result)
    assert result.metadata.termination_reason == "no_improvement_streak"


def test_max_steps_reached(monkeypatch):
    """steps_taken hits EXPLORATION_MAX_STEPS before finishing worklist."""
    import agent_v2.exploration.exploration_engine_v2 as emod

    monkeypatch.setattr(emod, "ENABLE_UTILITY_STOP", False)
    monkeypatch.setattr(emod, "EXPLORATION_MAX_STEPS", 1)

    class _Parser:
        def parse(self, instruction: str, **kwargs):
            return QueryIntent(symbols=["M"], keywords=[], intents=["debug"])

    class _Dispatcher:
        def execute(self, step, state):
            return _ok("read_snippet", data={})

        def search_batch(self, queries, state, *, mode, step_id_prefix, max_workers=4):
            return [
                _ok(
                    "search",
                    data={
                        "results": [
                            {"file_path": "/tmp/max_a.py", "symbol": "M", "score": 0.9, "source": "grep"},
                            {"file_path": "/tmp/max_b.py", "symbol": "M", "score": 0.8, "source": "grep"},
                        ]
                    },
                )
                for _q in queries
            ]

    class _Selector:
        def select_batch(self, instruction, intent_text, scoped, seen_files, *, limit, **kwargs):
            return scoped[:limit]

    class _Reader:
        def inspect_packet(self, selected, *, symbol, line, window, state):
            fp = str(selected.file_path)
            res = _ok(
                "read_snippet",
                data={"mode": "symbol_body", "content": "x=1", "file_path": fp},
                summary="read",
            )
            return ReadPacket(file_path=fp, symbol=symbol, line_start=1, line_end=2), res

    class _Analyzer:
        def analyze(self, instruction, intent, context_blocks, **kwargs):
            return UnderstandingResult(
                relevance="medium",
                confidence=0.5,
                sufficient=False,
                evidence_sufficiency="partial",
                knowledge_gaps=["still partial"],
                summary="partial",
            )

    class _Graph:
        def expand(self, symbol, file_path, state, *, max_nodes, max_depth, **kwargs):
            return [], _ok("graph_query", data={})

    engine = ExplorationEngineV2(
        dispatcher=_Dispatcher(),
        intent_parser=_Parser(),
        selector=_Selector(),
        inspection_reader=_Reader(),
        analyzer=_Analyzer(),
        graph_expander=_Graph(),
    )
    result = engine.explore("max steps cap", state=SimpleNamespace(context={}))
    _print_case("max steps reached", result)
    _assert_exploration_result_valid(result)
    assert result.metadata.termination_reason == "max_steps"
    # One inspection step plus discovery-tier rows can both appear in evidence (≤ cap).
    assert len(result.evidence) >= 1
