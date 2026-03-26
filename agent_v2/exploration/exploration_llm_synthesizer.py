"""
Optional LLM layer: key_insights + objective_coverage only.

Input contract: memory.get_summary() order, cap after sort — see design doc.
"""

from __future__ import annotations

from typing import Any, Callable

from agent_v2.config import EXPLORATION_MAX_ITEMS
from agent_v2.observability.langfuse_helpers import (
    LANGFUSE_GENERATION_PROMPT_INPUT_MAX_CHARS,
    exploration_llm_call,
)
from agent_v2.exploration.exploration_result_adapter import ADAPTER_VERSION
from agent_v2.exploration.exploration_working_memory import ExplorationWorkingMemory
from agent_v2.schemas.final_exploration import ExplorationAdapterTrace, FinalExplorationSchema
from agent_v2.utils.json_extractor import JSONExtractor

_LLM_CAP_EVIDENCE = EXPLORATION_MAX_ITEMS
_SUMMARY_MAX_CHARS = 200
_REL_SAMPLE = 8
_GAP_CAP = 6
_PROMPT_REGISTRY_KEY_EXPLORATION_SYNTHESIS = "exploration.result_llm_synthesis"


def _compress_relationships_for_prompt(rel_dicts: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for i, row in enumerate(rel_dicts[:_REL_SAMPLE]):
        if not isinstance(row, dict):
            continue
        lines.append(
            f"{row.get('from')} --{row.get('type')}--> {row.get('to')}"
        )
    if len(rel_dicts) > _REL_SAMPLE:
        lines.append(f"... ({len(rel_dicts) - _REL_SAMPLE} more edge(s))")
    return "\n".join(lines) if lines else "(none)"


def _build_prompt(instruction: str, memory: ExplorationWorkingMemory) -> str:
    snap = memory.get_summary()
    evs = snap.get("evidence") or []
    capped = evs[:_LLM_CAP_EVIDENCE]
    ev_lines: list[str] = []
    for i, ev in enumerate(capped):
        if not isinstance(ev, dict):
            continue
        sym = (ev.get("symbol") or "") or ""
        fp = str(ev.get("file") or "")
        sm = str(ev.get("summary") or "")[:_SUMMARY_MAX_CHARS]
        ev_lines.append(f"{i + 1}. file={fp} symbol={sym} summary={sm}")
    rel_block = _compress_relationships_for_prompt(snap.get("relationships") or [])
    gaps = snap.get("gaps") or []
    gap_lines: list[str] = []
    for g in gaps[:_GAP_CAP]:
        if isinstance(g, dict):
            gap_lines.append(str(g.get("description") or "").strip())
    gap_text = "\n".join(f"- {x}" for x in gap_lines if x) or "(none)"

    return (
        "You synthesize exploration results. Reply with ONLY a JSON object, no markdown.\n"
        'Schema: {"key_insights": string[2..4], "objective_coverage": string or null}\n'
        "Rules: insights must be grounded in the evidence lines; no invented files or symbols; "
        "keep each insight under 400 characters; objective_coverage is one short paragraph or null.\n\n"
        f"Instruction:\n{instruction[:1500]}\n\n"
        f"Evidence (ordered, capped):\n" + "\n".join(ev_lines) + "\n\n"
        f"Relationships (from graph):\n{rel_block}\n\n"
        f"Knowledge gaps:\n{gap_text}\n"
    )


def _parse_synthesis(raw: str) -> tuple[list[str], str | None]:
    parsed = JSONExtractor.extract_final_json(raw)
    if not isinstance(parsed, dict):
        raise ValueError("synthesis JSON not an object")
    insights_raw = parsed.get("key_insights")
    if insights_raw is None:
        insights = []
    elif isinstance(insights_raw, list):
        insights = [str(x).strip() for x in insights_raw if str(x).strip()]
    elif isinstance(insights_raw, str):
        insights = [insights_raw.strip()] if insights_raw.strip() else []
    else:
        insights = []
    insights = insights[:4]
    for s in insights:
        if len(s) > 800:
            raise ValueError("insight too long")
    cov = parsed.get("objective_coverage")
    if cov is None:
        return insights, None
    cs = str(cov).strip()
    if not cs:
        return insights, None
    return insights, cs[:1200]


def apply_optional_llm_synthesis(
    final: FinalExplorationSchema,
    memory: ExplorationWorkingMemory,
    instruction: str,
    llm_generate: Callable[[str], str],
    *,
    lf_exploration_parent: Any = None,
    model_name: str | None = None,
) -> FinalExplorationSchema:
    """
    Returns a copy with key_insights / objective_coverage / trace updated.
    On any failure, returns the same factual fields with synthesis_success=False.
    """
    prompt = _build_prompt(instruction, memory)
    syn_span: Any = None
    if lf_exploration_parent is not None and hasattr(lf_exploration_parent, "span"):
        try:
            syn_span = lf_exploration_parent.span(
                "exploration.synthesis",
                input={"stage": "synthesis", "input_source": "adapter_output"},
            )
        except Exception:
            syn_span = None
    result: list[tuple[list[str], str | None]] = []

    def _invoke() -> str:
        return llm_generate(prompt)

    def _complete(raw: str) -> tuple[dict[str, Any], dict[str, Any]]:
        insights, coverage = _parse_synthesis(raw)
        if len(insights) < 2:
            raise ValueError("need at least 2 key_insights")
        result.append((insights, coverage))
        return (
            {
                "key_insights": insights,
                "objective_coverage": coverage,
                "raw_preview": raw[:LANGFUSE_GENERATION_PROMPT_INPUT_MAX_CHARS],
                "synthesis_success": True,
            },
            {"stage": "synthesis", "input_source": "adapter_output", "ok": True},
        )

    try:
        exploration_llm_call(
            syn_span,
            lf_exploration_parent,
            name="exploration.synthesis",
            prompt=prompt,
            prompt_registry_key=_PROMPT_REGISTRY_KEY_EXPLORATION_SYNTHESIS,
            invoke=_invoke,
            stage="synthesis",
            model_name=model_name,
            input_extra={"input_source": "adapter_output"},
            on_complete=_complete,
            failure_output_extra={"synthesis_success": False},
        )
        insights, coverage = result[0]
        trace = ExplorationAdapterTrace(
            llm_used=True,
            synthesis_success=True,
            adapter_version=ADAPTER_VERSION,
        )
        return final.model_copy(
            update={
                "key_insights": insights,
                "objective_coverage": coverage,
                "trace": trace,
            }
        )
    except Exception:
        trace = ExplorationAdapterTrace(
            llm_used=True,
            synthesis_success=False,
            adapter_version=ADAPTER_VERSION,
        )
        return final.model_copy(update={"trace": trace})
    finally:
        if syn_span is not None:
            try:
                syn_span.end()
            except Exception:
                pass
