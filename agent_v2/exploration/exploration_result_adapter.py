"""
Single mapping path: WorkingMemory → ExplorationItem / FinalExplorationSchema.

See: Docs/architecture_freeze/EXPLORATION_RESULT_ADAPTER_HYBRID.md
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from agent_v2.config import EXPLORATION_MAX_ITEMS
from agent_v2.exploration.exploration_working_memory import ExplorationWorkingMemory
from agent_v2.schemas.exploration import (
    ExplorationContent,
    ExplorationItem,
    ExplorationItemMetadata,
    ExplorationRelevance,
    ExplorationResult,
    ExplorationResultMetadata,
    ExplorationSource,
    ExplorationSummary,
    read_query_intent_from_agent_state,
)
from agent_v2.schemas.final_exploration import (
    ExplorationAdapterTrace,
    ExplorationConfidenceBand,
    ExplorationRelationshipEdge,
    FinalExplorationSchema,
)

_LOG = logging.getLogger(__name__)

ADAPTER_VERSION = "v1"

_LOW_TERMINATION: frozenset[str] = frozenset(
    {
        "no_relevant_candidate",
        "stalled",
        "policy_violation_full_read",
    }
)


def source_summary_from_items(items: list[ExplorationItem]) -> dict[str, int]:
    """Structural counts for metadata.source_summary (symbol/line/head reads)."""
    counts = {"symbol": 0, "line": 0, "head": 0}
    for it in items:
        rs = getattr(it, "read_source", None)
        if rs in counts:
            counts[rs] += 1
    return counts


def project_discrete_confidence(
    *,
    completion_status: str,
    termination_reason: str,
    n_items: int,
) -> ExplorationConfidenceBand:
    """
    Deterministic band from engine signals (not a float heuristic).

    - high: run marked complete with at least one evidence item.
    - low: no items, or termination indicates failure / abort.
    - medium: otherwise (e.g. incomplete but grounded).
    """
    if completion_status == "complete" and n_items >= 1:
        return "high"
    if n_items == 0 or termination_reason in _LOW_TERMINATION:
        return "low"
    return "medium"


def _relationship_edges_from_snapshot(rels: list[dict[str, Any]]) -> list[ExplorationRelationshipEdge]:
    out: list[ExplorationRelationshipEdge] = []
    for row in rels:
        if not isinstance(row, dict):
            continue
        fk = str(row.get("from") or "").strip()
        tk = str(row.get("to") or "").strip()
        rt = row.get("type")
        if not fk or not tk or rt not in ("callers", "callees", "related"):
            continue
        conf = float(row.get("confidence") or 0.85)
        src = str(row.get("source") or "expansion")
        out.append(
            ExplorationRelationshipEdge(
                from_key=fk,
                to_key=tk,
                type=rt,
                confidence=max(0.0, min(1.0, conf)),
                source=src,
            )
        )
    return out


def _items_from_memory_snapshot(
    evs: list[dict[str, Any]],
    *,
    max_items: int,
    max_snippet_chars: int,
) -> list[ExplorationItem]:
    items: list[ExplorationItem] = []
    ts = datetime.now(timezone.utc).isoformat()
    for idx, ev in enumerate(evs[:max_items], start=1):
        ref = str(ev.get("file") or "unknown")
        summary = str(ev.get("summary") or "")[:600]
        if not summary.strip():
            summary = "evidence recorded"
        key_points = [summary]
        score = 0.8 if float(ev.get("confidence") or 0.0) >= 0.5 else 0.4
        rs = ev.get("read_source")
        if rs not in ("symbol", "line", "head"):
            rs = None
        item_type = "file" if rs else "search"
        snippet = str(ev.get("snippet") or "")[:max_snippet_chars]
        tool_name = str(ev.get("tool_name") or "read_snippet")
        items.append(
            ExplorationItem(
                item_id=f"item_{idx}",
                type=item_type,
                source=ExplorationSource(ref=ref, location=None),
                content=ExplorationContent(
                    summary=summary,
                    key_points=key_points,
                    entities=[ref],
                ),
                relevance=ExplorationRelevance(
                    score=score,
                    reason=f"{str(ev.get('source') or 'evidence')} ok",
                ),
                metadata=ExplorationItemMetadata(
                    timestamp=ts,
                    tool_name=tool_name,
                ),
                snippet=snippet,
                read_source=rs,
            )
        )
    return items


def _exploration_summary_for_schema4(
    items: list[ExplorationItem],
    rel_edges: list[ExplorationRelationshipEdge],
    gap_dicts: list[dict[str, Any]],
) -> ExplorationSummary:
    rels = rel_edges  # only for key_findings blurb (counts), not for structured field
    n_rel = len(rels)
    key_findings = [it.content.summary for it in items[:3]]
    if n_rel and len(key_findings) < 3:
        key_findings.append(
            f"Recorded {n_rel} relationship edge(s) (callers/callees/related)."
        )
    gap_descriptions = [str(g.get("description") or "") for g in gap_dicts if str(g.get("description") or "").strip()]
    if gap_descriptions:
        knowledge_gaps = gap_descriptions[:6]
        kg_er: str | None = None
    else:
        knowledge_gaps = []
        kg_er = (
            "No additional knowledge gaps were recorded by the analyzer."
            if items
            else "No candidates discovered from instruction intent."
        )
    overall = f"Exploration v2 gathered {len(items)} evidence item(s) for instruction."
    if n_rel:
        overall += f" {n_rel} relationship edge(s)."
    return ExplorationSummary(
        overall=overall,
        key_findings=key_findings[:6],
        knowledge_gaps=knowledge_gaps,
        knowledge_gaps_empty_reason=kg_er,
    )


class ExplorationResultAdapter:
    """Sole owner of memory → FinalExplorationSchema (deterministic core)."""

    @staticmethod
    def build(
        memory: ExplorationWorkingMemory,
        instruction: str,
        *,
        completion_status: str,
        termination_reason: str,
        explored_files: int,
        explored_symbols: int,
        max_items: int = EXPLORATION_MAX_ITEMS,
        max_snippet_chars: int = 600,
        state: Any = None,
        engine_loop_steps: int = 0,
    ) -> FinalExplorationSchema:
        _LOG.debug("[ExplorationResultAdapter.build]")
        snap = memory.get_summary()
        evs = snap.get("evidence") or []
        rel_dicts = snap.get("relationships") or []
        gap_dicts = snap.get("gaps") or []

        items = _items_from_memory_snapshot(
            evs,
            max_items=max_items,
            max_snippet_chars=max_snippet_chars,
        )
        rel_edges = _relationship_edges_from_snapshot(rel_dicts)
        summary = _exploration_summary_for_schema4(items, rel_edges, gap_dicts)

        exploration_id = f"exp_{uuid.uuid4().hex[:8]}"
        created = datetime.now(timezone.utc).isoformat()
        meta = ExplorationResultMetadata(
            total_items=len(items),
            created_at=created,
            completion_status=("complete" if completion_status == "complete" else "incomplete"),
            termination_reason=termination_reason,
            explored_files=explored_files,
            explored_symbols=explored_symbols,
            engine_loop_steps=max(0, int(engine_loop_steps)),
            source_summary=source_summary_from_items(items),
        )
        band = project_discrete_confidence(
            completion_status=completion_status,
            termination_reason=termination_reason,
            n_items=len(items),
        )
        trace = ExplorationAdapterTrace(llm_used=False, synthesis_success=False, adapter_version=ADAPTER_VERSION)
        key_insights = list(summary.key_findings)[:4]
        qi_mirror = read_query_intent_from_agent_state(state) if state is not None else None

        return FinalExplorationSchema(
            exploration_id=exploration_id,
            instruction=instruction,
            status="complete" if completion_status == "complete" else "incomplete",
            evidence=items,
            relationships=rel_edges,
            exploration_summary=summary,
            metadata=meta,
            key_insights=key_insights,
            objective_coverage=None,
            confidence=band,
            trace=trace,
            query_intent=qi_mirror,
        )


def final_from_legacy_phase3_exploration_result(result: ExplorationResult) -> FinalExplorationSchema:
    """
    Wrap the pre-V2 runner bundle into the planner contract.

    ``ExplorationResult`` is only used inside ``agent_v2/exploration`` and legacy runner glue.
    """
    cs = result.metadata.completion_status or "incomplete"
    tr = result.metadata.termination_reason or "unknown"
    trace = ExplorationAdapterTrace(llm_used=False, synthesis_success=False, adapter_version=ADAPTER_VERSION)
    return FinalExplorationSchema(
        exploration_id=result.exploration_id,
        instruction=result.instruction,
        status="complete" if cs == "complete" else "incomplete",
        evidence=result.items,
        relationships=[],
        exploration_summary=result.summary,
        metadata=result.metadata,
        key_insights=list(result.summary.key_findings)[:4],
        objective_coverage=None,
        confidence=project_discrete_confidence(
            completion_status=cs,
            termination_reason=tr,
            n_items=len(result.items),
        ),
        trace=trace,
    )
