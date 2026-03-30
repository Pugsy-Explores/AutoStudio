"""
V1 AnswerSynthesizer: runs after exploration, before planner (see Docs/agent_v2_answer_synthesis_audit_and_spec.md).

Prompts load like other Agent V2 stages: :func:`~agent.prompt_system.registry.get_registry`
:meth:`~agent.prompt_system.registry.PromptRegistry.render_prompt_parts` with ``version="latest"``,
``model_name=get_prompt_model_name_for_task(ANSWER_SYNTHESIS_TASK)`` (Option B →
``agent/prompt_versions/answer_synthesis/models/<model>/v*.yaml`` when present).

LLM entry: :func:`call_reasoning_model` with task ``ANSWER_SYNTHESIS``.
Output: sectioned text (Answer / Explanation / Evidence / Gaps / Confidence), with optional JSON fallback.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from agent.models.model_client import call_reasoning_model
from agent.models.model_config import get_prompt_model_name_for_task
from agent.prompt_system.registry import get_registry
from agent_v2.config import (
    ANSWER_SYNTHESIS_IDEAL_EVIDENCE_ITEMS,
    ANSWER_SYNTHESIS_MAX_EVIDENCE_ITEMS,
    ENABLE_ANSWER_SYNTHESIS,
    get_config,
)
from agent_v2.planning.exploration_outcome_policy import normalize_understanding
from agent_v2.schemas.answer_synthesis import (
    AnswerSynthesisInput,
    AnswerSynthesisResult,
    CitationRef,
    derive_answer_synthesis_coverage,
)
from agent_v2.schemas.exploration import ExplorationItem
from agent_v2.schemas.final_exploration import FinalExplorationSchema
from agent_v2.utils.json_extractor import JSONExtractor

_LOG = logging.getLogger(__name__)

ANSWER_SYNTHESIS_TASK = "ANSWER_SYNTHESIS"
PROMPT_REGISTRY_KEY = "answer_synthesis"
_KEY_FINDING_CAP = 800
_SNIPPET_LINE_CAP = 600

_SECTION_SPLIT_RE = re.compile(
    r"(?mi)^\s*(Answer|Explanation|Evidence|Gaps|Confidence)\s*:\s*",
)


def _fmt_safe(s: str) -> str:
    """Escape braces so :meth:`str.format_map` does not interpret snippet/code content."""
    return (s or "").replace("{", "{{").replace("}", "}}")


def _snippet_preview(snippet: str) -> str:
    s = (snippet or "").strip()
    if len(s) <= _SNIPPET_LINE_CAP:
        return s
    return s[:_SNIPPET_LINE_CAP] + "…"


def _select_evidence_rows(evidence: list[ExplorationItem]) -> list[ExplorationItem]:
    """Rank by relevance score (desc), cap for 7B prompts (ideal 3–6, max 8)."""
    rows = list(evidence or [])
    rows.sort(key=lambda e: float(e.relevance.score or 0.0), reverse=True)
    return rows[:ANSWER_SYNTHESIS_MAX_EVIDENCE_ITEMS]


def _evidence_structured_block(inp: AnswerSynthesisInput) -> str:
    rows = _select_evidence_rows(inp.evidence)
    if not rows:
        return "(no evidence rows)"
    lines: list[str] = []
    for ev in rows:
        fp = str(ev.source.ref or "").strip()
        loc = str(ev.source.location or "").strip()
        summ = str(ev.content.summary or "").strip()[:500]
        lines.append(
            f"- file: {fp}\n"
            f"  location: {loc or '—'}\n"
            f"  summary: {summ}\n"
            f"  item_id: {ev.item_id}\n"
            f"  read_source: {ev.read_source or '—'}\n"
            f"  snippet: {_snippet_preview(ev.snippet or '')}"
        )
    return "\n\n".join(lines)


def _relationships_block(inp: AnswerSynthesisInput) -> str:
    lines: list[str] = []
    for e in inp.relationships[:24]:
        lines.append(f"- {e.from_key} --{e.type}--> {e.to_key} (confidence={e.confidence})")
    return "\n".join(lines) if lines else "(none)"


def _key_findings_block(inp: AnswerSynthesisInput) -> str:
    kf = inp.exploration.exploration_summary.key_findings or []
    lines = [str(x).strip()[:_KEY_FINDING_CAP] for x in kf if str(x).strip()]
    return "\n".join(f"- {x}" for x in lines) if lines else "(none)"


def _understanding_distilled(inp: AnswerSynthesisInput) -> str:
    """Short synthesized context only — no raw QueryIntent JSON."""
    es = inp.exploration.exploration_summary
    overall = (es.overall or "").strip()[:4000]
    parts = [f"Summary: {overall}"]
    oc = (inp.exploration.objective_coverage or "").strip()
    if oc:
        parts.append(f"Objective coverage: {oc[:1200]}")
    insights = [str(x).strip() for x in (inp.exploration.key_insights or []) if str(x).strip()][:4]
    if insights:
        parts.append("Key insights (short):\n" + "\n".join(f"- {x[:400]}" for x in insights))
    return "\n\n".join(parts)


def _open_questions_block(inp: AnswerSynthesisInput) -> str:
    gaps = [str(g).strip() for g in inp.knowledge_gaps if str(g).strip()]
    if gaps:
        return "\n".join(f"- {g}" for g in gaps)
    reason = (inp.exploration.exploration_summary.knowledge_gaps_empty_reason or "").strip()
    if reason:
        return f"(none listed — empty_reason: {reason})"
    return "(none)"


def _build_prompt_variables(inp: AnswerSynthesisInput) -> dict[str, str]:
    n_ev = len(_select_evidence_rows(inp.evidence))
    if n_ev > ANSWER_SYNTHESIS_IDEAL_EVIDENCE_ITEMS:
        _LOG.debug(
            "answer_synthesis evidence_count=%s (ideal<=%s, max=%s)",
            n_ev,
            ANSWER_SYNTHESIS_IDEAL_EVIDENCE_ITEMS,
            ANSWER_SYNTHESIS_MAX_EVIDENCE_ITEMS,
        )
    return {
        "instruction": _fmt_safe(inp.instruction.strip()[:8000]),
        "key_findings": _fmt_safe(_key_findings_block(inp)),
        "understanding": _fmt_safe(_understanding_distilled(inp)),
        "relationships": _fmt_safe(_relationships_block(inp)),
        "evidence": _fmt_safe(_evidence_structured_block(inp)),
        "system_confidence": _fmt_safe(str(inp.confidence or "unknown")),
        "coverage": inp.coverage,
        "open_questions": _fmt_safe(_open_questions_block(inp)),
    }


def _strip_code_fences(text: str) -> str:
    t = (text or "").strip()
    if not t.startswith("```"):
        return t
    lines = t.split("\n")
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _parse_sectioned_synthesis(raw: str, inp: AnswerSynthesisInput) -> AnswerSynthesisResult | None:
    text = _strip_code_fences(raw)
    parts = _SECTION_SPLIT_RE.split(text)
    if len(parts) < 3:
        return None
    sections: dict[str, str] = {}
    i = 1
    while i + 1 < len(parts):
        header = parts[i].strip().lower()
        body = parts[i + 1].strip()
        sections[header] = body
        i += 2
    ans = sections.get("answer", "").strip()
    expl = sections.get("explanation", "").strip()
    ev_block = sections.get("evidence", "").strip()
    gaps = sections.get("gaps", "").strip()
    conf_line = sections.get("confidence", "").strip()
    if not ans and not expl:
        return None
    cites = _citations_from_evidence_section(ev_block)
    unc = gaps if gaps else None
    if unc and unc.lower() in ("none", "n/a"):
        unc = None
    return AnswerSynthesisResult(
        direct_answer=ans[:20000],
        structured_explanation=expl[:50000],
        citations=cites,
        uncertainty=unc[:8000] if unc else None,
        stated_confidence=conf_line[:32] if conf_line else None,
        coverage=inp.coverage,
        synthesis_success=True,
        error=None,
    )


def _citations_from_evidence_section(block: str) -> list[CitationRef]:
    out: list[CitationRef] = []
    seen: set[tuple[str, str, str]] = set()
    for m in re.finditer(r"(?i)item_id:\s*(\S+)", block):
        iid = m.group(1).strip()[:200]
        key = (iid, "", "")
        if key not in seen:
            seen.add(key)
            out.append(CitationRef(item_id=iid, file="", symbol=""))
    for m in re.finditer(r"(?i)file:\s*(\S+)", block):
        fp = m.group(1).strip()[:2000]
        key = ("", fp, "")
        if key not in seen:
            seen.add(key)
            out.append(CitationRef(item_id="", file=fp, symbol=""))
    return out[:64]


def _parse_synthesis_json_fallback(raw: str, inp: AnswerSynthesisInput) -> AnswerSynthesisResult:
    parsed = JSONExtractor.extract_final_json(raw)
    if not isinstance(parsed, dict):
        raise ValueError("answer synthesis JSON is not an object")
    direct = str(parsed.get("direct_answer") or "").strip()
    struct = str(parsed.get("structured_explanation") or "").strip()
    unc = parsed.get("uncertainty")
    uncertainty: str | None = None
    if unc is not None and str(unc).strip():
        uncertainty = str(unc).strip()[:4000]
    cites_raw = parsed.get("citations")
    citations: list[CitationRef] = []
    if isinstance(cites_raw, list):
        for row in cites_raw[:64]:
            if not isinstance(row, dict):
                continue
            citations.append(
                CitationRef(
                    item_id=str(row.get("item_id") or "")[:200],
                    file=str(row.get("file") or "")[:2000],
                    symbol=str(row.get("symbol") or "")[:500],
                )
            )
    return AnswerSynthesisResult(
        direct_answer=direct[:20000],
        structured_explanation=struct[:50000],
        citations=citations,
        uncertainty=uncertainty,
        stated_confidence=None,
        coverage=inp.coverage,
        synthesis_success=True,
        error=None,
    )


def _parse_model_output(raw: str, inp: AnswerSynthesisInput) -> AnswerSynthesisResult:
    sec = _parse_sectioned_synthesis(raw, inp)
    if sec is not None and (sec.direct_answer or sec.structured_explanation):
        return sec
    try:
        return _parse_synthesis_json_fallback(raw, inp)
    except Exception:
        raise ValueError("could not parse answer synthesis output (sections or JSON)") from None


def synthesize_answer(
    exploration: FinalExplorationSchema,
    *,
    langfuse_parent: Any = None,
) -> AnswerSynthesisResult:
    """
    Build :class:`AnswerSynthesisInput`, render prompts, call reasoning model, parse output.

    ``langfuse_parent`` is reserved for future tracing; unused in V1.
    """
    _ = langfuse_parent
    if not ENABLE_ANSWER_SYNTHESIS:
        return AnswerSynthesisResult(
            synthesis_success=False,
            error="answer_synthesis_disabled",
            coverage=derive_answer_synthesis_coverage(exploration),
        )

    inp = AnswerSynthesisInput.from_exploration(exploration)
    variables = _build_prompt_variables(inp)
    model_display = get_prompt_model_name_for_task(ANSWER_SYNTHESIS_TASK)
    system_prompt, user_prompt = get_registry().render_prompt_parts(
        PROMPT_REGISTRY_KEY,
        version="latest",
        variables=variables,
        model_name=model_display,
    )
    if not user_prompt.strip():
        return AnswerSynthesisResult(
            synthesis_success=False,
            error="empty_user_prompt",
            coverage=inp.coverage,
        )

    n_sel = len(_select_evidence_rows(inp.evidence))
    _LOG.info(
        "answer_synthesis start coverage=%s system_confidence=%s evidence_rows=%s (cap=%s)",
        inp.coverage,
        inp.confidence,
        n_sel,
        ANSWER_SYNTHESIS_MAX_EVIDENCE_ITEMS,
    )
    try:
        raw = call_reasoning_model(
            user_prompt,
            system_prompt=system_prompt if system_prompt.strip() else None,
            task_name=ANSWER_SYNTHESIS_TASK,
            prompt_name=None,
        )
        result = _parse_model_output(raw, inp)
        _LOG.info("answer_synthesis ok direct_answer_chars=%s", len(result.direct_answer))
        return result
    except Exception as ex:
        _LOG.warning("answer_synthesis failed: %s", ex, exc_info=True)
        return AnswerSynthesisResult(
            synthesis_success=False,
            error=str(ex)[:2000],
            coverage=inp.coverage,
        )


def maybe_synthesize_to_state(state: Any, exploration: FinalExplorationSchema, langfuse_trace: Any = None) -> None:
    """
    When enabled, store synthesis on ``state.context`` as ``answer_synthesis`` (JSON dict)
    and ``final_answer`` (direct_answer string when successful).
    """
    if not ENABLE_ANSWER_SYNTHESIS:
        return
    cfg = get_config()
    if cfg.chat_planning.skip_answer_synthesis_when_sufficient:
        if normalize_understanding(exploration) == "sufficient":
            return
    ctx = getattr(state, "context", None)
    if not isinstance(ctx, dict):
        return
    result = synthesize_answer(exploration, langfuse_parent=langfuse_trace)
    ctx["answer_synthesis"] = result.model_dump(mode="json")
    if result.synthesis_success and (result.direct_answer or "").strip():
        ctx["final_answer"] = result.direct_answer.strip()
