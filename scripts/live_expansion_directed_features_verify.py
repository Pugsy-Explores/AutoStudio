#!/usr/bin/env python3
"""
Live verification harness for directed expansion / multi-hop depth / redundancy controls.

What this is:
- Executable "live test" (no pytest): patches key stages, runs controlled scenarios, asserts invariants.
- No LLM and no network required (fully mocked loop); safe for CI and local smoke checks.

Patched stages (instrumentation only; semantics unchanged except where tests patch config):
- ``GraphExpander.expand()`` — record ``direction_hint``, skip-set sizes.
- ``ExplorationEngineV2._prefilter_expansion_targets()`` — record in/out counts per expand.
- Config: ``EXPLORATION_MAX_STEPS``, ``ENABLE_UTILITY_STOP``, ``ExplorationState`` (spy for final depth/hint).

What it verifies (ExplorationEngineV2 + GraphExpander contract):
- Gap-driven mapping sets needs + expand_direction_hint (or refine + discovery_keyword_inject).
- Graph expand receives direction_hint + skip_files/skip_symbols; hint cleared after expand.
- expansion_depth increments once per expand call even when multiple targets enqueue.
- Prefilter records attempted (gap, file, symbol) triples and drops duplicates.
- Discovery merges ``discovery_keyword_inject`` into text queries then clears it.

Usage:
  python3 scripts/live_expansion_directed_features_verify.py
  python3 scripts/live_expansion_directed_features_verify.py --json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from types import MethodType, SimpleNamespace
from typing import Any
from unittest.mock import patch

# Repo root on sys.path when run as ``python3 scripts/...``
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import agent_v2.exploration.exploration_engine_v2 as emod
from agent_v2.exploration.exploration_engine_v2 import ExplorationEngineV2
from agent_v2.schemas.exploration import (
    ExplorationDecision,
    ExplorationState,
    ExplorationTarget,
    QueryIntent,
    ReadPacket,
    UnderstandingResult,
)
from agent_v2.schemas.tool import ToolResult
from agent_v2.runtime.tool_mapper import map_tool_result_to_execution_result


def _ok(tool_name: str, data: dict | None = None, summary: str = "ok"):
    tr = ToolResult(tool_name=tool_name, success=True, data=data or {}, duration_ms=1)
    result = map_tool_result_to_execution_result(tr, step_id="s1")
    return result.model_copy(update={"output": result.output.model_copy(update={"summary": summary})})


def _instrument_graph_and_prefilter(
    engine: ExplorationEngineV2,
    telemetry: dict[str, Any],
) -> None:
    """Record graph expand kwargs and prefilter I/O without changing semantics."""
    orig_expand = engine._graph_expander.expand
    orig_prefilter = engine._prefilter_expansion_targets

    def w_expand(sym, fp, state, *, max_nodes, max_depth, **kwargs):
        telemetry["graph_expand_calls"].append(
            {
                "symbol": sym,
                "direction_hint": kwargs.get("direction_hint"),
                "skip_files_n": len(kwargs.get("skip_files") or ()),
                "skip_symbols_n": len(kwargs.get("skip_symbols") or ()),
            }
        )
        return orig_expand(sym, fp, state, max_nodes=max_nodes, max_depth=max_depth, **kwargs)

    def w_prefilter(self, ex_state, targets, gap_bundle_key):
        n_in = len(targets)
        out = orig_prefilter(ex_state, targets, gap_bundle_key)
        telemetry["prefilter_events"].append(
            {"in": n_in, "out": len(out), "gap_key_len": len((gap_bundle_key or "").strip())}
        )
        return out

    engine._graph_expander.expand = w_expand
    engine._prefilter_expansion_targets = MethodType(w_prefilter, engine)


def _build_e2e_mocks(root: str, telemetry: dict[str, Any]):
    """Minimal stack to reach one gap-driven expand with three synthetic expansion targets."""
    seed_fp = os.path.join(root, "seed_directed_verify.py")

    class _Parser:
        def parse(self, instruction: str, **kwargs):
            return QueryIntent(symbols=["RootSym"], keywords=[], intents=["verify"])

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
        def expand(self, symbol, file_path, state, *, max_nodes, max_depth, **kwargs):
            telemetry["graph_expand_return_n"] = 3
            return (
                [
                    ExplorationTarget(
                        file_path="/tmp/directed_verify_a.py",
                        symbol="VA",
                        source="expansion",
                    ),
                    ExplorationTarget(
                        file_path="/tmp/directed_verify_b.py",
                        symbol="VB",
                        source="expansion",
                    ),
                    ExplorationTarget(
                        file_path="/tmp/directed_verify_c.py",
                        symbol="VC",
                        source="expansion",
                    ),
                ],
                _ok("graph_query", data={}),
            )

    return _Parser(), _Dispatcher(), _Selector(), _Reader(), _Analyzer(), _Graph()


def scenario_e2e_directed_expand(root: str) -> dict[str, Any]:
    """One explore(): gap → callers hint → expand ×1 with 3 targets → depth +1; hint cleared."""
    telemetry: dict[str, Any] = {
        "graph_expand_calls": [],
        "prefilter_events": [],
        "graph_expand_return_n": 0,
    }

    captured: list[ExplorationState] = []

    class _SpyES(ExplorationState):
        def model_post_init(self, __context):
            captured.clear()
            captured.append(self)

    parser, dispatcher, selector, reader, analyzer, graph = _build_e2e_mocks(root, telemetry)
    engine = ExplorationEngineV2(
        dispatcher=dispatcher,
        intent_parser=parser,
        selector=selector,
        inspection_reader=reader,
        analyzer=analyzer,
        graph_expander=graph,
    )
    _instrument_graph_and_prefilter(engine, telemetry)

    with (
        patch.object(emod, "EXPLORATION_MAX_STEPS", 2),
        patch.object(emod, "ENABLE_UTILITY_STOP", False),
        patch.object(emod, "ExplorationState", _SpyES),
    ):
        engine.explore("verify RootSym", state=SimpleNamespace(context={"project_root": root}))

    if not captured:
        return {
            "name": "e2e_directed_expand",
            "ok": False,
            "error": "ExplorationState instance not captured (Spy hook failed)",
        }
    ex = captured[0]
    checks: dict[str, Any] = {
        "expansion_depth": ex.expansion_depth,
        "expand_direction_hint_after_run": ex.expand_direction_hint,
        "graph_expand_call_count": len(telemetry["graph_expand_calls"]),
        "first_expand_direction_hint": (
            telemetry["graph_expand_calls"][0]["direction_hint"]
            if telemetry["graph_expand_calls"]
            else None
        ),
        "first_expand_skip_sets_non_negative": bool(telemetry["graph_expand_calls"]),
        "prefilter_last_out": telemetry["prefilter_events"][-1]["out"] if telemetry["prefilter_events"] else 0,
    }

    ok = (
        ex.expansion_depth == 1
        and ex.expand_direction_hint is None
        and len(telemetry["graph_expand_calls"]) == 1
        and telemetry["graph_expand_calls"][0]["direction_hint"] == "callers"
        and telemetry["prefilter_events"]
        and telemetry["prefilter_events"][-1]["out"] == 3
    )
    return {"name": "e2e_directed_expand", "ok": ok, "checks": checks, "telemetry": telemetry}


def scenario_gap_mapping_matrix(engine: ExplorationEngineV2) -> dict[str, Any]:
    """Pure gap → decision mapping (no explore)."""
    rows = []
    matrix = [
        ("missing caller for X", "expand", "callers", None),
        ("missing callee chain", "expand", "callees", None),
        ("missing definition of Foo", "refine", None, "definition"),
    ]
    for gap, want_action, want_dir, inject_token in matrix:
        ex_state = ExplorationState(instruction="t")
        decision = ExplorationDecision(status="partial", needs=["more_code"], reason="r", next_action="stop")
        u = UnderstandingResult(
            relevance="medium",
            sufficient=False,
            evidence_sufficiency="partial",
            knowledge_gaps=[gap],
            summary="s",
        )
        out = engine._apply_gap_driven_decision(decision, u, ex_state)
        row = {
            "gap": gap,
            "next_action": out.next_action,
            "needs": list(out.needs or []),
            "hint": ex_state.expand_direction_hint,
            "inject": list(ex_state.discovery_keyword_inject),
        }
        rows.append(row)
        try:
            if want_action == "expand":
                if out.next_action != "expand":
                    raise AssertionError(f"expected expand, got {out.next_action}")
                if want_dir and ex_state.expand_direction_hint != want_dir:
                    raise AssertionError(f"expected hint {want_dir!r}, got {ex_state.expand_direction_hint!r}")
            else:
                if out.next_action != "refine":
                    raise AssertionError(f"expected refine, got {out.next_action}")
                if inject_token and inject_token not in ex_state.discovery_keyword_inject:
                    raise AssertionError(f"expected inject to contain {inject_token!r}, got {ex_state.discovery_keyword_inject}")
        except AssertionError as exc:
            return {"name": "gap_mapping_matrix", "ok": False, "rows": rows, "error": str(exc)}

    return {"name": "gap_mapping_matrix", "ok": True, "rows": rows}


def scenario_prefilter_idempotent(engine: ExplorationEngineV2) -> dict[str, Any]:
    """Same (gap_key, file, symbol) skipped on second pass."""
    ex_state = ExplorationState(instruction="t")
    gk = "gap|x"
    t = ExplorationTarget(file_path="/tmp/prefilter_once.py", symbol="PS", source="expansion")
    o1 = engine._prefilter_expansion_targets(ex_state, [t], gk)
    o2 = engine._prefilter_expansion_targets(ex_state, [t], gk)
    ok = len(o1) == 1 and len(o2) == 0
    return {
        "name": "prefilter_repeat_gap_target",
        "ok": ok,
        "first_pass_out": len(o1),
        "second_pass_out": len(o2),
    }


def scenario_discovery_keyword_inject(engine: ExplorationEngineV2) -> dict[str, Any]:
    """Engine-local inject merged into text discovery channel then cleared."""
    ex_state = ExplorationState(instruction="t")
    ex_state.discovery_keyword_inject = ["Bar", "Bar"]
    intent = QueryIntent(symbols=[], keywords=["foo"], intents=["x"])
    calls: list[tuple[str, list[str]]] = []

    def fake_search_batch(queries, state, *, mode, step_id_prefix, max_workers=4):
        calls.append((mode, list(queries)))
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
    cleared = ex_state.discovery_keyword_inject == []
    text_modes = [c for c in calls if c[0] == "text"]
    merged = bool(text_modes) and "Bar" in text_modes[0][1]
    ok = cleared and merged
    return {
        "name": "discovery_keyword_inject",
        "ok": ok,
        "inject_cleared": cleared,
        "bar_in_text_queries": merged,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="Print machine-readable JSON only.")
    args = ap.parse_args()

    root = os.getcwd()
    results: list[dict[str, Any]] = []

    engine = ExplorationEngineV2(
        dispatcher=object(),
        intent_parser=object(),
        selector=object(),
        inspection_reader=object(),
        analyzer=object(),
        graph_expander=object(),
    )

    results.append(scenario_e2e_directed_expand(root))
    results.append(scenario_gap_mapping_matrix(engine))
    results.append(scenario_prefilter_idempotent(engine))
    results.append(scenario_discovery_keyword_inject(engine))

    all_ok = all(r.get("ok") for r in results)
    payload = {"all_ok": all_ok, "project_root": root, "scenarios": results}

    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        print("# live_expansion_directed_features_verify")
        print(f"# project_root={root}")
        print(f"# all_ok={all_ok}")
        for r in results:
            print("=" * 80)
            print(json.dumps(r, indent=2, default=str))

    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
