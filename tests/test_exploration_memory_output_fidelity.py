"""
FinalExplorationSchema must be a deterministic projection of ExplorationWorkingMemory.get_summary()
(tier-sorted, capped), with no dependency on the legacy in-loop evidence tuple list.

Run with output:
  pytest -s tests/test_exploration_memory_output_fidelity.py -v
"""

from __future__ import annotations

import copy
import inspect
import json
import os
from types import SimpleNamespace
from typing import Any

import pytest

from agent_v2.config import EXPLORATION_MAX_ITEMS
from agent_v2.exploration.exploration_engine_v2 import ExplorationEngineV2
from agent_v2.exploration.exploration_working_memory import ExplorationWorkingMemory, _is_generic_gap
from agent_v2.schemas.exploration import ExplorationTarget, QueryIntent, ReadPacket, UnderstandingResult
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


def _normalize_result(r: FinalExplorationSchema) -> dict[str, Any]:
    d = r.model_dump()
    d.pop("exploration_id", None)
    d["metadata"] = dict(d["metadata"])
    d["metadata"].pop("created_at", None)
    for it in d.get("evidence") or []:
        if isinstance(it, dict) and it.get("metadata"):
            it["metadata"].pop("timestamp", None)
    return d


def _memory_snapshot_for_print(memory: ExplorationWorkingMemory) -> dict[str, Any]:
    snap = memory.get_summary()
    return {
        "evidence": snap.get("evidence") or [],
        "evidence_uncapped_n": len(memory.all_evidence_rows()),
        "relationships": snap.get("relationships") or [],
        "gaps": snap.get("gaps") or [],
    }


def _row_to_expected_item_fields(ev: dict[str, Any], *, max_snippet: int = 600) -> dict[str, Any]:
    ref = str(ev.get("file") or "unknown")
    summary = str(ev.get("summary") or "")[:600]
    if not summary.strip():
        summary = "evidence recorded"
    conf = float(ev.get("confidence") or 0.0)
    score = 0.8 if conf >= 0.5 else 0.4
    rs = ev.get("read_source")
    if rs not in ("symbol", "line", "head"):
        rs = None
    snippet = str(ev.get("snippet") or "")[:max_snippet]
    tool_name = str(ev.get("tool_name") or "read_snippet")
    src = str(ev.get("source") or "evidence")
    return {
        "ref": ref,
        "summary": summary,
        "snippet": snippet,
        "read_source": rs,
        "tool_name": tool_name,
        "relevance_score": score,
        "relevance_reason_suffix": f"{src} ok",
    }


def _assert_items_match_memory_projection(
    memory: ExplorationWorkingMemory,
    result: FinalExplorationSchema,
    *,
    engine: ExplorationEngineV2,
) -> tuple[list[str], list[str]]:
    """Returns (missing, extra) human-readable diff lines for evidence mapping."""
    snap = memory.get_summary()
    evs = (snap.get("evidence") or [])[:EXPLORATION_MAX_ITEMS]
    items = result.evidence
    missing: list[str] = []
    extra: list[str] = []

    if len(items) != len(evs):
        missing.append(f"count mismatch: items={len(items)} vs snapshot evidence={len(evs)}")

    for i, ev in enumerate(evs):
        if i >= len(items):
            missing.append(f"row[{i}] {ev.get('file')!r}: no item")
            continue
        it = items[i]
        exp = _row_to_expected_item_fields(ev, max_snippet=engine.MAX_SNIPPET_CHARS)
        if it.source.ref != exp["ref"]:
            missing.append(f"item[{i}] ref: got {it.source.ref!r} want {exp['ref']!r}")
        if it.content.summary != exp["summary"]:
            missing.append(f"item[{i}] summary drift")
        if it.snippet != exp["snippet"]:
            missing.append(f"item[{i}] snippet drift")
        if it.read_source != exp["read_source"]:
            missing.append(f"item[{i}] read_source: got {it.read_source!r} want {exp['read_source']!r}")
        if it.metadata.tool_name != exp["tool_name"]:
            missing.append(f"item[{i}] tool_name drift")
        if abs(it.relevance.score - exp["relevance_score"]) > 1e-9:
            missing.append(f"item[{i}] relevance.score drift")
        if exp["relevance_reason_suffix"] not in (it.relevance.reason or ""):
            missing.append(f"item[{i}] relevance.reason drift: {it.relevance.reason!r}")

    if len(items) > len(evs):
        for j in range(len(evs), len(items)):
            extra.append(f"item[{j}] ref={items[j].source.ref!r} (no snapshot row)")

    return missing, extra


