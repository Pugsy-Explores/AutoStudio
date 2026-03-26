import logging
import os
from types import SimpleNamespace

from agent_v2.exploration.exploration_engine_v2 import ExplorationEngineV2
from agent_v2.schemas.execution import ExecutionResult
from agent_v2.schemas.exploration import (
    ExplorationCandidate,
    ExplorationDecision,
    ExplorationTarget,
    ExplorationState,
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


def test_no_relevant_candidate_terminates_without_inspection(caplog):
    class _Parser:
        def parse(self, instruction: str, **kwargs) -> QueryIntent:
            return QueryIntent(symbols=["X"], keywords=["X"], intents=["debug"])

    class _Selector:
        def select_batch(self, instruction, intent, candidates, seen_files, *, limit, **kwargs):
            return None

    class _Reader:
        def inspect(self, selected, *, symbol, line, window, state):
            raise AssertionError("inspect must not run when selector returns None")

    class _Analyzer:
        def analyze(self, instruction, file_path, snippet) -> ExplorationDecision:
            raise AssertionError("analyzer must not run when selector returns None")

    class _Graph:
        def expand(self, symbol, file_path, state, *, max_nodes, max_depth):
            return [], _ok("graph_query", data={})

    class _Dispatcher:
        def execute(self, step, state):
            return _ok(
                "search",
                data={"results": [{"file_path": "a.py", "symbol": "X", "source": "grep"}]},
                summary="found",
            )

        def search_batch(self, queries, state, *, mode, step_id_prefix, max_workers=4):
            return [self.execute({"query": q}, state) for q in queries]

    engine = ExplorationEngineV2(
        dispatcher=_Dispatcher(),
        intent_parser=_Parser(),
        selector=_Selector(),
        inspection_reader=_Reader(),
        analyzer=_Analyzer(),
        graph_expander=_Graph(),
    )
    with caplog.at_level(logging.INFO, logger="agent_v2.exploration.exploration_engine_v2"):
        result = engine.explore("find X", state=SimpleNamespace(context={}))
    assert any("exploration.discovery" in r.message for r in caplog.records), caplog.text
    assert result.metadata.termination_reason == "no_relevant_candidate"
    assert result.metadata.completion_status == "incomplete"


def test_evidence_delta_key_is_file_symbol_read_source():
    t = ExplorationTarget(
        file_path="/repo/a.py",
        symbol="Foo",
        source="discovery",
    )
    k = ExplorationEngineV2._evidence_delta_key(
        "/abs/a.py",
        t,
        {"mode": "symbol_body"},
    )
    assert k == ("/abs/a.py", "Foo", "symbol")
    seen: set[tuple[str, str, str]] = {k}
    assert not ExplorationEngineV2._is_meaningful_new_evidence(seen, k)
    assert ExplorationEngineV2._is_meaningful_new_evidence(
        seen,
        ("/abs/a.py", "Foo", "line"),
    )


def test_read_source_for_delta_empty_for_unknown_mode():
    assert ExplorationEngineV2._read_source_for_delta({}) == ""
    assert ExplorationEngineV2._read_source_for_delta({"mode": "other"}) == ""


def test_enqueue_ranked_skips_scoper_when_capped_len_at_or_below_skip_below(monkeypatch):
    monkeypatch.setattr(
        "agent_v2.exploration.exploration_engine_v2.EXPLORATION_SCOPER_SKIP_BELOW",
        5,
    )

    class _Scoper:
        def scope(self, instruction, candidates, **kwargs):
            raise AssertionError("scoper should not run when len(capped) <= skip_below")

    class _Sel:
        def select_batch(self, instruction, intent, candidates, seen_files, *, limit, **kwargs):
            return candidates[:1]

    engine = ExplorationEngineV2(
        dispatcher=object(),
        intent_parser=object(),
        selector=_Sel(),
        inspection_reader=object(),
        analyzer=object(),
        graph_expander=object(),
        scoper=_Scoper(),
    )
    ex_state = ExplorationState(instruction="x")
    cands = [
        ExplorationCandidate(file_path=f"{i}.py", symbol="s", source="grep") for i in range(4)
    ]
    engine._enqueue_ranked(
        "instr",
        QueryIntent(symbols=[], keywords=[], intents=["debug"]),
        cands,
        ex_state,
        limit=5,
    )


def test_enqueue_ranked_calls_scoper_when_capped_len_above_skip_below(monkeypatch):
    monkeypatch.setattr(
        "agent_v2.exploration.exploration_engine_v2.EXPLORATION_SCOPER_SKIP_BELOW",
        5,
    )

    class _Scoper:
        def __init__(self):
            self.calls = 0

        def scope(self, instruction, candidates, **kwargs):
            self.calls += 1
            return candidates[:2]

    class _Sel:
        def select_batch(self, instruction, intent, candidates, seen_files, *, limit, **kwargs):
            return candidates

    scoper = _Scoper()
    engine = ExplorationEngineV2(
        dispatcher=object(),
        intent_parser=object(),
        selector=_Sel(),
        inspection_reader=object(),
        analyzer=object(),
        graph_expander=object(),
        scoper=scoper,
    )
    ex_state = ExplorationState(instruction="x")
    cands = [
        ExplorationCandidate(file_path=f"{i}.py", symbol="s", source="grep") for i in range(6)
    ]
    engine._enqueue_ranked(
        "instr",
        QueryIntent(symbols=[], keywords=[], intents=["debug"]),
        cands,
        ex_state,
        limit=5,
    )
    assert scoper.calls == 1


def test_enqueue_ranked_skips_all_when_already_explored(monkeypatch):
    monkeypatch.setattr(
        "agent_v2.exploration.exploration_engine_v2.EXPLORATION_SCOPER_SKIP_BELOW",
        5,
    )

    class _Scoper:
        def scope(self, instruction, candidates, **kwargs):
            raise AssertionError("scoper must not run when all candidates are explored")

    class _Sel:
        def select_batch(self, instruction, intent, candidates, seen_files, *, limit, **kwargs):
            raise AssertionError("selector must not run when queue is empty")

    engine = ExplorationEngineV2(
        dispatcher=object(),
        intent_parser=object(),
        selector=_Sel(),
        inspection_reader=object(),
        analyzer=object(),
        graph_expander=object(),
        scoper=_Scoper(),
    )
    ex_state = ExplorationState(instruction="x")
    base_root = os.environ.get("SERENA_PROJECT_DIR") or os.getcwd()
    canon = ExplorationEngineV2._canonical_path("0.py", base_root=base_root)
    ex_state.explored_location_keys.add((canon, "s"))
    cands = [
        ExplorationCandidate(file_path="0.py", symbol="s", source="grep"),
    ]
    out = engine._enqueue_ranked(
        "instr",
        QueryIntent(symbols=[], keywords=[], intents=["debug"]),
        cands,
        ex_state,
        limit=5,
    )
    assert out is True


def test_enqueue_targets_skips_already_explored_expansion_path():
    """Expansion and discovery both use _may_enqueue; explored keys must not re-enter queue."""
    engine = ExplorationEngineV2(
        dispatcher=object(),
        intent_parser=object(),
        selector=object(),
        inspection_reader=object(),
        analyzer=object(),
        graph_expander=object(),
    )
    ex_state = ExplorationState(instruction="x")
    base_root = os.environ.get("SERENA_PROJECT_DIR") or os.getcwd()
    canon = ExplorationEngineV2._canonical_path("dup.py", base_root=base_root)
    ex_state.explored_location_keys.add((canon, "sym"))
    engine._enqueue_targets(
        ex_state,
        [
            ExplorationTarget(file_path=canon, symbol="sym", source="expansion"),
        ],
    )
    assert ex_state.pending_targets == []


def test_gap_filter_rejects_generic_and_attempted():
    engine = ExplorationEngineV2(
        dispatcher=object(),
        intent_parser=object(),
        selector=object(),
        inspection_reader=object(),
        analyzer=object(),
        graph_expander=object(),
    )
    ex_state = ExplorationState(instruction="x")
    ex_state.attempted_gaps.add("missing caller path")
    decision = ExplorationDecision(status="partial", needs=["more_code"], reason="r", next_action="stop")
    understanding = UnderstandingResult(
        relevance="medium",
        sufficient=False,
        evidence_sufficiency="partial",
        knowledge_gaps=["missing caller path", "need more context", "missing callee chain"],
        summary="s",
    )
    out = engine._apply_gap_driven_decision(decision, understanding, ex_state)
    assert out.next_action == "expand"
    assert "callees" in out.needs
    assert "missing callee chain" in ex_state.attempted_gaps
    assert "missing caller path" in ex_state.attempted_gaps


def test_refine_cooldown_forces_expand_when_eligible():
    engine = ExplorationEngineV2(
        dispatcher=object(),
        intent_parser=object(),
        selector=object(),
        inspection_reader=object(),
        analyzer=object(),
        graph_expander=object(),
    )
    ex_state = ExplorationState(instruction="x", refine_used_last_step=True)
    decision = ExplorationDecision(
        status="partial",
        needs=["callers"],
        reason="r",
        next_action="refine",
    )
    target = ExplorationTarget(file_path="/tmp/a.py", symbol="Foo", source="discovery")
    action = engine._apply_refine_cooldown("refine", decision, target, ex_state)
    assert action == "expand"


def test_utility_signal_binary_improvement_and_stop():
    engine = ExplorationEngineV2(
        dispatcher=object(),
        intent_parser=object(),
        selector=object(),
        inspection_reader=object(),
        analyzer=object(),
        graph_expander=object(),
    )
    ex_state = ExplorationState(instruction="x")
    u1 = UnderstandingResult(
        relevance="low",
        sufficient=False,
        evidence_sufficiency="partial",
        knowledge_gaps=["gap_a", "gap_b"],
        summary="s1",
    )
    stop1, _ = engine._update_utility_and_should_stop(u1, ex_state)
    assert stop1 is False
    u2 = UnderstandingResult(
        relevance="low",
        sufficient=False,
        evidence_sufficiency="partial",
        knowledge_gaps=["gap_a", "gap_b"],
        summary="s2",
    )
    stop2, _ = engine._update_utility_and_should_stop(u2, ex_state)
    assert stop2 is False
    stop3, reason3 = engine._update_utility_and_should_stop(u2, ex_state)
    assert stop3 is True
    assert reason3 == "no_improvement_streak"


def test_enqueue_targets_prioritizes_and_dedupes_edges():
    engine = ExplorationEngineV2(
        dispatcher=object(),
        intent_parser=object(),
        selector=object(),
        inspection_reader=object(),
        analyzer=object(),
        graph_expander=object(),
    )
    ex_state = ExplorationState(
        instruction="x",
        current_target=ExplorationTarget(file_path="/tmp/src.py", symbol="A", source="discovery"),
    )
    ex_state.seen_symbols.add("Known")
    t1 = ExplorationTarget(file_path="/tmp/b.py", symbol="Known", source="expansion")
    t2 = ExplorationTarget(file_path="/tmp/c.py", symbol="New", source="expansion")
    engine._enqueue_targets(ex_state, [t1, t2, t2])
    assert len(ex_state.pending_targets) == 2
    assert ex_state.pending_targets[0].symbol == "New"


def test_gap_mapping_caller_direction_and_callee():
    engine = ExplorationEngineV2(
        dispatcher=object(),
        intent_parser=object(),
        selector=object(),
        inspection_reader=object(),
        analyzer=object(),
        graph_expander=object(),
    )
    ex_state = ExplorationState(instruction="x")
    decision = ExplorationDecision(status="partial", needs=["more_code"], reason="r", next_action="stop")
    u = UnderstandingResult(
        relevance="medium",
        sufficient=False,
        evidence_sufficiency="partial",
        knowledge_gaps=["missing caller path for Foo"],
        summary="s",
    )
    out = engine._apply_gap_driven_decision(decision, u, ex_state)
    assert out.next_action == "expand"
    assert "callers" in out.needs
    assert ex_state.expand_direction_hint == "callers"

    ex_state2 = ExplorationState(instruction="x")
    u2 = UnderstandingResult(
        relevance="medium",
        sufficient=False,
        evidence_sufficiency="partial",
        knowledge_gaps=["missing callee chain"],
        summary="s",
    )
    out2 = engine._apply_gap_driven_decision(decision, u2, ex_state2)
    assert out2.next_action == "expand"
    assert "callees" in out2.needs
    assert ex_state2.expand_direction_hint == "callees"


def test_gap_mapping_definition_triggers_refine_with_keyword_inject():
    engine = ExplorationEngineV2(
        dispatcher=object(),
        intent_parser=object(),
        selector=object(),
        inspection_reader=object(),
        analyzer=object(),
        graph_expander=object(),
    )
    ex_state = ExplorationState(instruction="x")
    decision = ExplorationDecision(status="partial", needs=["more_code"], reason="r", next_action="stop")
    u = UnderstandingResult(
        relevance="medium",
        sufficient=False,
        evidence_sufficiency="partial",
        knowledge_gaps=["missing definition of Bar"],
        summary="s",
    )
    out = engine._apply_gap_driven_decision(decision, u, ex_state)
    assert out.next_action == "refine"
    assert ex_state.discovery_keyword_inject
    assert "definition" in ex_state.discovery_keyword_inject


def test_should_expand_respects_depth_cap(monkeypatch):
    monkeypatch.setattr(
        "agent_v2.exploration.exploration_engine_v2.EXPLORATION_EXPAND_MAX_DEPTH",
        1,
    )
    engine = ExplorationEngineV2(
        dispatcher=object(),
        intent_parser=object(),
        selector=object(),
        inspection_reader=object(),
        analyzer=object(),
        graph_expander=object(),
    )
    ex_state = ExplorationState(instruction="x", expansion_depth=1)
    decision = ExplorationDecision(
        status="partial",
        needs=["callers"],
        reason="r",
        next_action="expand",
    )
    target = ExplorationTarget(file_path="/tmp/a.py", symbol="Foo", source="discovery")
    assert False is engine._should_expand("expand", decision, target, ex_state)


def test_prefilter_expansion_targets_and_attempted():
    engine = ExplorationEngineV2(
        dispatcher=object(),
        intent_parser=object(),
        selector=object(),
        inspection_reader=object(),
        analyzer=object(),
        graph_expander=object(),
    )
    ex_state = ExplorationState(instruction="x")
    base_root = os.environ.get("SERENA_PROJECT_DIR") or os.getcwd()
    canon = ExplorationEngineV2._canonical_path("seen.py", base_root=base_root)
    ex_state.explored_location_keys.add((canon, "S"))
    t_skip = ExplorationTarget(file_path=canon, symbol="S", source="expansion")
    t_ok = ExplorationTarget(file_path="/tmp/new.py", symbol="N", source="expansion")
    gk = "gap|bundle"
    out = engine._prefilter_expansion_targets(ex_state, [t_skip, t_ok], gk)
    assert len(out) == 1
    assert out[0].symbol == "N"
    tri = (gk, engine._make_location_key(out[0].file_path, out[0].symbol)[0], "N")
    assert tri in ex_state.attempted_gap_targets


def test_graph_expander_direction_filters_callers(monkeypatch):
    from agent_v2.exploration.graph_expander import GraphExpander

    rows = [
        {"file": "/a.py", "symbol": "X", "line": 1, "snippet": "caller of foo"},
        {"file": "/b.py", "symbol": "Y", "line": 2, "snippet": "callee of foo"},
        {"file": "/c.py", "symbol": "Z", "line": 3, "snippet": "related"},
    ]

    def fake_fetch(sym, project_root, top_k=15):
        return rows, []

    monkeypatch.setattr(
        "agent.retrieval.adapters.graph.fetch_graph",
        fake_fetch,
    )
    g = GraphExpander(dispatcher=object())
    state = SimpleNamespace(context={"project_root": "/tmp"})
    out, _res = g.expand(
        "foo",
        "/anchor.py",
        state,
        max_nodes=10,
        max_depth=2,
        direction_hint="callers",
    )
    assert len(out) == 1
    assert out[0].symbol == "X"


def test_graph_expander_direction_empty_falls_back_to_related(monkeypatch):
    from agent_v2.exploration.graph_expander import GraphExpander

    rows = [
        {"file": "/c.py", "symbol": "Z", "line": 3, "snippet": "related"},
    ]

    def fake_fetch(sym, project_root, top_k=15):
        return rows, []

    monkeypatch.setattr(
        "agent.retrieval.adapters.graph.fetch_graph",
        fake_fetch,
    )
    g = GraphExpander(dispatcher=object())
    state = SimpleNamespace(context={"project_root": "/tmp"})
    out, _res = g.expand(
        "foo",
        "/anchor.py",
        state,
        max_nodes=10,
        max_depth=2,
        direction_hint="callers",
    )
    assert len(out) == 1
    assert out[0].symbol == "Z"


def test_discovery_merges_keyword_inject():
    engine = ExplorationEngineV2(
        dispatcher=object(),
        intent_parser=object(),
        selector=object(),
        inspection_reader=object(),
        analyzer=object(),
        graph_expander=object(),
    )
    ex_state = ExplorationState(instruction="x")
    ex_state.discovery_keyword_inject = ["Bar", "Bar"]
    intent = QueryIntent(symbols=[], keywords=["foo"], intents=["x"])
    calls = []

    def fake_search_batch(queries, state, *, mode, step_id_prefix, max_workers=4):
        calls.append((mode, queries))
        return [
            _ok(
                "search",
                data={"results": [{"file_path": "a.py", "symbol": "X", "score": 0.5}]},
                summary="ok",
            )
            for _q in queries
        ]

    engine._dispatcher = SimpleNamespace(search_batch=fake_search_batch)
    engine._discovery(intent, SimpleNamespace(), ex_state)
    assert ex_state.discovery_keyword_inject == []
    text_modes = [c for c in calls if c[0] == "text"]
    assert text_modes
    assert "Bar" in text_modes[0][1]


def test_e2e_expand_three_targets_increments_depth_once_and_clears_direction_hint(monkeypatch):
    """
    One graph expand returning three valid targets must increment expansion_depth by 1 (not 3).
    expand_direction_hint must be cleared after that expand (no stale hint for later hops).
    """
    import agent_v2.exploration.exploration_engine_v2 as emod

    monkeypatch.setattr(emod, "EXPLORATION_MAX_STEPS", 2)
    monkeypatch.setattr(emod, "ENABLE_UTILITY_STOP", False)

    captured_state: list = []

    class _SpyExplorationState(ExplorationState):
        def model_post_init(self, __context):
            captured_state.clear()
            captured_state.append(self)

    monkeypatch.setattr(emod, "ExplorationState", _SpyExplorationState)

    root = os.getcwd()
    seed_fp = os.path.join(root, "seed_e2e_depth_probe.py")

    class _Parser:
        def parse(self, instruction: str, **kwargs):
            return QueryIntent(symbols=["RootSym"], keywords=[], intents=["debug"])

    class _Dispatcher:
        def execute(self, step, state):
            return _ok(
                "read_snippet",
                data={"mode": "symbol_body", "content": "def RootSym(): pass", "file_path": seed_fp},
            )

        def search_batch(self, queries, state, *, mode, step_id_prefix, max_workers=4):
            return [
                _ok(
                    "search",
                    data={
                        "results": [
                            {
                                "file_path": seed_fp,
                                "symbol": "RootSym",
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
                data={"mode": "symbol_body", "content": "body", "file_path": fp},
            )
            pkt = ReadPacket(file_path=fp, symbol=symbol, read_source="symbol", content="c")
            return pkt, res

    class _Analyzer:
        def __init__(self):
            self._n = 0

        def analyze(self, instruction, intent, context_blocks, **kwargs):
            self._n += 1
            if self._n == 1:
                return UnderstandingResult(
                    relevance="high",
                    confidence=0.5,
                    sufficient=False,
                    evidence_sufficiency="partial",
                    knowledge_gaps=["missing caller path for Zed"],
                    summary="need callers",
                )
            return UnderstandingResult(
                relevance="high",
                confidence=0.9,
                sufficient=True,
                evidence_sufficiency="sufficient",
                knowledge_gaps=[],
                summary="done",
            )

    class _Graph:
        def __init__(self):
            self.expand_calls = 0

        def expand(self, symbol, file_path, state, *, max_nodes, max_depth, **kwargs):
            self.expand_calls += 1
            triple = [
                ExplorationTarget(
                    file_path="/tmp/exp_e2e_depth_a.py",
                    symbol="E2A",
                    source="expansion",
                ),
                ExplorationTarget(
                    file_path="/tmp/exp_e2e_depth_b.py",
                    symbol="E2B",
                    source="expansion",
                ),
                ExplorationTarget(
                    file_path="/tmp/exp_e2e_depth_c.py",
                    symbol="E2C",
                    source="expansion",
                ),
            ]
            return triple, _ok("graph_query", data={})

    graph = _Graph()
    engine = ExplorationEngineV2(
        dispatcher=_Dispatcher(),
        intent_parser=_Parser(),
        selector=_Selector(),
        inspection_reader=_Reader(),
        analyzer=_Analyzer(),
        graph_expander=graph,
    )
    engine.explore("debug RootSym", state=SimpleNamespace(context={"project_root": root}))

    assert captured_state, "exploration state should be captured"
    ex = captured_state[0]
    assert ex.expansion_depth == 1, "expected single increment per expand call, not per target"
    assert graph.expand_calls == 1
    assert ex.expand_direction_hint is None
