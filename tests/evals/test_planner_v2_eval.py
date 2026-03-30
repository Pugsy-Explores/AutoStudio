"""
Live LLM evaluation for PlannerV2 decision JSON (tool + decision), no execution.

Uses production ``PlannerV2.plan`` → ``call_reasoning_model`` with the same task scoping as
runtime (``planner_model_task_scope`` inside ``plan()``). Prompts are unchanged.

Run:

  export PLANNER_V2_EVAL_LIVE=1
  pytest tests/evals/test_planner_v2_eval.py -v -s -m planner_v2_eval

CI: skipped unless PLANNER_V2_EVAL_LIVE=1.

YAML: ``instruction``, ``context`` (exploration-shaped dict), optional ``path: replan`` +
``replan`` block, optional ``session``, ``expected`` constraints.

Sequence cases: ``tests/evals/planner_v2/sequence_cases.yaml`` — ``steps`` with per-step
``context`` (merged cumulatively), ``expect``, optional ``session``; session evolves via
``record_planner_output`` between steps.

Query checks: optional ``expected.query_must_have_domain_signal`` and
``expected.query_must_not_echo_instruction`` (hard fail for explore/act when set).
"""

from __future__ import annotations

import os
import re
from collections import Counter
from pathlib import Path
from typing import Any, Literal

import pytest
import yaml

from agent.models.model_client import call_reasoning_model
from agent_v2.planner.planner_v2 import PlannerV2
from agent_v2.planner.planner_model_call_context import get_active_planner_model_task
from agent_v2.schemas.execution import ErrorType
from agent_v2.schemas.exploration import (
    ExplorationContent,
    ExplorationItem,
    ExplorationItemMetadata,
    ExplorationRelevance,
    ExplorationResultMetadata,
    ExplorationSource,
    ExplorationSummary,
)
from agent_v2.schemas.final_exploration import ExplorationAdapterTrace, FinalExplorationSchema
from agent_v2.schemas.planner_plan_context import PlannerPlanContext
from agent_v2.schemas.policies import ExecutionPolicy
from agent_v2.schemas.replan import (
    ReplanCompletedStep,
    ReplanContext,
    ReplanExplorationSummary,
    ReplanFailureContext,
    ReplanFailureError,
)
from agent_v2.runtime.session_memory import SessionMemory
from agent_v2.runtime.tool_policy import ACT_MODE_TOOL_POLICY, PLAN_MODE_TOOL_POLICY

Tier = Literal["easy", "medium", "hard", "edge"]

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_EVAL_DIR = Path(__file__).resolve().parent / "planner_v2"
_CASE_FILES: dict[Tier, str] = {
    "easy": "easy_cases.yaml",
    "medium": "medium_cases.yaml",
    "hard": "hard_cases.yaml",
    "edge": "edge_cases.yaml",
}
_SEQUENCE_CASES_FILE = "sequence_cases.yaml"

_GENERIC_QUERIES = frozenset(
    {
        "search",
        "find",
        "code",
        "look",
        "more",
        "continue",
        "explore",
        "investigate",
        "check",
        "see",
    }
)

# Repo / code-ish signal: path-like, file extensions, identifiers, or common domain tokens.
_DOMAIN_SIGNAL = re.compile(
    r"[\w./\\-]+\.(?:py|ts|js|tsx|jsx|md)\b|"
    r"\b(?:agent_|agent\.|repo|module|class|def|function|implementation|planner|explore|"
    r"validator|schema|runtime|tests?/|src/)\b|"
    r"[/\\][\w./-]{2,}|"
    r"\b[A-Z][a-z]+(?:[A-Z][a-z]+)+\b",
    re.IGNORECASE,
)

# Phrases that look like a query but carry no retrieval target (warn / fail when domain required).
_USELESS_QUERY_PHRASES = re.compile(
    r"^(?:search\s+code|find\s+code|get\s+code|look\s+for\s+code|read\s+code|more\s+code)\s*\.?$",
    re.IGNORECASE,
)


def _load_cases(tier: Tier) -> list[dict[str, Any]]:
    path = _EVAL_DIR / _CASE_FILES[tier]
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(raw, list)
    return raw


def _load_sequence_cases() -> list[dict[str, Any]]:
    path = _EVAL_DIR / _SEQUENCE_CASES_FILE
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(raw, list)
    return raw