def _assert_gaps_match_memory(memory: ExplorationWorkingMemory, result: FinalExplorationSchema) -> list[str]:
    snap = memory.get_summary()
    gaps = snap.get("gaps") or []
    descs = [str(g.get("description") or "").strip() for g in gaps if str(g.get("description") or "").strip()]
    descs = descs[:6]
    errs: list[str] = []
    es = result.exploration_summary
    if descs:
        if es.knowledge_gaps != descs:
            errs.append(f"knowledge_gaps mismatch: {es.knowledge_gaps!r} vs {descs!r}")
        if es.knowledge_gaps_empty_reason is not None:
            errs.append("knowledge_gaps_empty_reason must be null when gaps non-empty")
        for g in es.knowledge_gaps:
            if _is_generic_gap(g.lower()):
                errs.append(f"generic gap in output: {g!r}")
    else:
        if es.knowledge_gaps:
            errs.append("expected empty knowledge_gaps when memory has no gaps")
        if not (es.knowledge_gaps_empty_reason or "").strip():
            errs.append("empty reason missing when gaps empty")
    return errs


def _assert_relationships_surface(memory: ExplorationWorkingMemory, result: FinalExplorationSchema) -> list[str]:
    snap = memory.get_summary()
    rels = snap.get("relationships") or []
    errs: list[str] = []
    if not rels:
        return errs
    needle = f"Recorded {len(rels)} relationship edge(s) (callers/callees/related)."
    es = result.exploration_summary
    overall = es.overall
    kf = es.key_findings
    if needle not in overall and not any(needle in x for x in kf):
        errs.append(f"relationships not reflected in summary: expected substring in overall or key_findings: {needle!r}")
    return errs


def _print_fidelity_case(title: str, memory: ExplorationWorkingMemory, result: FinalExplorationSchema) -> None:
    snap = _memory_snapshot_for_print(memory)
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")
    print("memory snapshot:", json.dumps(snap, indent=2, default=str))
    print("FinalExplorationSchema (trimmed):", json.dumps(_normalize_result(result), indent=2, default=str)[:12000])


def test_build_result_source_has_no_legacy_tuple_reads():
    src = inspect.getsource(ExplorationEngineV2._build_result_from_memory)
    assert "evidence.append" not in src
    assert "evidence.extend" not in src
    assert "_prioritize_evidence_for_items" not in src


def test_rebuild_from_memory_idempotent_with_explore(monkeypatch, repo_root):
    """Same memory + metadata → same normalized result (deterministic transform)."""
    monkeypatch.setattr("agent_v2.exploration.exploration_engine_v2.ENABLE_UTILITY_STOP", False)
    wm_path = os.path.join(repo_root, "agent_v2", "exploration", "exploration_working_memory.py")

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
            res = _ok(
                "read_snippet",
                data={"mode": "symbol_body", "content": "def file_symbol_key(): ...", "file_path": fp},
                summary="inspect",
            )
            return ReadPacket(file_path=fp, symbol=symbol, line_start=1, line_end=20), res

    class _Analyzer:
        def analyze(self, instruction, intent, context_blocks, **kwargs):
            return UnderstandingResult(
                relevance="high",
                confidence=0.9,
                sufficient=True,
                evidence_sufficiency="sufficient",
                knowledge_gaps=[],
                summary="ok",
            )

    engine = ExplorationEngineV2(
        dispatcher=_Dispatcher(),
        intent_parser=_Parser(),
        selector=_Selector(),
        inspection_reader=_Reader(),
        analyzer=_Analyzer(),
        graph_expander=type("_G", (), {"expand": lambda *a, **k: ([], _ok("graph_query", data={}))})(),
    )
    r1 = engine.explore("x", state=SimpleNamespace(context={"project_root": repo_root}))
    mem = engine.last_working_memory
    assert mem is not None
    r2 = engine._build_result_from_memory(
        mem,
        "x",
        completion_status=r1.metadata.completion_status,
        termination_reason=r1.metadata.termination_reason,
        explored_files=r1.metadata.explored_files,
        explored_symbols=r1.metadata.explored_symbols,
    )
    assert _normalize_result(r1) == _normalize_result(r2)


