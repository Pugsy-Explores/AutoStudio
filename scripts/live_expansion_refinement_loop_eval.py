#!/usr/bin/env python3
"""
Live behavioral evaluation for ExplorationEngineV2 expansion/refinement loop.

Scope:
- Tests ONLY post-analyzer loop control behavior.
- Uses real repo-derived symbols/files for candidates and bounded reads.
- Does NOT evaluate parser/analyzer prompt quality or reasoning text.
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from pathlib import Path
from types import MethodType, SimpleNamespace
from typing import Any

from agent_v2.exploration.exploration_engine_v2 import ExplorationEngineV2
from agent_v2.exploration.graph_expander import GraphExpander
from agent_v2.exploration.inspection_reader import InspectionReader
from agent_v2.exploration.read_router import ReadRequest, read
from agent_v2.schemas.execution import ExecutionMetadata, ExecutionOutput, ExecutionResult
from agent_v2.schemas.exploration import (
    ExplorationCandidate,
    QueryIntent,
    UnderstandingResult,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCAN_ROOTS = [PROJECT_ROOT / "agent", PROJECT_ROOT / "agent_v2"]


@dataclass(frozen=True)
class SymbolEntry:
    file_path: str
    symbol: str
    line_start: int
    line_end: int
    kind: str


@dataclass(frozen=True)
class LoopEvalCase:
    name: str
    instruction: str
    seed_symbols: list[SymbolEntry]
    analyzer_script: list[dict[str, Any]]
    expected: dict[str, Any]
    force_refine_actions: int = 0


class _ScriptedIntentParser:
    def parse(self, instruction: str, **kwargs: Any) -> QueryIntent:
        words = [w.strip(" ,.:()[]{}").lower() for w in instruction.split()]
        words = [w for w in words if w]
        return QueryIntent(
            symbols=[],
            keywords=list(dict.fromkeys(words[:6])),
            intents=["understand_flow"],
            regex_patterns=[],
        )


class _PassSelector:
    def select_batch(
        self,
        instruction: str,
        intent: str,
        candidates: list[ExplorationCandidate],
        seen_files: set[str],
        *,
        limit: int,
        **kwargs: Any,
    ) -> list[ExplorationCandidate]:
        return list(candidates[:limit])


class _ScriptedAnalyzer:
    def __init__(self, script: list[dict[str, Any]]):
        self._script = script
        self._idx = 0

    def analyze(self, instruction: str, intent: str, context_blocks: list[Any], **kwargs: Any) -> UnderstandingResult:
        if self._idx < len(self._script):
            payload = self._script[self._idx]
            self._idx += 1
        else:
            payload = self._script[-1]
        return UnderstandingResult.model_validate(payload)


class _ReadDispatcher:
    def execute(self, step: dict, state: Any) -> ExecutionResult:
        args = step.get("_react_args") or {}
        req = ReadRequest(
            path=str(args.get("path") or ""),
            symbol=args.get("symbol"),
            line=args.get("line"),
            window=int(args.get("window") or 80),
        )
        payload = read(req, state=state)
        return ExecutionResult(
            step_id=str(step.get("id") or "read_eval"),
            success=True,
            status="success",
            output=ExecutionOutput(data=payload, summary="read_snippet"),
            error=None,
            metadata=ExecutionMetadata(tool_name="read_snippet", duration_ms=1, timestamp="eval"),
        )

    def search_batch(
        self,
        queries: list[str],
        state: Any,
        *,
        mode: str,
        step_id_prefix: str,
        max_workers: int = 4,
    ) -> list[ExecutionResult]:
        catalog = getattr(state, "signal_catalog", [])
        results: list[ExecutionResult] = []
        for i, _q in enumerate(queries):
            rows = []
            for e in catalog[:8]:
                rows.append(
                    {
                        "file_path": e.file_path,
                        "file": e.file_path,
                        "symbol": e.symbol,
                        "snippet": f"{e.kind} {e.symbol}",
                        "score": 0.9 - (0.05 * min(i, 5)),
                    }
                )
            results.append(
                ExecutionResult(
                    step_id=f"{step_id_prefix}_{i}",
                    success=True,
                    status="success",
                    output=ExecutionOutput(data={"results": rows}, summary=f"search_{mode}"),
                    error=None,
                    metadata=ExecutionMetadata(tool_name="search", duration_ms=1, timestamp="eval"),
                )
            )
        return results


def _iter_python_files() -> list[Path]:
    files: list[Path] = []
    for root in SCAN_ROOTS:
        if not root.exists():
            continue
        for p in root.rglob("*.py"):
            rel = p.relative_to(PROJECT_ROOT).as_posix()
            if "/tests/" in rel:
                continue
            files.append(p)
    return sorted(files)


def _collect_signals() -> dict[str, list[SymbolEntry]]:
    functions: list[SymbolEntry] = []
    classes: list[SymbolEntry] = []
    modules: set[str] = set()
    for file_path in _iter_python_files():
        rel = file_path.relative_to(PROJECT_ROOT).as_posix()
        for token in rel.replace(".py", "").split("/"):
            if token and token != "__init__":
                modules.add(token)
        try:
            tree = ast.parse(file_path.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            continue
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
                functions.append(
                    SymbolEntry(
                        file_path=rel,
                        symbol=node.name,
                        line_start=max(1, int(node.lineno)),
                        line_end=max(int(node.lineno), int(getattr(node, "end_lineno", node.lineno))),
                        kind="function",
                    )
                )
            elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
                classes.append(
                    SymbolEntry(
                        file_path=rel,
                        symbol=node.name,
                        line_start=max(1, int(node.lineno)),
                        line_end=max(int(node.lineno), int(getattr(node, "end_lineno", node.lineno))),
                        kind="class",
                    )
                )
    return {
        "functions": sorted(functions, key=lambda x: (x.file_path, x.symbol)),
        "classes": sorted(classes, key=lambda x: (x.file_path, x.symbol)),
        "modules": [SymbolEntry(m, m, 1, 1, "module") for m in sorted(modules)],
    }


def _pick(entries: list[SymbolEntry], i: int, fallback: SymbolEntry) -> SymbolEntry:
    if not entries:
        return fallback
    return entries[i % len(entries)]


def _build_cases(signals: dict[str, list[SymbolEntry]]) -> list[LoopEvalCase]:
    fn0 = _pick(signals["functions"], 0, SymbolEntry("agent_v2/config.py", "get_config", 1, 30, "function"))
    fn1 = _pick(signals["functions"], 3, fn0)
    cls0 = _pick(signals["classes"], 0, SymbolEntry("agent_v2/runtime/mode_manager.py", "ModeManager", 1, 40, "class"))
    mod0 = _pick(
        signals["classes"],
        1,
        SymbolEntry("agent_v2/runtime/mode_manager.py", "ModeManager", 1, 40, "class"),
    )
    return [
        LoopEvalCase(
            name="gap_driven_expansion",
            instruction=f"trace usage of {fn0.symbol}",
            seed_symbols=[fn0, fn1],
            analyzer_script=[
                {
                    "relevance": "low",
                    "confidence": 0.3,
                    "sufficient": False,
                    "evidence_sufficiency": "insufficient",
                    "knowledge_gaps": [f"missing caller chain for {fn0.symbol}"],
                    "summary": "need callers",
                },
                {
                    "relevance": "medium",
                    "confidence": 0.5,
                    "sufficient": True,
                    "evidence_sufficiency": "sufficient",
                    "knowledge_gaps": [],
                    "summary": "enough",
                },
            ],
            expected={"must_expand": True},
        ),
        LoopEvalCase(
            name="gap_filtering",
            instruction=f"understand flow of {cls0.symbol}",
            seed_symbols=[cls0, fn0],
            analyzer_script=[
                {
                    "relevance": "high",
                    "confidence": 0.6,
                    "sufficient": False,
                    "evidence_sufficiency": "partial",
                    "knowledge_gaps": ["need more context", "need more context", f"missing callee path for {cls0.symbol}"],
                    "summary": "mixed gaps",
                },
                {
                    "relevance": "high",
                    "confidence": 0.7,
                    "sufficient": True,
                    "evidence_sufficiency": "sufficient",
                    "knowledge_gaps": [],
                    "summary": "resolved",
                },
            ],
            expected={"generic_gap_ignored": True},
        ),
        LoopEvalCase(
            name="refine_cooldown",
            instruction=f"find where {mod0.symbol} is used",
            seed_symbols=[fn1, fn0],
            analyzer_script=[
                {
                    "relevance": "medium",
                    "confidence": 0.4,
                    "sufficient": False,
                    "evidence_sufficiency": "partial",
                    "knowledge_gaps": [f"missing caller chain for {fn1.symbol}"],
                    "summary": "needs caller path",
                },
                {
                    "relevance": "medium",
                    "confidence": 0.4,
                    "sufficient": False,
                    "evidence_sufficiency": "partial",
                    "knowledge_gaps": [f"missing caller chain for {fn1.symbol}"],
                    "summary": "still needs caller path",
                },
                {
                    "relevance": "medium",
                    "confidence": 0.5,
                    "sufficient": True,
                    "evidence_sufficiency": "sufficient",
                    "knowledge_gaps": [],
                    "summary": "resolved",
                },
            ],
            expected={"refine_then_forced_expand": True},
            force_refine_actions=2,
        ),
        LoopEvalCase(
            name="utility_stop",
            instruction=f"trace usage of {fn1.symbol}",
            seed_symbols=[fn1, fn0],
            analyzer_script=[
                {
                    "relevance": "medium",
                    "confidence": 0.4,
                    "sufficient": False,
                    "evidence_sufficiency": "partial",
                    "knowledge_gaps": [f"missing caller for {fn1.symbol}", "missing callee flow"],
                    "summary": "partial",
                },
                {
                    "relevance": "medium",
                    "confidence": 0.4,
                    "sufficient": False,
                    "evidence_sufficiency": "partial",
                    "knowledge_gaps": [f"missing caller for {fn1.symbol}", "missing callee flow"],
                    "summary": "partial again",
                },
                {
                    "relevance": "medium",
                    "confidence": 0.4,
                    "sufficient": False,
                    "evidence_sufficiency": "partial",
                    "knowledge_gaps": [f"missing caller for {fn1.symbol}", "missing callee flow"],
                    "summary": "still partial",
                },
            ],
            expected={"utility_early_stop": True},
        ),
        LoopEvalCase(
            name="duplicate_prevention_and_priority",
            instruction=f"understand flow of {fn0.symbol}",
            seed_symbols=[fn0, fn1, cls0],
            analyzer_script=[
                {
                    "relevance": "high",
                    "confidence": 0.6,
                    "sufficient": False,
                    "evidence_sufficiency": "partial",
                    "knowledge_gaps": [f"missing caller chain for {fn0.symbol}"],
                    "summary": "expand",
                },
                {
                    "relevance": "high",
                    "confidence": 0.7,
                    "sufficient": True,
                    "evidence_sufficiency": "sufficient",
                    "knowledge_gaps": [],
                    "summary": "done",
                },
            ],
            expected={"dedupe_and_priority": True},
        ),
        LoopEvalCase(
            name="medium_multihop_chain_resolution",
            instruction=(
                f"Trace multi-hop call graph for {fn0.symbol} through callers and downstream flow; "
                f"identify the dependency handoff points."
            ),
            seed_symbols=[fn0, fn1, cls0, mod0],
            analyzer_script=[
                {
                    "relevance": "medium",
                    "confidence": 0.45,
                    "sufficient": False,
                    "evidence_sufficiency": "partial",
                    "knowledge_gaps": [
                        f"missing caller chain for {fn0.symbol}",
                        "missing callee flow for dependency handoff",
                    ],
                    "summary": "need first-hop relationships",
                },
                {
                    "relevance": "medium",
                    "confidence": 0.5,
                    "sufficient": False,
                    "evidence_sufficiency": "partial",
                    "knowledge_gaps": [
                        f"missing caller chain for {fn1.symbol}",
                        "missing callee flow to dependency boundary",
                    ],
                    "summary": "need second-hop relationships",
                },
                {
                    "relevance": "medium",
                    "confidence": 0.5,
                    "sufficient": False,
                    "evidence_sufficiency": "partial",
                    "knowledge_gaps": [
                        f"missing caller chain for {cls0.symbol}",
                        "missing callee flow for final dependency",
                    ],
                    "summary": "need third-hop relationships",
                },
                {
                    "relevance": "medium",
                    "confidence": 0.55,
                    "sufficient": False,
                    "evidence_sufficiency": "partial",
                    "knowledge_gaps": [
                        f"missing caller chain for {mod0.symbol}",
                    ],
                    "summary": "need final caller hop",
                },
                {
                    "relevance": "high",
                    "confidence": 0.7,
                    "sufficient": True,
                    "evidence_sufficiency": "sufficient",
                    "knowledge_gaps": [],
                    "summary": "multi-hop chain resolved",
                },
            ],
            expected={"min_iterations": 4, "must_expand_only": True},
        ),
        LoopEvalCase(
            name="hard_multihop_alternating_callers_callees",
            instruction=(
                f"Perform deep multi-hop traversal for {fn0.symbol}: alternate caller/callee chain analysis "
                f"and locate the terminal dependency boundary."
            ),
            seed_symbols=[fn0, fn1, cls0, mod0],
            analyzer_script=[
                {
                    "relevance": "medium",
                    "confidence": 0.45,
                    "sufficient": False,
                    "evidence_sufficiency": "partial",
                    "knowledge_gaps": [f"missing caller chain for {fn0.symbol}"],
                    "summary": "hop 1 caller gap",
                },
                {
                    "relevance": "medium",
                    "confidence": 0.47,
                    "sufficient": False,
                    "evidence_sufficiency": "partial",
                    "knowledge_gaps": ["missing callee flow for handoff stage 2"],
                    "summary": "hop 2 callee gap",
                },
                {
                    "relevance": "medium",
                    "confidence": 0.49,
                    "sufficient": False,
                    "evidence_sufficiency": "partial",
                    "knowledge_gaps": [f"missing caller chain for {fn1.symbol}"],
                    "summary": "hop 3 caller gap",
                },
                {
                    "relevance": "medium",
                    "confidence": 0.50,
                    "sufficient": False,
                    "evidence_sufficiency": "partial",
                    "knowledge_gaps": ["missing callee flow for handoff stage 4"],
                    "summary": "hop 4 callee gap",
                },
                {
                    "relevance": "medium",
                    "confidence": 0.52,
                    "sufficient": False,
                    "evidence_sufficiency": "partial",
                    "knowledge_gaps": [f"missing caller chain for {cls0.symbol}"],
                    "summary": "hop 5 caller gap",
                },
                {
                    "relevance": "medium",
                    "confidence": 0.53,
                    "sufficient": False,
                    "evidence_sufficiency": "partial",
                    "knowledge_gaps": ["missing callee flow for handoff stage 6"],
                    "summary": "hop 6 callee gap",
                },
                {
                    "relevance": "medium",
                    "confidence": 0.55,
                    "sufficient": False,
                    "evidence_sufficiency": "partial",
                    "knowledge_gaps": [f"missing caller chain for {mod0.symbol}"],
                    "summary": "hop 7 caller gap",
                },
                {
                    "relevance": "high",
                    "confidence": 0.72,
                    "sufficient": True,
                    "evidence_sufficiency": "sufficient",
                    "knowledge_gaps": [],
                    "summary": "deep chain resolved",
                },
            ],
            expected={"min_iterations": 6, "must_expand_only": True, "alternating_relation_gaps": True},
        ),
        LoopEvalCase(
            name="forced_refine_after_hops",
            instruction=(
                f"After relationship exploration for {fn1.symbol}, reinterpret the objective and find exact definition path."
            ),
            seed_symbols=[fn1, fn0, cls0],
            analyzer_script=[
                {
                    "relevance": "medium",
                    "confidence": 0.45,
                    "sufficient": False,
                    "evidence_sufficiency": "partial",
                    "knowledge_gaps": [f"missing caller chain for {fn1.symbol}"],
                    "summary": "initial relationship gap",
                },
                {
                    "relevance": "medium",
                    "confidence": 0.48,
                    "sufficient": False,
                    "evidence_sufficiency": "partial",
                    "knowledge_gaps": [f"missing callee flow for {fn0.symbol}"],
                    "summary": "second relationship gap",
                },
                {
                    "relevance": "low",
                    "confidence": 0.40,
                    "sufficient": False,
                    "evidence_sufficiency": "partial",
                    "knowledge_gaps": [f"missing definition of {fn1.symbol}"],
                    "summary": "switch to definition refine",
                },
                {
                    "relevance": "high",
                    "confidence": 0.70,
                    "sufficient": True,
                    "evidence_sufficiency": "sufficient",
                    "knowledge_gaps": [],
                    "summary": "objective reinterpreted and resolved",
                },
            ],
            expected={"must_include_refine": True, "refine_after_expand_hops": True},
            force_refine_actions=1,
        ),
        LoopEvalCase(
            name="forced_refine_at_start_definition_gap",
            instruction=(
                f"Start by refining query intent for definition tracing of {cls0.symbol} before relationship expansion."
            ),
            seed_symbols=[cls0, fn0],
            analyzer_script=[
                {
                    "relevance": "low",
                    "confidence": 0.40,
                    "sufficient": False,
                    "evidence_sufficiency": "partial",
                    "knowledge_gaps": [f"missing definition of {cls0.symbol}"],
                    "summary": "definition-first refinement needed",
                },
                {
                    "relevance": "high",
                    "confidence": 0.70,
                    "sufficient": True,
                    "evidence_sufficiency": "sufficient",
                    "knowledge_gaps": [],
                    "summary": "definition resolved after refine",
                },
            ],
            expected={"must_include_refine": True, "refine_at_start": True},
            force_refine_actions=1,
        ),
    ]


def _build_engine(case: LoopEvalCase, signals: dict[str, list[SymbolEntry]]) -> tuple[ExplorationEngineV2, dict[str, Any]]:
    parser = _ScriptedIntentParser()
    selector = _PassSelector()
    analyzer = _ScriptedAnalyzer(case.analyzer_script)
    dispatcher = _ReadDispatcher()
    reader = InspectionReader(dispatcher)
    graph = GraphExpander(dispatcher=dispatcher)

    engine = ExplorationEngineV2(
        dispatcher=dispatcher,
        intent_parser=parser,
        selector=selector,
        inspection_reader=reader,
        analyzer=analyzer,
        graph_expander=graph,
    )

    telemetry: dict[str, Any] = {
        "initial_gaps": case.analyzer_script[0].get("knowledge_gaps", []),
        "actions": [],
        "step_decisions": [],
        "dedupe_skips": 0,
        "enqueued_order": [],
        "no_improvement_streak": [],
        "cooldown_forced_expands": 0,
    }

    original_next_action = engine._next_action
    original_apply_refine_cooldown = engine._apply_refine_cooldown
    original_should_expand = engine._should_expand
    original_should_refine = engine._should_refine
    original_enqueue_targets = engine._enqueue_targets
    original_update_utility = engine._update_utility_and_should_stop

    forced_refine_budget = {"remaining": int(case.force_refine_actions)}

    def wrap_next_action(self: ExplorationEngineV2, decision: Any) -> str:
        action = original_next_action(decision)
        if forced_refine_budget["remaining"] > 0:
            action = "refine"
            forced_refine_budget["remaining"] -= 1
        telemetry["step_decisions"].append(
            {"status": decision.status, "needs": list(decision.needs or []), "next_action": action}
        )
        return action

    def wrap_apply_refine_cooldown(
        self: ExplorationEngineV2,
        action: str,
        decision: Any,
        target: Any,
        ex_state: Any,
        *,
        exploration_outer: Any = None,
    ) -> str:
        out = original_apply_refine_cooldown(
            action,
            decision,
            target,
            ex_state,
            exploration_outer=exploration_outer,
        )
        if action == "refine" and out == "expand":
            telemetry["cooldown_forced_expands"] += 1
        return out

    def wrap_should_expand(self: ExplorationEngineV2, action: str, decision: Any, target: Any, ex_state: Any) -> bool:
        out = original_should_expand(action, decision, target, ex_state)
        if out:
            telemetry["actions"].append("expand")
        return out

    def wrap_should_refine(
        self: ExplorationEngineV2,
        action: str,
        decision: Any,
        ex_state: Any,
        *args: Any,
        **kwargs: Any,
    ) -> bool:
        out = original_should_refine(action, decision, ex_state, *args, **kwargs)
        if out:
            telemetry["actions"].append("refine")
        return out

    def wrap_enqueue_targets(
        self: ExplorationEngineV2,
        ex_state: Any,
        targets: list[Any],
        *args: Any,
        **kwargs: Any,
    ) -> None:
        before = len(ex_state.pending_targets)
        input_n = len(targets)
        original_enqueue_targets(ex_state, targets, *args, **kwargs)
        after = len(ex_state.pending_targets)
        accepted = max(0, after - before)
        telemetry["dedupe_skips"] += max(0, input_n - accepted)
        telemetry["enqueued_order"].extend(
            [
                {"file_path": t.file_path, "symbol": t.symbol, "source": t.source}
                for t in ex_state.pending_targets[before:after]
            ]
        )

    def wrap_update_utility(self: ExplorationEngineV2, understanding: Any, ex_state: Any, *, exploration_outer: Any = None):
        stop, reason = original_update_utility(understanding, ex_state, exploration_outer=exploration_outer)
        telemetry["no_improvement_streak"].append(ex_state.no_improvement_streak)
        return stop, reason

    engine._next_action = MethodType(wrap_next_action, engine)
    engine._apply_refine_cooldown = MethodType(wrap_apply_refine_cooldown, engine)
    engine._should_expand = MethodType(wrap_should_expand, engine)
    engine._should_refine = MethodType(wrap_should_refine, engine)
    engine._enqueue_targets = MethodType(wrap_enqueue_targets, engine)
    engine._update_utility_and_should_stop = MethodType(wrap_update_utility, engine)

    catalog = []
    catalog.extend(case.seed_symbols)
    catalog.extend(signals["functions"][:6])
    catalog.extend(signals["classes"][:4])
    telemetry["seed_catalog_size"] = len(catalog)

    state = SimpleNamespace(context={"project_root": str(PROJECT_ROOT)}, signal_catalog=catalog)
    telemetry["state"] = state
    return engine, telemetry


def _run_case(case: LoopEvalCase, signals: dict[str, list[SymbolEntry]]) -> dict[str, Any]:
    engine, t = _build_engine(case, signals)
    result = engine.explore(case.instruction, state=t["state"])
    t.pop("state", None)
    return {
        "case": case.name,
        "instruction": case.instruction,
        "initial_gaps": t["initial_gaps"],
        "expansion_actions_per_step": t["actions"],
        "refine_expand_decisions": t["step_decisions"],
        "iterations": len(t["step_decisions"]),
        "termination_reason": result.metadata.termination_reason,
        "no_improvement_streak": t["no_improvement_streak"][-1] if t["no_improvement_streak"] else 0,
        "dedupe_skips": t["dedupe_skips"],
        "enqueued_order": t["enqueued_order"][:8],
        "completion_status": result.metadata.completion_status,
        "cooldown_forced_expands": t["cooldown_forced_expands"],
        "expected": case.expected,
    }


def main() -> int:
    signals = _collect_signals()
    cases = _build_cases(signals)

    print("# Expansion/Refinement Loop Live Eval (Post-Analyzer)")
    print(f"# project_root={PROJECT_ROOT}")
    print(
        f"# signals functions={len(signals['functions'])} classes={len(signals['classes'])} modules={len(signals['modules'])}"
    )

    all_outputs: list[dict[str, Any]] = []
    for case in cases:
        run1 = _run_case(case, signals)
        run2 = _run_case(case, signals)
        same_path = run1["expansion_actions_per_step"] == run2["expansion_actions_per_step"]
        out = {
            **run1,
            "stability_same_expansion_path": same_path,
            "second_run_expansion_actions_per_step": run2["expansion_actions_per_step"],
        }
        all_outputs.append(out)
        print("=" * 120)
        print(json.dumps(out, indent=2, ensure_ascii=False))

    print("=" * 120)
    print(
        json.dumps(
            {
                "summary": {
                    "cases": len(all_outputs),
                    "no_improvement_stops": sum(1 for x in all_outputs if x["termination_reason"] == "no_improvement_streak"),
                    "cases_with_dedupe_skips": sum(1 for x in all_outputs if x["dedupe_skips"] > 0),
                    "stable_paths": sum(1 for x in all_outputs if x["stability_same_expansion_path"]),
                }
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