def _merge_exploration_context(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    """Shallow merge for sequence steps; keys in ``update`` replace ``base``."""
    out = dict(base)
    out.update(update)
    return out


def _normalize_step_expect(raw: Any) -> dict[str, Any]:
    if isinstance(raw, str):
        s = raw.strip()
        return {"must_decision": s} if s else {}
    if isinstance(raw, dict):
        return dict(raw)
    return {}


def _evolve_session_from_decision(sm: SessionMemory | None, decision: str, tool: str) -> SessionMemory:
    m = sm.model_copy(deep=True) if sm is not None else SessionMemory()
    m.record_planner_output(decision=decision, tool=tool or "none")
    return m


def _minimal_evidence_item(idx: int) -> ExplorationItem:
    return ExplorationItem(
        item_id=f"eval_{idx}",
        type="file",
        source=ExplorationSource(ref="eval_placeholder.py"),
        content=ExplorationContent(summary="Eval fixture evidence", key_points=[], entities=[]),
        relevance=ExplorationRelevance(score=0.5, reason="eval"),
        metadata=ExplorationItemMetadata(timestamp="2026-03-30T00:00:00Z", tool_name="eval"),
    )


def _exploration_summary_from_context(raw: dict[str, Any]) -> ExplorationSummary:
    gaps = [str(x).strip() for x in (raw.get("gaps") or raw.get("knowledge_gaps") or []) if str(x).strip()]
    findings = [
        str(x).strip() for x in (raw.get("current_findings") or raw.get("key_findings") or []) if str(x).strip()
    ]
    explored = [str(x).strip() for x in (raw.get("explored_locations") or []) if str(x).strip()]
    key_findings = list(findings)
    for loc in explored:
        key_findings.append(f"Explored: {loc}")
    overall = (raw.get("overall") or "").strip() or "(none)"
    if not key_findings:
        key_findings = ["(none)"]
    if not gaps:
        empty_reason = (
            raw.get("gaps_empty_reason") or raw.get("knowledge_gaps_empty_reason") or "Eval fixture: no gaps listed."
        )
        empty_reason = str(empty_reason).strip()
        return ExplorationSummary(
            overall=overall,
            key_findings=key_findings,
            knowledge_gaps=[],
            knowledge_gaps_empty_reason=empty_reason,
        )
    return ExplorationSummary(
        overall=overall,
        key_findings=key_findings,
        knowledge_gaps=gaps,
        knowledge_gaps_empty_reason=None,
    )


def _final_exploration_from_case(instruction: str, ctx: dict[str, Any]) -> FinalExplorationSchema:
    es = _exploration_summary_from_context(ctx)
    conf = str(ctx.get("confidence") or "medium").strip().lower()
    if conf not in ("high", "medium", "low"):
        conf = "medium"
    status = str(ctx.get("status") or "incomplete").strip().lower()
    if status not in ("complete", "incomplete"):
        status = "incomplete"
    return FinalExplorationSchema(
        exploration_id=f"eval_{(hash(instruction) & 0xFFFFFFFF):x}",
        instruction=instruction,
        status=status,  # type: ignore[arg-type]
        evidence=[_minimal_evidence_item(0)],
        relationships=[],
        exploration_summary=es,
        metadata=ExplorationResultMetadata(
            total_items=1,
            created_at="2026-03-30T00:00:00Z",
            completion_status="complete" if status == "complete" else "incomplete",
            termination_reason=str(ctx.get("termination_reason") or "eval"),
        ),
        confidence=conf,  # type: ignore[arg-type]
        trace=ExplorationAdapterTrace(llm_used=False, synthesis_success=True),
    )


def _session_from_case(case: dict[str, Any]) -> SessionMemory | None:
    raw = case.get("session")
    if not isinstance(raw, dict) or not raw:
        return None
    return SessionMemory.model_validate(raw)


def _planner_context_from_case(case: dict[str, Any]) -> PlannerPlanContext:
    instruction = str(case.get("instruction") or "")
    path = str(case.get("path") or "exploration").strip().lower()
    if path == "replan":
        r = case.get("replan") or {}
        fc = r.get("failure_context") or {}
        err = fc.get("error") or {}
        et_raw = str(err.get("type") or "unknown").strip().lower()
        try:
            et = ErrorType(et_raw)
        except ValueError:
            et = ErrorType.unknown
        failure = ReplanFailureContext(
            step_id=str(fc.get("step_id") or "s0"),
            error=ReplanFailureError(type=et, message=str(err.get("message") or "error")),
            attempts=int(fc.get("attempts") or 1),
            last_output_summary=str(fc.get("last_output_summary") or ""),
        )
        completed_raw = r.get("completed_steps") or []
        completed: list[ReplanCompletedStep] = []
        for row in completed_raw:
            if not isinstance(row, dict):
                continue
            completed.append(
                ReplanCompletedStep(step_id=str(row.get("step_id") or ""), summary=str(row.get("summary") or ""))
            )
        es_raw = r.get("exploration_summary")
        expl_summary: ReplanExplorationSummary | None = None
        if isinstance(es_raw, dict):
            expl_summary = ReplanExplorationSummary(
                overall=str(es_raw.get("overall") or "(none)"),
                key_findings=[str(x) for x in (es_raw.get("key_findings") or []) if str(x).strip()],
                knowledge_gaps=[str(x) for x in (es_raw.get("knowledge_gaps") or []) if str(x).strip()],
            )
        trig = str(r.get("trigger") or "failure").strip().lower()
        if trig not in ("failure", "insufficiency"):
            trig = "failure"
        rc = ReplanContext(
            failure_context=failure,
            completed_steps=completed,
            exploration_summary=expl_summary,
            trigger=trig,  # type: ignore[arg-type]
            task_control_last_outcome=(
                str(r["task_control_last_outcome"]).strip() if r.get("task_control_last_outcome") else None
            ),
            explore_block_details=r.get("explore_block_details") if isinstance(r.get("explore_block_details"), dict) else None,
        )
        eb = case.get("exploration_budget")
        return PlannerPlanContext(
            replan=rc,
            session=_session_from_case(case),
            exploration_budget=int(eb) if eb is not None else None,
        )

    return _planner_context_exploration(
        instruction,
        case.get("context") or {},
        _session_from_case(case),
        int(case["exploration_budget"]) if case.get("exploration_budget") is not None else None,
    )


def _planner_context_exploration(
    instruction: str,
    context_dict: dict[str, Any],
    session: SessionMemory | None,
    exploration_budget: int | None,
) -> PlannerPlanContext:
    fe = _final_exploration_from_case(instruction, context_dict)
    return PlannerPlanContext(
        exploration=fe,
        session=session,
        exploration_budget=exploration_budget,
    )


def _norm(s: Any) -> str:
    return str(s or "").strip().lower()


def _query_has_domain_signal(q: str) -> bool:
    q = (q or "").strip()
    if len(q) < 4:
        return False
    if _USELESS_QUERY_PHRASES.match(q):
        return False
    return bool(_DOMAIN_SIGNAL.search(q))


def _query_echoes_instruction(query: str, instruction: str) -> bool:
    qi = (query or "").strip().lower()
    ii = (instruction or "").strip().lower()
    if not qi or not ii:
        return False
    if qi == ii:
        return True
    if len(ii) >= 24 and ii in qi:
        return True
    return False


def _query_hard_failures(
    exp: dict[str, Any], decision: str, query: str, instruction: str
) -> list[str]:
    """Hard fails when YAML opts in with ``query_must_*`` flags (explore/act only)."""
    errs: list[str] = []
    d = _norm(decision)
    if d not in ("explore", "act"):
        return errs
    q = (query or "").strip()
    if exp.get("query_must_have_domain_signal") and not _query_has_domain_signal(q):
        errs.append("query_must_have_domain_signal: missing path/symbol/repo token")
    if exp.get("query_must_not_echo_instruction") and _query_echoes_instruction(q, instruction):
        errs.append("query_must_not_echo_instruction: query repeats user instruction")
    return errs


def _query_quality_warnings(decision: str, query: str, instruction: str = "") -> list[str]:
    q = (query or "").strip()
    d = _norm(decision)
    w: list[str] = []
    if d in ("explore", "act") and not q:
        w.append("empty planner query for explore/act")
    low = q.lower()
    if q and len(q) < 12 and d in ("explore", "act"):
        w.append("very short planner query (may be too generic)")
    if low in _GENERIC_QUERIES or (len(q.split()) <= 2 and low in _GENERIC_QUERIES):
        w.append("generic planner query")
    if d in ("explore", "act") and q and _USELESS_QUERY_PHRASES.match(q):
        w.append('useless query phrase (e.g. "search code" with no target)')
    if d in ("explore", "act") and q and not _query_has_domain_signal(q):
        w.append("weak query: no domain signal (path, module, or symbol)")
    if d in ("explore", "act") and q and instruction.strip() and _query_echoes_instruction(q, instruction):
        w.append("query likely echoes instruction (no refined target)")
    return w


def _norm_list(key: str, exp: dict[str, Any]) -> list[str]:
    v = exp.get(key)
    if v is None:
        return []
    if isinstance(v, list):
        return [_norm(x) for x in v if str(x).strip()]
    return [_norm(v)] if str(v).strip() else []


def _failure_patterns(
    decision: str,
    tool: str,
    exp: dict[str, Any],
) -> list[str]:
    """Best-effort labels for report (only when expectations were explicit)."""
    d, t = _norm(decision), _norm(tool)
    out: list[str] = []
    md = _norm(exp.get("must_decision"))
    if md and d != md:
        if md == "explore" and d == "synthesize":
            out.append("premature synthesis")
        elif md == "synthesize" and d == "explore":
            out.append("over-exploring")
        elif md == "act" and d != "act":
            out.append("wrong tool selection")
    for forbidden in _norm_list("must_not_decision", exp):
        if forbidden and d == forbidden:
            out.append("wrong tool selection")
    for forbidden in _norm_list("must_not_tool", exp):
        if forbidden and t == forbidden:
            out.append("wrong tool selection")
    return list(dict.fromkeys(out))


def _validate_expected(
    decision: str,
    tool: str,
    exp: dict[str, Any],
) -> tuple[bool, list[str], list[str]]:
    """
    Returns: (hard_fail, warnings, failure_patterns)
    """
    warnings: list[str] = []
    d, t = _norm(decision), _norm(tool)

    any_d = _norm_list("any_decision", exp)
    if any_d:
        if d not in any_d:
            return True, [], _failure_patterns(decision, tool, exp)
    else:
        md = exp.get("must_decision")
        if md is not None and str(md).strip() and d != _norm(md):
            return True, [], _failure_patterns(decision, tool, exp)

    any_t = _norm_list("any_of_tool", exp)
    if any_t:
        if t not in any_t:
            return True, [], _failure_patterns(decision, tool, exp)
    else:
        mt = exp.get("must_tool")
        if mt is not None and str(mt).strip() and t != _norm(mt):
            return True, [], _failure_patterns(decision, tool, exp)

    for forbidden in _norm_list("must_not_decision", exp):
        if forbidden and d == forbidden:
            return True, [], _failure_patterns(decision, tool, exp)
    for forbidden in _norm_list("must_not_tool", exp):
        if forbidden and t == forbidden:
            return True, [], _failure_patterns(decision, tool, exp)

    wtool = exp.get("warn_if_tool")
    if isinstance(wtool, str) and t == _norm(wtool):
        warnings.append(f"suboptimal tool (matched warn_if_tool={wtool!r})")

    wdec = exp.get("warn_if_decision")
    if isinstance(wdec, str) and d == _norm(wdec):
        warnings.append(f"suboptimal decision (matched warn_if_decision={wdec!r})")

    return False, warnings, []


def _make_planner(tool_policy_mode: str) -> PlannerV2:
    def generate_fn(user_prompt: str, system_prompt: str | None = None) -> str:
        task = get_active_planner_model_task()
        return call_reasoning_model(
            user_prompt,
            system_prompt=system_prompt,
            task_name=task or "PLANNER_DECISION_ACT",
        )

    pol = ExecutionPolicy(max_steps=8, max_retries_per_step=2, max_replans=2)
    mode = (tool_policy_mode or "act").strip().lower()
    tp = PLAN_MODE_TOOL_POLICY if mode == "plan" else ACT_MODE_TOOL_POLICY
    return PlannerV2(generate_fn=generate_fn, policy=pol, tool_policy=tp)


def _run_one(case: dict[str, Any], tier: Tier) -> dict[str, Any]:
    cid = str(case["id"])
    instruction = str(case.get("instruction") or "")
    exp = case.get("expected") or {}
    deep = bool(case.get("deep", False))
    tool_policy = str(case.get("tool_policy") or "act")
    validation_task_mode = case.get("validation_task_mode")

    ctx = _planner_context_from_case(case)
    planner = _make_planner(tool_policy)

    try:
        doc = planner.plan(
            instruction,
            ctx,
            deep=deep,
            validation_task_mode=validation_task_mode,
        )
    except Exception as e:
        return {
            "id": cid,
            "tier": tier,
            "hard_fail": True,
            "error": repr(e),
            "decision": None,
            "tool": None,
            "query": None,
            "warnings": [f"planner raised: {e!r}"],
            "failure_patterns": ["planner exception / invalid JSON"],
        }

    eng = doc.engine
    decision = _norm(eng.decision) if eng else ""
    tool = _norm(eng.tool) if eng else ""
    query = str(eng.query or "") if eng else ""

    hf, w1, fp = _validate_expected(decision, tool, exp)
    qh = _query_hard_failures(exp, decision, query, instruction)
    if qh:
        hf = True
        fp = fp + ["query constraint violation"]
    w2 = _query_quality_warnings(decision, query, instruction)
    warnings = list(dict.fromkeys([*w1, *w2, *qh]))

    return {
        "id": cid,
        "tier": tier,
        "hard_fail": hf,
        "error": None,
        "decision": decision,
        "tool": tool,
        "query": query,
        "warnings": warnings,
        "failure_patterns": fp,
    }


def _run_sequence(case: dict[str, Any]) -> dict[str, Any]:
    cid = str(case["id"])
    instruction = str(case.get("instruction") or "")
    steps_raw = case.get("steps")
    if not isinstance(steps_raw, list) or not steps_raw:
        return {
            "id": cid,
            "tier": "sequence",
            "hard_fail": True,
            "error": "sequence case has no steps",
            "decision": None,
            "tool": None,
            "query": None,
            "warnings": [],
            "failure_patterns": ["invalid sequence fixture"],
        }

    planner = _make_planner(str(case.get("tool_policy") or "act"))
    deep = bool(case.get("deep", False))
    validation_task_mode = case.get("validation_task_mode")
    eb = int(case["exploration_budget"]) if case.get("exploration_budget") is not None else None

    base_ctx: dict[str, Any] = dict(case.get("context") or {})
    sm: SessionMemory | None = _session_from_case(case)
    prev_decision: str | None = None
    prev_tool = ""

    for i, step in enumerate(steps_raw):
        if not isinstance(step, dict):
            continue
        step_ctx = dict(step.get("context") or {})
        merged = _merge_exploration_context(base_ctx, step_ctx)
        base_ctx = merged

        if i > 0 and prev_decision is not None:
            sm = _evolve_session_from_decision(sm, prev_decision, prev_tool)
        if isinstance(step.get("session"), dict) and step["session"]:
            prev_dump = sm.model_dump() if sm is not None else {}
            sm = SessionMemory.model_validate({**prev_dump, **step["session"]})

        exp = _normalize_step_expect(step.get("expect"))
        if not exp and isinstance(step.get("expected"), dict):
            exp = dict(step["expected"])

        pctx = _planner_context_exploration(instruction, merged, sm, eb)

        try:
            doc = planner.plan(
                instruction,
                pctx,
                deep=deep,
                validation_task_mode=validation_task_mode,
            )
        except Exception as e:
            return {
                "id": f"{cid}#step{i}",
                "tier": "sequence",
                "hard_fail": True,
                "error": repr(e),
                "decision": None,
                "tool": None,
                "query": None,
                "warnings": [f"planner raised: {e!r}"],
                "failure_patterns": ["planner exception / invalid JSON"],
                "sequence_id": cid,
                "step_index": i,
            }

        eng = doc.engine
        decision = _norm(eng.decision) if eng else ""
        tool = _norm(eng.tool) if eng else ""
        query = str(eng.query or "") if eng else ""

        hf, w1, fp = _validate_expected(decision, tool, exp)
        qh = _query_hard_failures(exp, decision, query, instruction)
        if qh:
            hf = True
            fp = fp + ["query constraint violation"]
        w2 = _query_quality_warnings(decision, query, instruction)
        warnings = list(dict.fromkeys([*w1, *w2, *qh]))

        if hf:
            return {
                "id": f"{cid}#step{i}",
                "tier": "sequence",
                "hard_fail": True,
                "error": None,
                "decision": decision,
                "tool": tool,
                "query": query,
                "warnings": warnings,
                "failure_patterns": fp,
                "sequence_id": cid,
                "step_index": i,
            }

        prev_decision = decision
        prev_tool = tool

    return {
        "id": cid,
        "tier": "sequence",
        "hard_fail": False,
        "error": None,
        "decision": None,
        "tool": None,
        "query": None,
        "warnings": [],
        "failure_patterns": [],
    }


def _print_report(records: list[tuple[str, dict[str, Any], dict[str, Any]]]) -> None:
    n = len(records)
    ok = sum(1 for _, _, r in records if not r.get("hard_fail"))
    fails = [(t, c, r) for t, c, r in records if r.get("hard_fail")]
    warned = [(t, c, r) for t, c, r in records if r.get("warnings")]

    dec_counts: Counter[str] = Counter()
    tool_counts: Counter[str] = Counter()
    for _, _, r in records:
        if r.get("decision"):
            dec_counts[str(r["decision"])] += 1
        if r.get("tool"):
            tool_counts[str(r["tool"])] += 1

    fp_all: Counter[str] = Counter()
    for _, _, r in records:
        for p in r.get("failure_patterns") or []:
            fp_all[p] += 1

    lines: list[str] = [
        "=== PLANNER V2 DIAGNOSTIC REPORT ===",
        "",
        "[SUMMARY]",
        f"Total cases: {n}",
        f"Accuracy: {ok}/{n}",
        "",
        "[FAILURES]",
        "",
    ]
    if not fails:
        lines.append("(none)")
        lines.append("")
    else:
        for tier, case, r in fails:
            seq = f" seq={r.get('sequence_id')!r} step={r.get('step_index')!r}" if r.get("sequence_id") else ""
            lines.append(
                f"- {r['id']} (tier={tier}){seq}: decision={r.get('decision')!r} "
                f"tool={r.get('tool')!r} err={r.get('error')}"
            )
        lines.append("")

    lines.append("[WARNINGS]")
    lines.append("")
    if not warned or all(not r.get("warnings") for _, _, r in warned):
        lines.append("(none)")
        lines.append("")
    else:
        for tier, case, r in warned:
            if not r.get("warnings"):
                continue
            lines.append(f"--- {r['id']} ({tier}) ---")
            for w in r["warnings"]:
                lines.append(f"- {w}")
            lines.append("")

    lines.append("[FAILURE PATTERNS]")
    lines.append("")
    if fp_all:
        for p, c in fp_all.most_common():
            lines.append(f"- {p}: {c}")
    else:
        lines.append("(none)")
    lines.append("")
    lines.append("=== DECISION COVERAGE ===")
    lines.append("")
    for k in sorted(dec_counts.keys()):
        lines.append(f"{k}: {dec_counts[k]}")
    if not dec_counts:
        lines.append("(no decisions recorded)")
    lines.append("")
    lines.append("=== TOOL COVERAGE (engine.tool) ===")
    lines.append("")
    for k in sorted(tool_counts.keys()):
        lines.append(f"{k}: {tool_counts[k]}")
    if not tool_counts:
        lines.append("(no tools recorded)")
    lines.append("")
    lines.append("=== END REPORT ===")
    lines.append("")
    print("\n".join(lines))


@pytest.mark.slow
@pytest.mark.planner_v2_eval
@pytest.mark.skipif(
    os.environ.get("PLANNER_V2_EVAL_LIVE") != "1",
    reason="Set PLANNER_V2_EVAL_LIVE=1 to run live PlannerV2 decision eval.",
)
def test_planner_v2_eval_suite() -> None:
    records: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for tier in ("easy", "medium", "hard", "edge"):
        for case in _load_cases(tier):
            row = _run_one(case, tier)
            records.append((tier, case, row))

    for case in _load_sequence_cases():
        row = _run_sequence(case)
        records.append(("sequence", case, row))

    if not records:
        pytest.fail("No eval cases loaded.")

    _print_report(records)

    bad = [r for _, _, r in records if r.get("hard_fail")]
    if bad:
        detail = "; ".join(
            f"{r['id']}(decision={r.get('decision')!r} tool={r.get('tool')!r} err={r.get('error')!r})"
            for r in bad
        )
        pytest.fail(f"Planner V2 eval hard failure(s): {detail}")


def test_planner_v2_yaml_fixtures_load() -> None:
    """Fast structural check (no LLM): all tier YAML files parse and build context."""
    for tier in ("easy", "medium", "hard", "edge"):
        for case in _load_cases(tier):
            ctx = _planner_context_from_case(case)
            assert ctx is not None
            assert case.get("id")

    for case in _load_sequence_cases():
        assert case.get("id")
        base = dict(case.get("context") or {})
        for step in case.get("steps") or []:
            if isinstance(step, dict):
                base = _merge_exploration_context(base, dict(step.get("context") or {}))
        _planner_context_exploration(str(case.get("instruction") or ""), base, None, None)