@pytest.fixture
def repo_root():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def test_fidelity_simple_symbol_vs_memory(repo_root, monkeypatch):
    monkeypatch.setattr("agent_v2.exploration.exploration_engine_v2.ENABLE_UTILITY_STOP", False)
    wm_path = os.path.join(repo_root, "agent_v2", "exploration", "exploration_working_memory.py")

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
            res = _ok(
                "read_snippet",
                data={"mode": "symbol_body", "content": "def file_symbol_key(): pass", "file_path": fp},
                summary="bounded read",
            )
            return ReadPacket(file_path=fp, symbol=symbol, line_start=10, line_end=40), res

    class _Analyzer:
        def analyze(self, instruction, intent, context_blocks, **kwargs):
            return UnderstandingResult(
                relevance="high",
                confidence=0.88,
                sufficient=True,
                evidence_sufficiency="sufficient",
                knowledge_gaps=[],
                summary="analyzer summary line.",
            )

    engine = ExplorationEngineV2(
        dispatcher=_Dispatcher(),
        intent_parser=_Parser(),
        selector=_Selector(),
        inspection_reader=_Reader(),
        analyzer=_Analyzer(),
        graph_expander=type("_G", (), {"expand": lambda *a, **k: ([], _ok("graph_query", data={}))})(),
    )
    instruction = "find file_symbol_key"
    result = engine.explore(instruction, state=SimpleNamespace(context={"project_root": repo_root}))
    memory = engine.last_working_memory
    assert memory is not None

    _print_fidelity_case("simple", memory, result)
    miss, extra = _assert_items_match_memory_projection(memory, result, engine=engine)
    g_errs = _assert_gaps_match_memory(memory, result)
    r_errs = _assert_relationships_surface(memory, result)
    print("diff missing:", miss)
    print("diff extra:", extra)
    assert not miss and not extra, (miss, extra)
    assert not g_errs and not r_errs, (g_errs, r_errs)


def test_fidelity_multihop_relationships_and_gaps(repo_root, monkeypatch):
    monkeypatch.setattr("agent_v2.exploration.exploration_engine_v2.ENABLE_UTILITY_STOP", False)
    monkeypatch.setattr("agent_v2.exploration.exploration_engine_v2.EXPLORATION_MAX_STEPS", 6)
    anchor = os.path.join(repo_root, "agent_v2", "exploration", "exploration_engine_v2.py")
    caller_fp = os.path.join(repo_root, "agent_v2", "config.py")
    canon_caller = ExplorationEngineV2._canonical_path(caller_fp, base_root=repo_root)

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

    class _Graph:
        def expand(self, symbol, file_path, state, *, max_nodes, max_depth, **kwargs):
            data = {
                "callers": [{"file_path": canon_caller, "symbol": "get_project_root"}],
                "callees": [],
                "related": [],
            }
            triple = [
                ExplorationTarget(file_path=caller_fp, symbol="get_project_root", source="expansion"),
            ]
            return triple, _ok("graph_query", data=data, summary="expanded")

    engine = ExplorationEngineV2(
        dispatcher=_Dispatcher(),
        intent_parser=_Parser(),
        selector=_Selector(),
        inspection_reader=_Reader(),
        analyzer=_Analyzer(),
        graph_expander=_Graph(),
    )
    instruction = "callers of _explore_inner"
    result = engine.explore(instruction, state=SimpleNamespace(context={"project_root": repo_root}))
    memory = engine.last_working_memory
    assert memory is not None

    _print_fidelity_case("multi-hop", memory, result)
    miss, extra = _assert_items_match_memory_projection(memory, result, engine=engine)
    g_errs = _assert_gaps_match_memory(memory, result)
    r_errs = _assert_relationships_surface(memory, result)
    print("diff missing:", miss)
    print("diff extra:", extra)
    assert not miss and not extra, (miss, extra)
    assert not g_errs and not r_errs, (g_errs, r_errs)
    assert memory.get_summary().get("relationships"), "fixture must have relationships"


