"""
Integration validation: ExplorationWorkingMemory captures critical loop signals.

Run with visible output:
  pytest -s tests/test_exploration_working_memory_loop_signals.py
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

from agent_v2.exploration.exploration_engine_v2 import ExplorationEngineV2
from agent_v2.exploration.exploration_working_memory import (
    ExplorationWorkingMemory,
    _is_generic_gap,
    file_symbol_key,
)
from agent_v2.schemas.execution import ExecutionResult
from agent_v2.schemas.exploration import (
    ExplorationTarget,
    QueryIntent,
    ReadPacket,
    UnderstandingResult,
)
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


def _mem_evidence_keys(memory: ExplorationWorkingMemory) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for row in memory.all_evidence_rows():
        fp = str(row.get("file") or "")
        sym = row.get("symbol")
        sk = (sym or "").strip() or "__file__"
        out.add((fp, sk))
    return out


def _print_run(
    title: str,
    instruction: str,
    inspected: list[tuple[str, str]],
    memory: ExplorationWorkingMemory,
) -> None:
    snap = memory.get_summary()
    ev_all = memory.all_evidence_rows()
    keys = sorted(_mem_evidence_keys(memory))
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")
    print("instruction:", instruction)
    print("inspected targets (canonical_path, symbol):", json.dumps(inspected, indent=2))
    print("memory.evidence count (uncapped):", len(ev_all))
    print("memory.evidence keys (file, symbol_key):", json.dumps(keys, indent=2))
    print("memory.relationships:", json.dumps(snap.get("relationships") or [], indent=2))
    print("memory.gaps:", json.dumps(snap.get("gaps") or [], indent=2))


def _assert_inspected_in_memory(memory: ExplorationWorkingMemory, inspected: list[tuple[str, str]]) -> None:
    keys = _mem_evidence_keys(memory)
    for canon, sym in inspected:
        sk = (sym or "").strip() or "__file__"
        assert (canon, sk) in keys, f"missing memory evidence for inspected ({canon!r}, {sym!r}); have {sorted(keys)}"


def _assert_no_generic_gaps(memory: ExplorationWorkingMemory) -> None:
    for row in memory.get_summary().get("gaps") or []:
        desc = str(row.get("description") or "")
        assert not _is_generic_gap(desc.lower()), f"generic gap leaked into memory: {desc!r}"


@pytest.fixture
def repo_root():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def test_simple_symbol_lookup_memory_integrity(repo_root, monkeypatch):
    """Single hop: discovery → inspect → analyze; evidence for inspected symbol must exist."""
    monkeypatch.setattr(
        "agent_v2.exploration.exploration_engine_v2.ENABLE_UTILITY_STOP",
        False,
    )
    wm_path = os.path.join(repo_root, "agent_v2", "exploration", "exploration_working_memory.py")
    assert os.path.isfile(wm_path), wm_path

    inspected: list[tuple[str, str]] = []

    class _Parser:
        def parse(self, instruction: str, **kwargs):
            return QueryIntent(symbols=["file_symbol_key"], keywords=[], intents=["find_definition"])

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
                                "file_path": wm_path,
                                "symbol": "file_symbol_key",
                                "score": 0.95,
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
            canon = ExplorationEngineV2._canonical_path(fp, base_root=repo_root)
            inspected.append((canon, symbol or ""))
            res = _ok(
                "read_snippet",
                data={
                    "mode": "symbol_body",
                    "content": "def file_symbol_key(a, b): ...",
                    "file_path": fp,
                },
                summary="bounded read ok",
            )
            pkt = ReadPacket(
                file_path=fp,
                symbol=symbol,
                line_start=10,
                line_end=40,
            )
            return pkt, res

    class _Analyzer:
        def analyze(self, instruction, intent, context_blocks, **kwargs):
            return UnderstandingResult(
                relevance="high",
                confidence=0.82,
                sufficient=True,
                evidence_sufficiency="sufficient",
                knowledge_gaps=[],
                summary="file_symbol_key builds stable keys for evidence rows.",
            )

    engine = ExplorationEngineV2(
        dispatcher=_Dispatcher(),
        intent_parser=_Parser(),
        selector=_Selector(),
        inspection_reader=_Reader(),
        analyzer=_Analyzer(),
        graph_expander=type("_G", (), {"expand": lambda *a, **k: ([], _ok("graph_query", data={}))})(),
    )
    instruction = f"Where is file_symbol_key defined? (repo file {wm_path})"
    engine.explore(instruction, state=SimpleNamespace(context={"project_root": repo_root}))
    memory = engine.last_working_memory
    assert memory is not None

    _print_run("simple symbol lookup", instruction, inspected, memory)
    _assert_inspected_in_memory(memory, inspected)
    assert not (memory.get_summary().get("relationships") or [])


def test_multihop_expansion_relationships_in_memory(repo_root, monkeypatch):
    """Expansion with graph payload → at least one relationship edge stored."""
    monkeypatch.setattr(
        "agent_v2.exploration.exploration_engine_v2.ENABLE_UTILITY_STOP",
        False,
    )
    monkeypatch.setattr(
        "agent_v2.exploration.exploration_engine_v2.EXPLORATION_MAX_STEPS",
        4,
    )
    anchor = os.path.join(repo_root, "agent_v2", "exploration", "exploration_engine_v2.py")
    caller_fp = os.path.join(repo_root, "agent_v2", "config.py")
    assert os.path.isfile(anchor), anchor

    inspected: list[tuple[str, str]] = []

    class _Parser:
        def parse(self, instruction: str, **kwargs):
            return QueryIntent(symbols=["_explore_inner"], keywords=[], intents=["debug"])

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
                                "file_path": anchor,
                                "symbol": "_explore_inner",
                                "score": 0.92,
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
            canon = ExplorationEngineV2._canonical_path(fp, base_root=repo_root)
            inspected.append((canon, symbol or ""))
            res = _ok(
                "read_snippet",
                data={"mode": "symbol_body", "content": "def _explore_inner(...):", "file_path": fp},
                summary="read anchor",
            )
            pkt = ReadPacket(file_path=fp, symbol=symbol, line_start=360, line_end=420)
            return pkt, res

    class _Analyzer:
        def analyze(self, instruction, intent, context_blocks, **kwargs):
            return UnderstandingResult(
                relevance="high",
                confidence=0.55,
                sufficient=False,
                evidence_sufficiency="partial",
                knowledge_gaps=["missing caller path for _explore_inner"],
                summary="need callers",
            )

    canon_caller = ExplorationEngineV2._canonical_path(caller_fp, base_root=repo_root)
    anchor_canon = ExplorationEngineV2._canonical_path(anchor, base_root=repo_root)

    class _Graph:
        def expand(self, symbol, file_path, state, *, max_nodes, max_depth, **kwargs):
            data = {
                "callers": [
                    {"file_path": canon_caller, "symbol": "get_project_root"},
                ],
                "callees": [],
                "related": [],
            }
            triple = [
                ExplorationTarget(file_path=caller_fp, symbol="get_project_root", source="expansion"),
            ]
            return triple, _ok("graph_query", data=data, summary="expanded callers")

    engine = ExplorationEngineV2(
        dispatcher=_Dispatcher(),
        intent_parser=_Parser(),
        selector=_Selector(),
        inspection_reader=_Reader(),
        analyzer=_Analyzer(),
        graph_expander=_Graph(),
    )
    instruction = "Who calls _explore_inner in exploration_engine_v2?"
    engine.explore(instruction, state=SimpleNamespace(context={"project_root": repo_root}))
    memory = engine.last_working_memory
    assert memory is not None

    _print_run("multi-hop (caller expansion)", instruction, inspected, memory)
    _assert_inspected_in_memory(memory, inspected)
    rels = memory.get_summary().get("relationships") or []
    assert len(rels) >= 1, "expected at least one relationship when expand_data has callers"
    from_k = file_symbol_key(anchor_canon, "_explore_inner")
    to_k = file_symbol_key(canon_caller, "get_project_root")
    assert any(
        r.get("from") == from_k and r.get("to") == to_k and r.get("type") == "callers" for r in rels
    ), rels


def test_partial_incomplete_gaps_filtered_and_specific_retained(repo_root, monkeypatch):
    """Analyzer emits generic + specific gaps: memory keeps only non-generic; gaps list has no placeholders."""
    monkeypatch.setattr(
        "agent_v2.exploration.exploration_engine_v2.ENABLE_UTILITY_STOP",
        False,
    )
    cfg = os.path.join(repo_root, "agent_v2", "config.py")
    assert os.path.isfile(cfg), cfg

    inspected: list[tuple[str, str]] = []

    class _Parser:
        def parse(self, instruction: str, **kwargs):
            return QueryIntent(symbols=["DISCOVERY_SYMBOL_CAP"], keywords=[], intents=["debug"])

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
                                "file_path": cfg,
                                "symbol": "DISCOVERY_SYMBOL_CAP",
                                "score": 0.88,
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
            canon = ExplorationEngineV2._canonical_path(fp, base_root=repo_root)
            inspected.append((canon, symbol or ""))
            res = _ok(
                "read_snippet",
                data={"mode": "symbol_body", "content": "DISCOVERY_SYMBOL_CAP = 12", "file_path": fp},
                summary="config constant",
            )
            pkt = ReadPacket(file_path=fp, symbol=symbol, line_start=1, line_end=30)
            return pkt, res

    class _Analyzer:
        def analyze(self, instruction, intent, context_blocks, **kwargs):
            return UnderstandingResult(
                relevance="medium",
                confidence=0.6,
                sufficient=False,
                evidence_sufficiency="partial",
                knowledge_gaps=[
                    "need more context",
                    "missing wiring between DISCOVERY_SYMBOL_CAP and merge limits in discovery pipeline",
                ],
                summary="partial understanding of discovery caps.",
            )

    engine = ExplorationEngineV2(
        dispatcher=_Dispatcher(),
        intent_parser=_Parser(),
        selector=_Selector(),
        inspection_reader=_Reader(),
        analyzer=_Analyzer(),
        graph_expander=type("_G", (), {"expand": lambda *a, **k: ([], _ok("graph_query", data={}))})(),
    )
    instruction = "Explain DISCOVERY_SYMBOL_CAP vs merge behavior (incomplete)"
    engine.explore(instruction, state=SimpleNamespace(context={"project_root": repo_root}))
    memory = engine.last_working_memory
    assert memory is not None

    _print_run("partial / mixed gaps", instruction, inspected, memory)
    _assert_inspected_in_memory(memory, inspected)
    gaps = memory.get_summary().get("gaps") or []
    texts = [str(g.get("description") or "") for g in gaps]
    assert any("merge limits" in t.lower() for t in texts), texts
    assert not any("need more context" in t.lower() for t in texts)
    _assert_no_generic_gaps(memory)
