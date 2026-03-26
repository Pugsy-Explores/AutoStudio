#!/usr/bin/env python3
"""
True live behavioral eval for ExplorationEngineV2 post-analyzer loop.

Live guarantees:
- Real LLM calls (QueryIntentParser + UnderstandingAnalyzer).
- Real retrieval/read calls (Dispatcher SEARCH + read_snippet + GraphExpander path).

Scope:
- Measures control behavior only (expand/refine/stop, cooldown, dedupe, utility stop).
- Does not score prompt reasoning text quality.
"""

from __future__ import annotations

import argparse
import ast
import json
from dataclasses import dataclass
from pathlib import Path
from types import MethodType, SimpleNamespace
from typing import Any

from agent.execution.step_dispatcher import _dispatch_react
from agent.models.model_client import call_reasoning_model, call_reasoning_model_messages
from agent.models.model_config import get_prompt_model_name_for_task
from agent_v2.exploration.exploration_engine_v2 import ExplorationEngineV2
from agent_v2.exploration.exploration_task_names import (
    EXPLORATION_TASK_ANALYZER,
    EXPLORATION_TASK_QUERY_INTENT,
)
from agent_v2.exploration.graph_expander import GraphExpander
from agent_v2.exploration.inspection_reader import InspectionReader
from agent_v2.exploration.query_intent_parser import QueryIntentParser
from agent_v2.exploration.understanding_analyzer import UnderstandingAnalyzer
from agent_v2.runtime.dispatcher import Dispatcher
from agent_v2.schemas.exploration import ExplorationCandidate

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
class Case:
    bucket: str
    instruction: str
    expected: str


class _PassSelector:
    """Keep selection deterministic to isolate loop behavior."""

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