def test_rebuild_from_rehydrated_memory_matches_live_explore(monkeypatch, repo_root):
    """
    Rehydrate a fresh WorkingMemory from get_summary() rows and rebuild — normalized result must match
    explore() (proves output is a pure function of memory snapshot, not side tables).
    """
    monkeypatch.setattr("agent_v2.exploration.exploration_engine_v2.ENABLE_UTILITY_STOP", False)
    wm_path = os.path.join(repo_root, "agent_v2", "exploration", "exploration_working_memory.py")

    class _Parser:
        def parse(self, instruction: str, **kwargs):
            return QueryIntent(symbols=["SymX"], keywords=[], intents=["debug"])

    class _Dispatcher:
        def execute(self, step, state):
            return _ok("read_snippet", data={})

        def search_batch(self, queries, state, *, mode, step_id_prefix, max_workers=4):
            return [
                _ok(
                    "search",
                    data={
                        "results": [
                            {"file_path": wm_path, "symbol": "SymX", "score": 0.9, "source": "grep"},
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
                data={"mode": "symbol_body", "content": "x", "file_path": fp},
                summary="s",
            )
            return ReadPacket(file_path=fp, symbol=symbol, line_start=1, line_end=2), res

    class _Analyzer:
        def analyze(self, instruction, intent, context_blocks, **kwargs):
            return UnderstandingResult(
                relevance="high",
                confidence=0.9,
                sufficient=True,
                evidence_sufficiency="sufficient",
                knowledge_gaps=[],
                summary="done",
            )

    engine = ExplorationEngineV2(
        dispatcher=_Dispatcher(),
        intent_parser=_Parser(),
        selector=_Selector(),
        inspection_reader=_Reader(),
        analyzer=_Analyzer(),
        graph_expander=type("_G", (), {"expand": lambda *a, **k: ([], _ok("graph_query", data={}))})(),
    )
    r0 = engine.explore("z", state=SimpleNamespace(context={"project_root": repo_root}))
    m0 = engine.last_working_memory
    assert m0 is not None
    snap = copy.deepcopy(m0.get_summary())
    m1 = ExplorationWorkingMemory(
        min_confidence=m0.min_confidence,
        max_evidence=m0.max_evidence,
        max_gaps=m0.max_gaps,
        max_relationships=m0.max_relationships,
    )
    for ev in snap.get("evidence") or []:
        m1.add_evidence(
            ev.get("symbol"),
            str(ev.get("file") or ""),
            (
                int((ev.get("line_range") or {}).get("start") or 1),
                int((ev.get("line_range") or {}).get("end") or 1),
            ),
            str(ev.get("summary") or ""),
            snippet=ev.get("snippet") or None,
            read_source=ev.get("read_source"),
            confidence=float(ev.get("confidence") or 0.0),
            source=ev.get("source") or "analyzer",
            tier=int(ev.get("tier") or 0),
            tool_name=str(ev.get("tool_name") or "read_snippet"),
        )
    for rel in snap.get("relationships") or []:
        m1.add_relationship(
            str(rel.get("from") or ""),
            str(rel.get("to") or ""),
            rel.get("type") or "related",
            confidence=float(rel.get("confidence") or 0.85),
            source="expansion",
        )
    for g in snap.get("gaps") or []:
        m1.add_gap(
            str(g.get("type") or "none"),
            str(g.get("description") or ""),
            confidence=float(g.get("confidence") or 0.5),
            source="analyzer",
        )

    r_clone = engine._build_result_from_memory(
        m1,
        "z",
        completion_status=r0.metadata.completion_status,
        termination_reason=r0.metadata.termination_reason,
        explored_files=r0.metadata.explored_files,
        explored_symbols=r0.metadata.explored_symbols,
    )
    assert _normalize_result(r0) == _normalize_result(r_clone)
