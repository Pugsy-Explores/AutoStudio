#!/usr/bin/env python3
"""
Live modular evaluation for adaptive exploration stack only:
Reader -> Router -> SliceGrouper -> Inspector -> Fetcher -> ContextBlockBuilder -> Analyzer -> DecisionMapper

Notes:
- Uses live model call for analyzer and query-intent parser.
- Generates cases dynamically from repository signals (functions/classes/modules/call relations).
- Does not invoke selector/planner/downstream runtime systems.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from agent.models.model_client import call_reasoning_model_messages
from agent.models.model_config import get_prompt_model_name_for_task
from agent_v2.config import (
    EXPLORATION_CONTEXT_MAX_TOTAL_LINES,
    EXPLORATION_CONTEXT_TOP_K_RANGES,
    EXPLORATION_ROUTING_COMPLEX_MAX_LINES,
    EXPLORATION_ROUTING_SIMPLE_MAX_LINES,
)
from agent_v2.exploration.context_block_builder import ContextBlockBuilder
from agent_v2.exploration.decision_mapper import EngineDecisionMapper
from agent_v2.exploration.exploration_task_names import (
    EXPLORATION_TASK_ANALYZER,
    EXPLORATION_TASK_QUERY_INTENT,
)
from agent_v2.exploration.fetcher import Fetcher
from agent_v2.exploration.inspection_reader import InspectionReader
from agent_v2.exploration.inspector import Inspector
from agent_v2.exploration.query_intent_parser import QueryIntentParser
from agent_v2.exploration.read_router import ReadRequest, read
from agent_v2.exploration.slice_grouper import SliceGrouper
from agent_v2.exploration.understanding_analyzer import UnderstandingAnalyzer
from agent_v2.schemas.execution import ExecutionMetadata, ExecutionOutput, ExecutionResult
from agent_v2.schemas.exploration import ExplorationCandidate, ReadPacket
from agent_v2.schemas.exploration import UnderstandingResult


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCAN_ROOTS = [PROJECT_ROOT / "agent", PROJECT_ROOT / "agent_v2"]


@dataclass(frozen=True)
class SymbolEntry:
    file_path: str
    symbol: str
    line_start: int
    line_end: int
    kind: str  # function|class


@dataclass(frozen=True)
class EvalCase:
    bucket: str
    instruction: str
    read_requests: list[ReadRequest]
    expect_route: str | None = None
    simulate_inspector_failure: bool = False


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


def _collect_repo_signals() -> dict[str, Any]:
    functions: list[SymbolEntry] = []
    classes: list[SymbolEntry] = []
    modules: set[str] = set()
    calls: list[tuple[str, str, str]] = []  # (file_path, caller, callee)

    for file_path in _iter_python_files():
        rel = file_path.relative_to(PROJECT_ROOT).as_posix()
        module_tokens = rel.replace(".py", "").split("/")
        for t in module_tokens:
            if t and t != "__init__":
                modules.add(t)
        try:
            source = file_path.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(source)
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
                for call in ast.walk(node):
                    if isinstance(call, ast.Call):
                        fn = call.func
                        if isinstance(fn, ast.Name):
                            calls.append((rel, node.name, fn.id))
                        elif isinstance(fn, ast.Attribute):
                            calls.append((rel, node.name, fn.attr))
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
    functions.sort(key=lambda s: (s.file_path, s.line_end - s.line_start, s.symbol))
    classes.sort(key=lambda s: (s.file_path, s.line_end - s.line_start, s.symbol))
    calls.sort()
    return {
        "functions": functions,
        "classes": classes,
        "modules": sorted(modules),
        "calls": calls,
    }


def _pick_symbol(entries: list[SymbolEntry], idx: int) -> SymbolEntry:
    if not entries:
        return SymbolEntry("agent_v2/config.py", "get_config", 1, 40, "function")
    return entries[idx % len(entries)]


def _build_cases(signals: dict[str, Any]) -> list[EvalCase]:
    fn_small = _pick_symbol(signals["functions"], 0)
    fn_medium = _pick_symbol(signals["functions"], 8)
    cls_one = _pick_symbol(signals["classes"], 0)
    cls_two = _pick_symbol(signals["classes"], 4)
    modules: list[str] = signals["modules"] or ["exploration"]
    calls: list[tuple[str, str, str]] = signals["calls"]
    call_edge = calls[0] if calls else (fn_medium.file_path, fn_medium.symbol, fn_small.symbol)

    easy = EvalCase(
        bucket="easy",
        instruction=f"Find where {fn_small.symbol} is defined",
        read_requests=[ReadRequest(path=fn_small.file_path, symbol=fn_small.symbol)],
        expect_route="simple_path",
    )
    medium = EvalCase(
        bucket="medium",
        instruction=f"Trace usage of {cls_one.symbol} and {cls_two.symbol}",
        read_requests=[
            ReadRequest(path=cls_one.file_path, symbol=cls_one.symbol),
            ReadRequest(path=cls_two.file_path, symbol=cls_two.symbol),
        ],
    )
    hard = EvalCase(
        bucket="hard",
        instruction=f"Understand how {call_edge[1]} interacts with {call_edge[2]} in {Path(call_edge[0]).stem}",
        read_requests=[
            ReadRequest(path=call_edge[0], symbol=call_edge[1]),
            ReadRequest(path=call_edge[0], line=max(1, fn_medium.line_start)),
        ],
        expect_route="complex_path",
    )
    adversarial = EvalCase(
        bucket="adversarial",
        instruction=f"Something is wrong with {modules[0]}",
        read_requests=[ReadRequest(path=fn_medium.file_path, line=fn_medium.line_start)],
    )
    routing_small = EvalCase(
        bucket="routing_validation_small",
        instruction=f"Find where {fn_small.symbol} is defined in {Path(fn_small.file_path).stem}",
        read_requests=[ReadRequest(path=fn_small.file_path, symbol=fn_small.symbol)],
        expect_route="simple_path",
    )
    routing_complex = EvalCase(
        bucket="routing_validation_complex",
        instruction=f"Understand how {modules[min(1, len(modules)-1)]} works across modules",
        read_requests=[
            ReadRequest(path=fn_small.file_path, symbol=fn_small.symbol),
            ReadRequest(path=fn_medium.file_path, symbol=fn_medium.symbol),
            ReadRequest(path=cls_one.file_path, symbol=cls_one.symbol),
        ],
        expect_route="complex_path",
    )
    failure_case = EvalCase(
        bucket="failure_handling",
        instruction=f"Trace usage of {fn_medium.symbol}",
        read_requests=[ReadRequest(path=fn_medium.file_path, symbol=fn_medium.symbol)],
        simulate_inspector_failure=True,
    )
    return [easy, medium, hard, adversarial, routing_small, routing_complex, failure_case]


def _build_execution_result(payload: dict, step_id: str = "inspect_eval") -> ExecutionResult:
    mode = str(payload.get("mode") or "")
    summary = f"read_snippet {mode}"
    return ExecutionResult(
        step_id=step_id,
        success=True,
        status="success",
        output=ExecutionOutput(data=payload, summary=summary),
        error=None,
        metadata=ExecutionMetadata(tool_name="read_snippet", duration_ms=1, timestamp="eval"),
    )


class _EvalDispatcher:
    def execute(self, step: dict, state: Any) -> ExecutionResult:
        args = step.get("_react_args") or {}
        payload = read(
            ReadRequest(
                path=str(args.get("path") or ""),
                symbol=args.get("symbol"),
                line=args.get("line"),
                window=int(args.get("window") or 80),
            ),
            state=state,
        )
        return _build_execution_result(payload, step_id=str(step.get("id") or "inspect_eval"))


def _complexity_signal(parsed_intent: dict) -> str:
    intents = [x for x in (parsed_intent.get("intents") or []) if str(x).strip()]
    symbols = [x for x in (parsed_intent.get("symbols") or []) if str(x).strip()]
    if len(intents) >= 3 or len(symbols) >= 4:
        return "high"
    if len(intents) >= 2 or len(symbols) >= 2:
        return "medium"
    return "low"


def _route(
    packets: list[ReadPacket],
    intent_payload: dict,
) -> tuple[str, str, str | None, str]:
    packet_count = len(packets)
    max_line_count = max((p.line_count for p in packets), default=0)
    symbol_set = {(p.symbol or "").strip() for p in packets if (p.symbol or "").strip()}
    symbol_count = len(symbol_set)
    c_sig = _complexity_signal(intent_payload)

    score = 0.0
    if packet_count > 1:
        score += 2.0
    if max_line_count > EXPLORATION_ROUTING_COMPLEX_MAX_LINES:
        score += 2.0
    elif max_line_count > EXPLORATION_ROUTING_SIMPLE_MAX_LINES:
        score += 1.0
    if symbol_count >= 3:
        score += 2.0
    elif symbol_count == 2:
        score += 1.0
    if c_sig == "high":
        score += 1.0
    elif c_sig == "medium":
        score += 0.5

    if score >= 3.0:
        bucket = "high"
    elif score >= 1.5:
        bucket = "medium"
    else:
        bucket = "low"

    use_inspector = bucket == "high" or (
        bucket == "medium"
        and (packet_count > 1 or max_line_count > EXPLORATION_ROUTING_COMPLEX_MAX_LINES or symbol_count >= 3)
    )
    if use_inspector:
        return "complex_path", "multi_slice_or_complex", None, bucket
    if max_line_count <= EXPLORATION_ROUTING_SIMPLE_MAX_LINES:
        return "simple_path", "small_input", "small_input", bucket
    if bucket == "low":
        return "simple_path", "low_complexity", "low_complexity", bucket
    return "simple_path", "low_noise", "low_noise", bucket


def _context_quality(blocks: list[dict]) -> dict:
    keys = [(b["file_path"], b["start"], b["end"]) for b in blocks]
    total_lines = sum(max(1, int(b["end"]) - int(b["start"]) + 1) for b in blocks) if blocks else 0
    return {
        "non_duplicated": len(keys) == len(set(keys)),
        "within_budget": total_lines <= EXPLORATION_CONTEXT_MAX_TOTAL_LINES,
        "total_lines": total_lines,
        "block_count": len(blocks),
    }


def _run_case(
    case: EvalCase,
    parser: QueryIntentParser,
    reader: InspectionReader,
    grouper: SliceGrouper,
    inspector: Inspector,
    fetcher: Fetcher,
    block_builder: ContextBlockBuilder,
    analyzer: UnderstandingAnalyzer,
    mapper: EngineDecisionMapper,
) -> dict[str, Any]:
    state = SimpleNamespace(context={"project_root": str(PROJECT_ROOT)}, steps_taken=0)
    packets: list[ReadPacket] = []
    for i, rr in enumerate(case.read_requests, start=1):
        candidate = ExplorationCandidate(symbol=rr.symbol, file_path=rr.path, snippet=None, source="grep")
        pkt, _res = reader.inspect_packet(
            candidate,
            symbol=rr.symbol,
            line=rr.line,
            window=rr.window,
            state=state,
        )
        packets.append(pkt)

    intent = parser.parse(case.instruction).model_dump()
    routing_path, routing_reason, skipped_reason, complexity_bucket = _route(packets, intent)
    used_inspector = routing_path == "complex_path"

    groups = grouper.group(packets)
    fallback = False
    if used_inspector:
        try:
            if case.simulate_inspector_failure:
                raise RuntimeError("simulated inspector failure")
            signals = inspector.inspect(groups[0] if groups else packets, max_ranges=EXPLORATION_CONTEXT_TOP_K_RANGES)
            fetched = fetcher.fetch(
                groups[0] if groups else packets,
                signals,
                top_k_ranges=EXPLORATION_CONTEXT_TOP_K_RANGES,
                max_total_lines=EXPLORATION_CONTEXT_MAX_TOTAL_LINES,
            )
            context_blocks = block_builder.finalize(
                fetched, max_total_lines=EXPLORATION_CONTEXT_MAX_TOTAL_LINES
            )
        except Exception:
            fallback = True
            used_inspector = False
            routing_path = "simple_path"
            routing_reason = "inspector_failure_fallback"
            skipped_reason = "low_noise"
            context_blocks = block_builder.from_packets(
                packets, max_total_lines=EXPLORATION_CONTEXT_MAX_TOTAL_LINES
            )
    else:
        context_blocks = block_builder.from_packets(
            packets, max_total_lines=EXPLORATION_CONTEXT_MAX_TOTAL_LINES
        )

    intent_text = ", ".join([x for x in intent.get("intents", []) if str(x).strip()]) or "no intent"
    understanding_1 = analyzer.analyze(
        case.instruction,
        intent=intent_text,
        context_blocks=context_blocks,
    ).model_dump()
    understanding_2 = analyzer.analyze(
        case.instruction,
        intent=intent_text,
        context_blocks=context_blocks,
    ).model_dump()
    decision = mapper.to_exploration_decision(
        UnderstandingResult.model_validate(understanding_1)
    ).model_dump()

    return {
        "bucket": case.bucket,
        "instruction": case.instruction,
        "complexity_bucket": complexity_bucket,
        "routing_path": routing_path,
        "routing_reason": routing_reason,
        "inspector_skipped_reason": skipped_reason,
        "expected_route": case.expect_route,
        "read_packets": len(packets),
        "context_blocks": len(context_blocks),
        "inspector_used": used_inspector,
        "analyzer_output": understanding_1,
        "analyzer_stability_same_output": understanding_1 == understanding_2,
        "final_exploration_decision": decision,
        "context_quality": _context_quality([b.model_dump() for b in context_blocks]),
        "inspector_fallback": fallback,
        "analyzer_has_control_leakage": any(k in understanding_1 for k in ("next_action", "needs", "status")),
    }


def main() -> int:
    signals = _collect_repo_signals()
    cases = _build_cases(signals)

    model_qip = get_prompt_model_name_for_task(EXPLORATION_TASK_QUERY_INTENT)
    model_an = get_prompt_model_name_for_task(EXPLORATION_TASK_ANALYZER)
    parser = QueryIntentParser(
        llm_generate_messages=lambda messages: call_reasoning_model_messages(
            messages, task_name=EXPLORATION_TASK_QUERY_INTENT
        ),
        model_name=model_qip,
    )
    analyzer = UnderstandingAnalyzer(
        llm_generate_messages=lambda messages: call_reasoning_model_messages(
            messages, task_name=EXPLORATION_TASK_ANALYZER
        ),
        model_name=model_an,
    )

    reader = InspectionReader(_EvalDispatcher())
    grouper = SliceGrouper()
    inspector = Inspector()
    fetcher = Fetcher()
    block_builder = ContextBlockBuilder()
    mapper = EngineDecisionMapper()

    print("# Adaptive Exploration Live Eval")
    print(f"# project_root={PROJECT_ROOT}")
    print(
        f"# signals functions={len(signals['functions'])} classes={len(signals['classes'])} modules={len(signals['modules'])} calls={len(signals['calls'])}"
    )

    for case in cases:
        result = _run_case(
            case,
            parser,
            reader,
            grouper,
            inspector,
            fetcher,
            block_builder,
            analyzer,
            mapper,
        )
        print("=" * 120)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
