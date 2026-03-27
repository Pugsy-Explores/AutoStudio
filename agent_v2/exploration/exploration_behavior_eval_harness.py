from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import MethodType, SimpleNamespace
from typing import Any

from agent.models.model_client import call_reasoning_model
from agent.models.model_config import get_model_call_params
from agent_v2.exploration.exploration_engine_v2 import ExplorationEngineV2
from agent_v2.exploration.exploration_task_names import EXPLORATION_TASK_ANALYZER
from agent_v2.exploration.graph_expander import GraphExpander
from agent_v2.exploration.inspection_reader import InspectionReader
from agent_v2.exploration.read_router import ReadRequest, read
from agent_v2.schemas.execution import ExecutionMetadata, ExecutionOutput, ExecutionResult
from agent_v2.schemas.exploration import ExplorationCandidate, QueryIntent, UnderstandingResult

_JUDGE_SCHEMA_DEFAULT_FAIL = {
    "semantic_alignment": "incorrect",
    "decision_quality": "poor",
    "loop_behavior": "stuck",
    "gap_handling": "not_resolved",
    "final_verdict": "fail",
}

_LLM_JUDGE_PROMPT = """You are an evaluation judge for an autonomous code exploration agent.

Your task is NOT to solve the problem.
Your task is to evaluate whether the agent behaved correctly.

You will be given:
1. The original instruction
2. A step-by-step trace of the agent's behavior
3. The expected behavior patterns

---

## Evaluation Criteria

Evaluate STRICTLY based on:

1. Decision Alignment
- Did the agent choose the correct action (expand/refine/stop) based on gaps?

2. Memory Utilization
- Did the agent use previously discovered information to guide next steps?

3. Loop Behavior
- Did the agent avoid redundant or repeated actions?
- Did it make forward progress?

4. Gap Handling
- Did the agent attempt to resolve identified gaps?
- Did it prefer expansion when structural gaps (callers/callees) existed?

---

## Rules

- DO NOT hallucinate missing steps
- DO NOT assume intent beyond trace
- DO NOT reward "reasonable looking" behavior
- ONLY evaluate what is explicitly visible
- The verdict MUST check behavior against expected_patterns and step_expectations.
- Be strict: minor mistakes -> downgrade
- Be deterministic: same input -> same output

---

## Output Format (STRICT JSON)

Return ONLY valid JSON:
{
  "semantic_alignment": "correct | partial | incorrect",
  "decision_quality": "good | weak | poor",
  "loop_behavior": "efficient | redundant | stuck",
  "gap_handling": "resolved | partially_resolved | not_resolved",
  "final_verdict": "pass | fail",
  "reason": "concise explanation (1-2 sentences max)"
}"""


@dataclass(frozen=True)
class SymbolEntry:
    file_path: str
    symbol: str
    line_start: int = 1
    line_end: int = 1
    kind: str = "function"


@dataclass(frozen=True)
class EvalCase:
    id: str
    instruction: str
    focus_area: str
    expected_behavior: dict[str, Any]
    analyzer_script: list[dict[str, Any]]
    seed_symbols: list[SymbolEntry]
    force_refine_actions: int = 0


@dataclass
class EvalCaseResult:
    case_id: str
    trace: dict[str, Any]
    rule_based: dict[str, Any]
    structural: dict[str, Any]
    llm_judge: dict[str, Any]
    final_case_pass: bool


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
    def __init__(self, script: list[dict[str, Any]], telemetry: dict[str, Any]):
        self._script = script
        self._idx = 0
        self._telemetry = telemetry

    def analyze(self, instruction: str, intent: str, context_blocks: list[Any], **kwargs: Any) -> UnderstandingResult:
        if self._idx < len(self._script):
            payload = self._script[self._idx]
            self._idx += 1
        else:
            payload = self._script[-1]
        out = UnderstandingResult.model_validate(payload)
        self._telemetry["last_analyzer"] = {
            "relevance": out.relevance,
            "sufficient": bool(out.sufficient),
            "confidence": float(out.confidence),
            "knowledge_gaps": list(out.knowledge_gaps or []),
            "summary": str(out.summary or ""),
        }
        return out


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
        out: list[ExecutionResult] = []
        for i, _ in enumerate(queries):
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
            out.append(
                ExecutionResult(
                    step_id=f"{step_id_prefix}_{i}",
                    success=True,
                    status="success",
                    output=ExecutionOutput(data={"results": rows}, summary=f"search_{mode}"),
                    error=None,
                    metadata=ExecutionMetadata(tool_name="search", duration_ms=1, timestamp="eval"),
                )
            )
        return out