def _collect_repo_symbols() -> dict[str, list[SymbolEntry]]:
    funcs: list[SymbolEntry] = []
    classes: list[SymbolEntry] = []
    modules: set[str] = set()
    for fp in _iter_python_files():
        rel = fp.relative_to(PROJECT_ROOT).as_posix()
        for token in rel.replace(".py", "").split("/"):
            if token and token != "__init__":
                modules.add(token)
        try:
            tree = ast.parse(fp.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            continue
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
                funcs.append(
                    SymbolEntry(
                        file_path=str(PROJECT_ROOT / rel),
                        symbol=node.name,
                        line_start=max(1, int(node.lineno)),
                        line_end=max(int(node.lineno), int(getattr(node, "end_lineno", node.lineno))),
                        kind="function",
                    )
                )
            elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
                classes.append(
                    SymbolEntry(
                        file_path=str(PROJECT_ROOT / rel),
                        symbol=node.name,
                        line_start=max(1, int(node.lineno)),
                        line_end=max(int(node.lineno), int(getattr(node, "end_lineno", node.lineno))),
                        kind="class",
                    )
                )
    return {
        "functions": sorted(funcs, key=lambda x: (x.file_path, x.symbol)),
        "classes": sorted(classes, key=lambda x: (x.file_path, x.symbol)),
        "modules": [
            SymbolEntry(file_path=m, symbol=m, line_start=1, line_end=1, kind="module")
            for m in sorted(modules)
        ],
    }


def _pick(xs: list[SymbolEntry], i: int, fallback: SymbolEntry) -> SymbolEntry:
    if not xs:
        return fallback
    return xs[i % len(xs)]


def _build_cases(symbols: dict[str, list[SymbolEntry]]) -> list[Case]:
    fn0 = _pick(symbols["functions"], 0, SymbolEntry(str(PROJECT_ROOT / "agent_v2/config.py"), "get_config", 1, 40, "function"))
    fn1 = _pick(symbols["functions"], 7, fn0)
    cls0 = _pick(symbols["classes"], 0, SymbolEntry(str(PROJECT_ROOT / "agent_v2/runtime/mode_manager.py"), "ModeManager", 1, 80, "class"))
    mod0 = _pick(symbols["modules"], 0, SymbolEntry("agent_v2", "agent_v2", 1, 1, "module"))
    return [
        Case(
            bucket="gap_driven_expansion",
            instruction=f"Trace usage of {fn0.symbol}; include missing caller/callee chain if needed.",
            expected="expand should follow gaps",
        ),
        Case(
            bucket="gap_filtering",
            instruction=f"Understand flow of {cls0.symbol}; ignore generic missing context and focus on actionable gaps.",
            expected="generic/repeated gaps should not drive useless expansion",
        ),
        Case(
            bucket="refine_cooldown",
            instruction=f"Find where {mod0.symbol} is used and recover if wrong target is explored repeatedly.",
            expected="avoid refine churn; force expansion after consecutive refine pressure",
        ),
        Case(
            bucket="utility_stop",
            instruction=f"Trace usage of {fn1.symbol} and stop if no new understanding is gained.",
            expected="terminate by no_improvement_streak before max_steps",
        ),
        Case(
            bucket="duplicate_prevention",
            instruction=f"Find where {fn0.symbol} is used across related files and avoid duplicate revisits.",
            expected="pre-enqueue dedupe should reduce duplicate queue work",
        ),
        Case(
            bucket="prioritization_behavior",
            instruction=f"Understand flow of {fn0.symbol}; prioritize high-signal related targets over low-value noise.",
            expected="higher-value targets should appear earlier in enqueue order",
        ),
    ]


def _instrument(engine: ExplorationEngineV2, seed_candidates: list[ExplorationCandidate]) -> tuple[ExplorationEngineV2, dict[str, Any]]:
    telemetry: dict[str, Any] = {
        "initial_gaps": [],
        "decisions": [],
        "actions": [],
        "iterations": 0,
        "dedupe_skips": 0,
        "enqueued_order": [],
        "cooldown_forced_expands": 0,
        "no_improvement_streak": 0,
        "retrieval_calls": {"search_batch": 0, "inspect_packet": 0, "graph_expand": 0},
        "discovery_fallback_injected": 0,
    }

    orig_apply_gap = engine._apply_gap_driven_decision
    orig_next = engine._next_action
    orig_should_expand = engine._should_expand
    orig_should_refine = engine._should_refine
    orig_enqueue_targets = engine._enqueue_targets
    orig_apply_cooldown = engine._apply_refine_cooldown
    orig_update_utility = engine._update_utility_and_should_stop
    orig_search_batch = engine._dispatcher.search_batch
    orig_inspect_packet = engine._inspection_reader.inspect_packet
    orig_graph_expand = engine._graph_expander.expand
    orig_run_discovery = engine._run_discovery_traced

    def w_apply_gap(self: ExplorationEngineV2, decision: Any, understanding: Any, ex_state: Any, *, exploration_outer: Any = None):
        if not telemetry["initial_gaps"]:
            telemetry["initial_gaps"] = list(getattr(understanding, "knowledge_gaps", []) or [])
        return orig_apply_gap(decision, understanding, ex_state, exploration_outer=exploration_outer)

    def w_next(self: ExplorationEngineV2, decision: Any) -> str:
        action = orig_next(decision)
        telemetry["decisions"].append(
            {
                "status": decision.status,
                "needs": list(decision.needs or []),
                "next_action": action,
            }
        )
        telemetry["iterations"] += 1
        return action

    def w_should_expand(self: ExplorationEngineV2, action: str, decision: Any, target: Any, ex_state: Any) -> bool:
        out = orig_should_expand(action, decision, target, ex_state)
        if out:
            telemetry["actions"].append("expand")
        return out

    def w_should_refine(self: ExplorationEngineV2, action: str, decision: Any, ex_state: Any) -> bool:
        out = orig_should_refine(action, decision, ex_state)
        if out:
            telemetry["actions"].append("refine")
        return out

    def w_enqueue_targets(self: ExplorationEngineV2, ex_state: Any, targets: list[Any]) -> None:
        before = len(ex_state.pending_targets)
        input_n = len(targets)
        orig_enqueue_targets(ex_state, targets)
        after = len(ex_state.pending_targets)
        accepted = max(0, after - before)
        telemetry["dedupe_skips"] += max(0, input_n - accepted)
        telemetry["enqueued_order"].extend(
            [
                {"file_path": t.file_path, "symbol": t.symbol, "source": t.source}
                for t in ex_state.pending_targets[before:after]
            ]
        )

    def w_apply_cooldown(self: ExplorationEngineV2, action: str, decision: Any, target: Any, ex_state: Any, *, exploration_outer: Any = None):
        out = orig_apply_cooldown(action, decision, target, ex_state, exploration_outer=exploration_outer)
        if action == "refine" and out == "expand":
            telemetry["cooldown_forced_expands"] += 1
        return out

    def w_update_utility(self: ExplorationEngineV2, understanding: Any, ex_state: Any, *, exploration_outer: Any = None):
        stop, reason = orig_update_utility(understanding, ex_state, exploration_outer=exploration_outer)
        telemetry["no_improvement_streak"] = int(getattr(ex_state, "no_improvement_streak", 0))
        return stop, reason

    def w_search_batch(queries: list[str], state: Any, *, mode: str, step_id_prefix: str, max_workers: int = 4):
        telemetry["retrieval_calls"]["search_batch"] += 1
        return orig_search_batch(queries, state, mode=mode, step_id_prefix=step_id_prefix, max_workers=max_workers)

    def w_inspect_packet(*args: Any, **kwargs: Any):
        telemetry["retrieval_calls"]["inspect_packet"] += 1
        return orig_inspect_packet(*args, **kwargs)

    def w_graph_expand(*args: Any, **kwargs: Any):
        telemetry["retrieval_calls"]["graph_expand"] += 1
        return orig_graph_expand(*args, **kwargs)

    def w_run_discovery(self: ExplorationEngineV2, exploration_outer: Any, phase: str, intent: Any, state: Any, ex_state: Any):
        candidates, records, ms = orig_run_discovery(exploration_outer, phase, intent, state, ex_state)
        if candidates:
            return candidates, records, ms
        injected = [c for c in seed_candidates if self._may_enqueue(ex_state, c.file_path, c.symbol)]
        if injected:
            telemetry["discovery_fallback_injected"] += len(injected)
            return injected[:8], records, ms
        return candidates, records, ms

    engine._apply_gap_driven_decision = MethodType(w_apply_gap, engine)
    engine._next_action = MethodType(w_next, engine)
    engine._should_expand = MethodType(w_should_expand, engine)
    engine._should_refine = MethodType(w_should_refine, engine)
    engine._enqueue_targets = MethodType(w_enqueue_targets, engine)
    engine._apply_refine_cooldown = MethodType(w_apply_cooldown, engine)
    engine._update_utility_and_should_stop = MethodType(w_update_utility, engine)
    engine._dispatcher.search_batch = w_search_batch
    engine._inspection_reader.inspect_packet = w_inspect_packet
    engine._graph_expander.expand = w_graph_expand
    engine._run_discovery_traced = MethodType(w_run_discovery, engine)
    return engine, telemetry


def _build_engine() -> ExplorationEngineV2:
    dispatcher = Dispatcher(execute_fn=_dispatch_react)
    parser = QueryIntentParser(
        llm_generate=lambda p: call_reasoning_model(p, task_name=EXPLORATION_TASK_QUERY_INTENT),
        llm_generate_messages=lambda msgs: call_reasoning_model_messages(
            msgs, task_name=EXPLORATION_TASK_QUERY_INTENT
        ),
        model_name=get_prompt_model_name_for_task(EXPLORATION_TASK_QUERY_INTENT),
    )
    analyzer = UnderstandingAnalyzer(
        llm_generate=lambda p: call_reasoning_model(p, task_name=EXPLORATION_TASK_ANALYZER),
        llm_generate_messages=lambda msgs: call_reasoning_model_messages(
            msgs, task_name=EXPLORATION_TASK_ANALYZER
        ),
        model_name=get_prompt_model_name_for_task(EXPLORATION_TASK_ANALYZER),
    )
    return ExplorationEngineV2(
        dispatcher=dispatcher,
        intent_parser=parser,
        selector=_PassSelector(),
        inspection_reader=InspectionReader(dispatcher=dispatcher),
        analyzer=analyzer,
        graph_expander=GraphExpander(dispatcher=dispatcher),
        scoper=None,
    )


def _run_case(case: Case) -> dict[str, Any]:
    engine = _build_engine()
    symbols = _collect_repo_symbols()
    fallback_pool = (symbols["functions"][:6] + symbols["classes"][:4])[:10]
    seed_candidates = [
        ExplorationCandidate(file_path=s.file_path, symbol=s.symbol, snippet=f"{s.kind} {s.symbol}", source="grep")
        for s in fallback_pool
        if s.file_path.startswith(str(PROJECT_ROOT))
    ]
    engine, t = _instrument(engine, seed_candidates)
    state = SimpleNamespace(context={"project_root": str(PROJECT_ROOT)})
    result = engine.explore(case.instruction, state=state)
    return {
        "bucket": case.bucket,
        "instruction": case.instruction,
        "expected": case.expected,
        "initial_gaps": t["initial_gaps"],
        "expansion_actions_per_step": t["actions"],
        "refine_expand_decisions": t["decisions"],
        "number_of_iterations": t["iterations"],
        "termination_reason": result.metadata.termination_reason,
        "no_improvement_streak": t["no_improvement_streak"],
        "dedupe_skips": t["dedupe_skips"],
        "enqueued_order": t["enqueued_order"][:10],
        "live_calls": t["retrieval_calls"],
        "discovery_fallback_injected": t["discovery_fallback_injected"],
        "completion_status": result.metadata.completion_status,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-cases", type=int, default=6)
    ap.add_argument("--repeats", type=int, default=1)
    args = ap.parse_args()

    symbols = _collect_repo_symbols()
    cases = _build_cases(symbols)[: max(1, int(args.max_cases))]

    print("# Live Expansion/Refinement Loop Eval")
    print("# scope=post-analyzer-control-flow-only")
    print(f"# project_root={PROJECT_ROOT}")
    print(
        f"# signals functions={len(symbols['functions'])} classes={len(symbols['classes'])} modules={len(symbols['modules'])}"
    )
    print(f"# repeats={max(1, int(args.repeats))}")

    outputs: list[dict[str, Any]] = []
    for case in cases:
        runs = [_run_case(case) for _ in range(max(1, int(args.repeats)))]
        first = runs[0]
        first["stability_same_expansion_path"] = all(
            r["expansion_actions_per_step"] == first["expansion_actions_per_step"] for r in runs[1:]
        )
        first["repeat_paths"] = [r["expansion_actions_per_step"] for r in runs]
        outputs.append(first)
        print("=" * 120)
        print(json.dumps(first, indent=2, ensure_ascii=False))

    print("=" * 120)
    print(
        json.dumps(
            {
                "summary": {
                    "cases": len(outputs),
                    "stable_paths": sum(1 for x in outputs if x["stability_same_expansion_path"]),
                    "utility_stops": sum(1 for x in outputs if x["termination_reason"] == "no_improvement_streak"),
                    "cases_with_dedupe_skips": sum(1 for x in outputs if x["dedupe_skips"] > 0),
                    "total_live_search_calls": sum(int(x["live_calls"]["search_batch"]) for x in outputs),
                    "total_live_read_calls": sum(int(x["live_calls"]["inspect_packet"]) for x in outputs),
                }
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