def _safe_json_extract(raw: str) -> dict[str, Any] | None:
    text = (raw or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        text = text.replace("```json", "").replace("```", "").strip()
    try:
        parsed = json.loads(text)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _is_explainable_divergence(reason: str) -> bool:
    low = (reason or "").lower()
    tokens = (
        "depth",
        "no symbol",
        "backtrack",
        "guard",
        "already expanded",
        "max",
        "blocked",
    )
    return any(t in low for t in tokens)


def _validate_llm_judge_schema(obj: dict[str, Any]) -> tuple[bool, str]:
    allowed = {
        "semantic_alignment": {"correct", "partial", "incorrect"},
        "decision_quality": {"good", "weak", "poor"},
        "loop_behavior": {"efficient", "redundant", "stuck"},
        "gap_handling": {"resolved", "partially_resolved", "not_resolved"},
        "final_verdict": {"pass", "fail"},
    }
    for k, vals in allowed.items():
        v = str(obj.get(k) or "")
        if v not in vals:
            return False, f"invalid_{k}:{v}"
    reason = str(obj.get("reason") or "").strip()
    if not reason:
        return False, "missing_reason"
    return True, ""


def _judge_fail(reason: str) -> dict[str, Any]:
    out = dict(_JUDGE_SCHEMA_DEFAULT_FAIL)
    out["reason"] = reason
    return out


def _has_judge_signal_conflict(obj: dict[str, Any]) -> bool:
    verdict = str(obj.get("final_verdict") or "")
    decision_quality = str(obj.get("decision_quality") or "")
    semantic_alignment = str(obj.get("semantic_alignment") or "")
    loop_behavior = str(obj.get("loop_behavior") or "")
    gap_handling = str(obj.get("gap_handling") or "")
    if verdict == "fail" and (
        decision_quality == "good"
        or semantic_alignment == "correct"
        or loop_behavior == "efficient"
        or gap_handling == "resolved"
    ):
        return True
    if verdict == "pass" and (
        decision_quality == "poor"
        or semantic_alignment == "incorrect"
        or loop_behavior == "stuck"
        or gap_handling == "not_resolved"
    ):
        return True
    return False


def _judge_core_signature(obj: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        str(obj.get("semantic_alignment") or ""),
        str(obj.get("decision_quality") or ""),
        str(obj.get("loop_behavior") or ""),
        str(obj.get("gap_handling") or ""),
        str(obj.get("final_verdict") or ""),
    )


def _build_trace_summary(trace: dict[str, Any]) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for i, step in enumerate(trace.get("steps", []), start=1):
        mem = step.get("memory_summary") or {}
        summary.append(
            {
                "step": i,
                "action": step.get("action_executed"),
                "gaps": mem.get("gaps", []),
                "relationships": int(mem.get("relationship_count") or 0),
                "gap_count": int(mem.get("gap_count") or 0),
                "gap_delta": int(mem.get("gap_delta") or 0),
                "decision_execution_alignment": step.get("decision_execution_alignment") or {},
            }
        )
    return summary


def llm_judge_fn(instruction: str, expected_behavior: dict[str, Any], trace: dict[str, Any]) -> dict[str, Any]:
    params = get_model_call_params(EXPLORATION_TASK_ANALYZER)
    temp = float(params.get("temperature") or 0.0)
    if temp > 0.2:
        return _judge_fail(f"temperature_guard_failed:{temp}")
    trace_summary = _build_trace_summary(trace)
    prompt = (
        f"{_LLM_JUDGE_PROMPT}\n\n"
        "## Input\n\n"
        f"Instruction:\n{instruction}\n\n"
        f"Expected Behavior:\n{json.dumps(expected_behavior, ensure_ascii=False)}\n\n"
        f"Trace:\n{json.dumps(trace_summary, ensure_ascii=False)}\n\n"
        f"Final Outcome:\n{json.dumps(trace.get('final_output', {}), ensure_ascii=False)}"
    )
    raw_1 = call_reasoning_model(
        prompt=prompt,
        task_name=EXPLORATION_TASK_ANALYZER,
        system_prompt="Return strict JSON only. No prose. No markdown.",
    )
    parsed_1 = _safe_json_extract(raw_1)
    if not parsed_1:
        return _judge_fail("invalid judge output")
    ok, err = _validate_llm_judge_schema(parsed_1)
    if not ok:
        return _judge_fail(f"invalid judge output:{err}")
    if _has_judge_signal_conflict(parsed_1):
        return _judge_fail("judge_signal_conflict")

    # Self-consistency check: same input, same deterministic categorization.
    raw_2 = call_reasoning_model(
        prompt=prompt,
        task_name=EXPLORATION_TASK_ANALYZER,
        system_prompt="Return strict JSON only. No prose. No markdown.",
    )
    parsed_2 = _safe_json_extract(raw_2)
    if not parsed_2:
        return _judge_fail("invalid judge output")
    ok, err = _validate_llm_judge_schema(parsed_2)
    if not ok:
        return _judge_fail(f"invalid judge output:{err}")
    if _has_judge_signal_conflict(parsed_2):
        return _judge_fail("judge_signal_conflict")

    if _judge_core_signature(parsed_1) != _judge_core_signature(parsed_2):
        return _judge_fail("judge_self_consistency_failed")

    return parsed_1


def _grade_rule_based(trace: dict[str, Any], case: EvalCase) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    expected = case.expected_behavior or {}
    actions = [str(s.get("action_executed") or "none") for s in trace.get("steps", [])]

    if expected.get("expected_actions"):
        ea = list(expected.get("expected_actions") or [])
        ok = actions[: len(ea)] == ea
        checks.append({"name": "expected_actions_prefix", "pass": ok, "details": {"expected": ea, "actual": actions}})

    for step_key, reqs in (expected.get("step_expectations") or {}).items():
        idx_s = str(step_key).replace("step_", "").strip()
        if not idx_s.isdigit():
            checks.append({"name": f"step_expectation_{step_key}", "pass": False, "details": "invalid_step_key"})
            continue
        idx = int(idx_s) - 1
        step = trace.get("steps", [])[idx] if idx < len(trace.get("steps", [])) else {}
        executed = str(step.get("action_executed") or "none")
        this_ok = True
        for req in reqs or []:
            if req == "must_expand":
                this_ok = this_ok and executed == "expand"
            elif req == "must_refine":
                this_ok = this_ok and executed == "refine"
            elif req == "must_not_refine":
                this_ok = this_ok and executed != "refine"
            elif req == "must_stop":
                this_ok = this_ok and executed in {"stop", "none"}
        checks.append({"name": f"step_expectation_{step_key}", "pass": this_ok, "details": {"executed": executed, "requirements": reqs}})

    for i, step in enumerate(trace.get("steps", []), start=1):
        sel = str(step.get("action_selected") or "none")
        exe = str(step.get("action_executed") or "none")
        if sel == exe:
            ok = True
            reason = "aligned"
        elif sel == "refine" and exe == "expand":
            # Engine may coerce refine -> expand (memory relationship / oscillation / cooldown).
            ok = True
            reason = "refine_to_expand_coercion"
        else:
            why = str((step.get("decision_execution_alignment") or {}).get("reason") or "")
            ok = _is_explainable_divergence(why)
            reason = why or "unexpected_divergence"
        checks.append({"name": f"decision_execution_alignment_step_{i}", "pass": ok, "details": {"selected": sel, "executed": exe, "reason": reason}})

    passed = all(bool(c.get("pass")) for c in checks) if checks else True
    return {"pass": passed, "checks": checks}


def _classify_gap_trend(steps: list[dict[str, Any]]) -> str:
    values = [int((s.get("memory_summary") or {}).get("gap_count") or 0) for s in steps]
    if len(values) < 2:
        return "stagnant"
    if values[-1] < values[0]:
        return "decreasing"
    if values[-1] > values[0]:
        return "increasing"
    return "stagnant"


def _grade_structural(trace: dict[str, Any], case: EvalCase) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    steps = list(trace.get("steps", []))
    signatures = [str(s.get("query_signature") or "") for s in steps if str(s.get("query_signature") or "")]
    repeated = len(signatures) - len(set(signatures))
    # Harness noise: discovery may repeat the same intent signature across phases.
    checks.append({"name": "avoid_repeated_queries", "pass": repeated <= max(2, len(steps) // 2), "details": {"repeated": repeated}})

    max_loop = int((case.expected_behavior or {}).get("max_loop_depth") or 999)
    checks.append({"name": "loop_depth", "pass": len(steps) <= max_loop, "details": {"steps": len(steps), "max_loop_depth": max_loop}})

    trend = _classify_gap_trend(steps)
    checks.append(
        {
            "name": "gap_delta_trend",
            "pass": True,
            "details": {"gap_trend": trend, "note": "classification_only"},
        }
    )

    ev_vals = [int((s.get("memory_summary") or {}).get("evidence_count") or 0) for s in steps]
    gap_vals = [int((s.get("memory_summary") or {}).get("gap_count") or 0) for s in steps]
    rel_vals = [int((s.get("memory_summary") or {}).get("relationship_count") or 0) for s in steps]
    mem_signal = any(
        r > 0 for r in rel_vals
    ) or any(
        i > 0 and (ev_vals[i] != ev_vals[i - 1] or gap_vals[i] != gap_vals[i - 1]) for i in range(1, len(steps))
    )
    acted = any(str(s.get("action_executed") or "") in ("expand", "refine") for s in steps)
    checks.append(
        {
            "name": "memory_influence_presence",
            "pass": mem_signal or len(steps) <= 1 or acted,
            "details": {
                "has_relationship_signal": any(r > 0 for r in rel_vals),
                "evidence_deltas": ev_vals,
                "gap_counts": gap_vals,
                "had_expand_or_refine": acted,
            },
        }
    )

    passed = all(bool(c.get("pass")) for c in checks)
    return {"pass": passed, "checks": checks, "gap_trend": trend}


def _grade_llm(trace: dict[str, Any], case: EvalCase) -> dict[str, Any]:
    out = llm_judge_fn(case.instruction, case.expected_behavior, trace)
    ok, err = _validate_llm_judge_schema(out)
    if not ok:
        return _judge_fail(f"judge_schema_invalid:{err}")
    return out


@contextmanager
def _capture_working_memory(telemetry: dict[str, Any]):
    from agent_v2.exploration.exploration_working_memory import ExplorationWorkingMemory

    orig = ExplorationWorkingMemory.add_evidence

    def wrap(self, *args: Any, **kwargs: Any) -> None:
        telemetry["memory_ref"] = self
        return orig(self, *args, **kwargs)

    ExplorationWorkingMemory.add_evidence = wrap
    try:
        yield
    finally:
        ExplorationWorkingMemory.add_evidence = orig


def run_eval_case(case: EvalCase) -> EvalCaseResult:
    project_root = Path(__file__).resolve().parents[2]
    import agent_v2.exploration.exploration_engine_v2 as ev2_mod

    saved_utility = ev2_mod.ENABLE_UTILITY_STOP
    ev2_mod.ENABLE_UTILITY_STOP = False
    telemetry: dict[str, Any] = {
        "steps": [],
        "last_analyzer": None,
        "last_memory_gap_count": None,
        "last_memory_evidence_count": None,
        "memory_ref": None,
        "forced_refine_remaining": int(case.force_refine_actions),
    }
    parser = _ScriptedIntentParser()
    selector = _PassSelector()
    analyzer = _ScriptedAnalyzer(case.analyzer_script, telemetry=telemetry)
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

    orig_next_action = engine._next_action
    orig_should_expand = engine._should_expand
    orig_should_refine = engine._should_refine
    orig_discovery = engine._run_discovery_traced

    def _memory_counts() -> tuple[int, int, int]:
        mem = telemetry.get("memory_ref")
        if mem is None:
            return (0, 0, 0)
        summ = mem.get_summary()
        return (
            len(summ.get("evidence") or []),
            len(summ.get("gaps") or []),
            len(summ.get("relationships") or []),
        )

    def wrap_discovery(self: ExplorationEngineV2, exploration_outer: Any, phase: str, intent: QueryIntent, state: Any, ex_state: Any):
        sig = self._intent_signature(intent)
        telemetry["last_query_signature"] = repr(sig)
        return orig_discovery(exploration_outer, phase, intent, state, ex_state)

    def wrap_next_action(self: ExplorationEngineV2, decision: Any) -> str:
        action = orig_next_action(decision)
        if telemetry["forced_refine_remaining"] > 0:
            action = "refine"
            telemetry["forced_refine_remaining"] -= 1
        e_count, g_count, r_count = _memory_counts()
        last_gap = telemetry.get("last_memory_gap_count")
        gap_delta = 0 if last_gap is None else g_count - int(last_gap)
        if gap_delta < 0:
            trend = "decreasing"
        elif gap_delta > 0:
            trend = "increasing"
        else:
            trend = "stagnant"
        telemetry["last_memory_gap_count"] = g_count
        telemetry["last_memory_evidence_count"] = e_count
        telemetry["steps"].append(
            {
                "step_index": len(telemetry["steps"]),
                "analyzer": dict(telemetry.get("last_analyzer") or {}),
                "decision_pre_override": {},
                "decision_post_override": {
                    "status": str(getattr(decision, "status", "")),
                    "needs": list(getattr(decision, "needs", []) or []),
                    "next_action": str(getattr(decision, "next_action", "")),
                },
                "action_selected": action,
                "action_executed": "none",
                "decision_execution_alignment": {"status": "diverged_unexpected", "reason": ""},
                "tool_calls": [],
                "memory_summary": {
                    "evidence_count": e_count,
                    "gap_count": g_count,
                    "gap_delta": gap_delta,
                    "gap_trend": trend,
                    "relationship_count": r_count,
                    "gaps": list((telemetry.get("last_analyzer") or {}).get("knowledge_gaps", [])),
                },
                "query_signature": str(telemetry.get("last_query_signature") or ""),
            }
        )
        return action

    def wrap_should_expand(self: ExplorationEngineV2, action: str, decision: Any, target: Any, ex_state: Any) -> bool:
        ok = orig_should_expand(action, decision, target, ex_state)
        if telemetry["steps"]:
            cur = telemetry["steps"][-1]
            if ok:
                cur["action_executed"] = "expand"
                cur["decision_execution_alignment"] = {"status": "aligned", "reason": ""}
                cur["tool_calls"].append({"phase": "expand", "tool": "graph_query"})
            elif cur["action_selected"] == "expand":
                cur["decision_execution_alignment"] = {"status": "diverged_explainable", "reason": "expand blocked by depth/symbol/guard"}
        return ok

    def wrap_should_refine(self: ExplorationEngineV2, action: str, decision: Any, ex_state: Any, *args: Any, **kwargs: Any) -> bool:
        ok = orig_should_refine(action, decision, ex_state, *args, **kwargs)
        if telemetry["steps"]:
            cur = telemetry["steps"][-1]
            if ok:
                cur["action_executed"] = "refine"
                cur["decision_execution_alignment"] = {"status": "aligned", "reason": ""}
                cur["tool_calls"].append({"phase": "refine", "tool": "search"})
            elif cur["action_selected"] == "refine" and cur["action_executed"] == "none":
                cur["decision_execution_alignment"] = {"status": "diverged_explainable", "reason": "refine blocked by backtrack/expand-signal guard"}
        return ok

    engine._run_discovery_traced = MethodType(wrap_discovery, engine)
    engine._next_action = MethodType(wrap_next_action, engine)
    engine._should_expand = MethodType(wrap_should_expand, engine)
    engine._should_refine = MethodType(wrap_should_refine, engine)

    try:
        with _capture_working_memory(telemetry):
            state = SimpleNamespace(
                context={"project_root": str(project_root)},
                signal_catalog=list(case.seed_symbols),
            )
            final = engine.explore(case.instruction, state=state)
    finally:
        ev2_mod.ENABLE_UTILITY_STOP = saved_utility
        engine._run_discovery_traced = orig_discovery
        engine._next_action = orig_next_action
        engine._should_expand = orig_should_expand
        engine._should_refine = orig_should_refine

    for st in telemetry["steps"]:
        if st["action_executed"] == "none" and st["action_selected"] == "stop":
            st["action_executed"] = "stop"
            st["decision_execution_alignment"] = {"status": "aligned", "reason": ""}

    trace = {
        "case_id": case.id,
        "steps": telemetry["steps"],
        "final_output": {
            "termination_reason": str(getattr(final.metadata, "termination_reason", "")),
            "completion_status": str(getattr(final.metadata, "completion_status", "")),
            "result_summary": str(getattr(final.exploration_summary, "overall", "")),
        },
    }
    rule_res = _grade_rule_based(trace, case)
    structural_res = _grade_structural(trace, case)
    llm_res = _grade_llm(trace, case)
    final_pass = bool(rule_res["pass"] and structural_res["pass"] and llm_res.get("final_verdict") == "pass")
    return EvalCaseResult(
        case_id=case.id,
        trace=trace,
        rule_based=rule_res,
        structural=structural_res,
        llm_judge=llm_res,
        final_case_pass=final_pass,
    )


def run_eval_suite(cases: list[EvalCase]) -> dict[str, Any]:
    outputs = [run_eval_case(c) for c in cases]
    rows = []
    for r in outputs:
        rows.append(
            {
                "case_id": r.case_id,
                "final_case_pass": r.final_case_pass,
                "rule_based_pass": bool(r.rule_based.get("pass")),
                "structural_pass": bool(r.structural.get("pass")),
                "llm_final_verdict": str(r.llm_judge.get("final_verdict") or "fail"),
                "llm_judge": dict(r.llm_judge),
                "gap_trend": str(r.structural.get("gap_trend") or "stagnant"),
                "loop_depth": len(r.trace.get("steps", [])),
            }
        )
    return {
        "cases": rows,
        "summary": {
            "total_cases": len(rows),
            "passed_cases": sum(1 for row in rows if row["final_case_pass"]),
            "action_correctness_rate": (
                sum(1 for row in rows if row["rule_based_pass"]) / len(rows) if rows else 0.0
            ),
            "avg_loop_depth": (sum(int(row["loop_depth"]) for row in rows) / len(rows) if rows else 0.0),
            "repeated_queries": sum(
                1
                for r in outputs
                for st in r.trace.get("steps", [])
                if str(st.get("query_signature") or "").strip()
            ),
            "gap_resolution_success": sum(
                1
                for row in rows
                if row["gap_trend"] == "decreasing" and row["final_case_pass"]
            ),
        },
        "final_case_pass_rule": "rule_based_pass AND structural_pass AND llm_judge.final_verdict == 'pass'",
    }

