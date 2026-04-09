from __future__ import annotations

import json
import logging
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable, Literal, cast
from pathlib import Path

from agent_v2.config import (
    ENABLE_EXPLORATION_RESULT_LLM_SYNTHESIS,
    ENABLE_SYMBOL_AWARE_EXPLORATION,
    EXPLORATION_SYMBOL_AWARE_LOG_PROGRESS,
    EXPLORATION_SYMBOL_RELATIONSHIPS_MAX_NAMES,
    DISCOVERY_QUERY_POOL_MAX_WORKERS,
    DISCOVERY_REGEX_CAP,
    DISCOVERY_SEARCH_BATCH_MAX_WORKERS,
    DISCOVERY_SYMBOL_CAP,
    DISCOVERY_TEXT_CAP,
    EXPLORATION_MAX_QUERY_RETRIES,
    EXPLORATION_EXPAND_MAX_DEPTH,
    EXPLORATION_EXPAND_MAX_NODES,
    EXPLORATION_MAX_BACKTRACKS,
    EXPLORATION_MAX_ITEMS,
    EXPLORATION_MAX_REFINE_CYCLES,
    EXPLORATION_MAX_STEPS,
    EXPLORATION_OUTLINE_TOP_K_FOR_SELECTOR,
    EXPLORATION_READ_WINDOW,
    EXPLORATION_SCOPER_K,
    EXPLORATION_SCOPER_SKIP_BELOW,
    EXPLORATION_SELECTOR_TOP_K,
    EXPLORATION_STAGNATION_STEPS,
    EXPLORATION_SYMBOL_GRAPH_CONTEXT_K,
    EXPLORATION_SYMBOL_RELATIONSHIPS_MAX_CHARS,
    EXPLORATION_RETRY_LOW_RELEVANCE_THRESHOLD,
    EXPLORATION_ROUTING_SIMPLE_MAX_LINES,
    EXPLORATION_ROUTING_COMPLEX_MAX_LINES,
    EXPLORATION_CONTEXT_MAX_TOTAL_LINES,
    EXPLORATION_CONTEXT_TOP_K_RANGES,
    EXPLORATION_DISCOVERY_POST_RERANK_TOP_K,
    EXPLORATION_DISCOVERY_PRERERANK_POOL_MAX,
    EXPLORATION_DISCOVERY_RERANK_ENABLED,
    EXPLORATION_DISCOVERY_RERANK_MIN_CANDIDATES,
    EXPLORATION_DISCOVERY_RERANK_USE_FUSION,
    EXPLORATION_DISCOVERY_SNIPPET_MERGE_MAX_CHARS,
    EXPLORATION_PENDING_EXPANSION_SYMBOLS_TOP_K,
    MAX_ANALYZER_CONTEXT_CHARS,
    MAX_ANALYZER_SYMBOL_CONTEXT_CHARS,
    exploration_symbol_graph_lookup_enabled,
    get_project_root,
)
from agent_v2.observability.langfuse_client import create_agent_trace, finalize_agent_trace
from agent_v2.observability.langfuse_helpers import lf_span_end_output
from agent_v2.exploration.candidate_selector import CandidateSelector
from agent_v2.exploration.context_block_builder import ContextBlockBuilder
from agent_v2.exploration.decision_mapper import EngineDecisionMapper
from agent_v2.exploration.exploration_scoper import ExplorationScoper
from agent_v2.exploration.fetcher import Fetcher
from agent_v2.exploration.graph_expander import GraphExpander
from agent_v2.exploration.inspector import Inspector
from agent_v2.exploration.inspection_reader import InspectionReader
from agent_v2.exploration.query_intent_parser import QueryIntentParser
from agent_v2.exploration.slice_grouper import SliceGrouper
from agent_v2.exploration.exploration_llm_synthesizer import apply_optional_llm_synthesis
from agent_v2.exploration.exploration_result_adapter import ExplorationResultAdapter
from agent_v2.exploration.exploration_working_memory import ExplorationWorkingMemory
from agent_v2.exploration.understanding_analyzer import UnderstandingAnalyzer
from agent_v2.exploration.selector_outline_injection import (
    ANALYZER_CODE_TRIM_NOTICE,
    build_bounded_symbol_context,
)
from agent_v2.schemas.execution import ExecutionResult
from agent_v2.schemas.final_exploration import FinalExplorationSchema
from agent_v2.schemas.exploration import (
    ContextBlock,
    ExplorationCandidate,
    ExplorationContent,
    ExplorationDecision,
    ExplorationItem,
    ExplorationItemMetadata,
    ExplorationRelevance,
    ExplorationResultMetadata,
    ExplorationSource,
    ExplorationState,
    ExplorationSummary,
    ExplorationTarget,
    ReadPacket,
    QueryIntent,
    SelectorBatchResult,
    UnderstandingResult,
    FailureReason,
    task_intent_summary_for_analyzer,
    write_query_intent_to_agent_state,
)

_LOG = logging.getLogger(__name__)

def _lf_end(span: Any) -> None:
    if span is None:
        return
    try:
        span.end()
    except Exception:
        pass


def _exploration_inspect_langfuse_output(
    snippet: str | None, inspect_result: ExecutionResult
) -> dict[str, Any]:
    """Rich span output for Langfuse (summary + bounded snippet lines + data preview)."""
    out: dict[str, Any] = {
        "tool": getattr(inspect_result.metadata, "tool_name", None),
        "success": inspect_result.success,
    }
    summary = None
    if inspect_result.output:
        summary = inspect_result.output.summary
    out["summary"] = summary
    text = (snippet or "").strip()
    if text:
        lines = text.splitlines()
        out["output_lines"] = lines[:80]
        out["output_line_count"] = len(lines)
        out["truncated"] = len(lines) > 80
    data = inspect_result.output.data if inspect_result.output else {}
    if isinstance(data, dict) and data:
        keys = list(data.keys())[:20]
        preview = {k: data[k] for k in keys}
        try:
            out["data_preview"] = json.dumps(preview, default=str)[:4000]
        except Exception:
            out["data_preview"] = str(preview)[:4000]
    return out


def _discovery_tool_langfuse_output(
    *,
    phase: str,
    intent: QueryIntent,
    candidates: list[ExplorationCandidate],
    records: list[tuple[str, dict, ExecutionResult]],
    discovery_ms: int,
) -> dict[str, Any]:
    """Structured Langfuse output for batched SEARCH (discovery) tool spans."""
    counts = {"symbol": 0, "regex": 0, "text": 0}
    for _k, payload, _res in records:
        qt = payload.get("query_type")
        if qt in counts:
            counts[qt] += 1
    successes = sum(1 for _k, _p, res in records if getattr(res, "success", False))
    return {
        "tool": "search",
        "batch": True,
        "phase": phase,
        "duration_ms": discovery_ms,
        "search_executions_by_channel": counts,
        "search_executions_total": len(records),
        "search_successes": successes,
        "candidates_after_may_enqueue": len(candidates),
    }


def _expand_tool_langfuse_output(
    expanded: list[ExplorationTarget],
    expand_result: ExecutionResult,
) -> dict[str, Any]:
    """Structured Langfuse output for graph/search expansion tool span."""
    md = getattr(expand_result, "metadata", None)
    tool = getattr(md, "tool_name", None) if md is not None else None
    summary = None
    if expand_result.output:
        summary = str(expand_result.output.summary or "")[:2000]
    return {
        "tool": tool or "expand",
        "success": expand_result.success,
        "summary": summary,
        "targets_count": len(expanded),
    }


def _emit_exploration_phase_events(exploration_span: Any, termination_reason: str) -> None:
    """Phase 12.6.G — events on ``exploration`` span (not root)."""
    if exploration_span is None or not hasattr(exploration_span, "event"):
        return
    mapping = {
        "no_relevant_candidate": "no_relevant_candidate",
        "pending_exhausted": "pending_exhausted",
        "primary_symbol_sufficient": "primary_symbol_sufficient",
    }
    name = mapping.get(termination_reason)
    if not name:
        return
    try:
        exploration_span.event(name=name, metadata={"termination_reason": termination_reason})
    except Exception:
        pass


class ExplorationEngineV2:
    """Deterministic staged exploration state machine."""

    MAX_SNIPPET_CHARS: int = 600  # Phase 12.6.E safety cap (deterministic, not heuristic)
    _GENERIC_GAP_MARKERS: tuple[str, ...] = (
        "more context",
        "need more context",
        "insufficient context",
        "missing details",
        "unclear",
        "unknown",
        "more code",
    )

    @classmethod
    def _classify_gap_category(cls, gap: str) -> str:
        """Deterministic gap category for directed expansion (substring rules)."""
        low = (gap or "").lower()
        if "callee" in low or "callees" in low:
            return "callee"
        if "caller" in low or "call site" in low or "who calls" in low:
            return "caller"
        if "defin" in low or "definition" in low or "where defined" in low or "locate" in low:
            return "definition"
        if "config" in low or "setting" in low or " env" in low or " flag" in low:
            return "config"
        if (
            "usage" in low
            or " used" in low
            or low.startswith("used ")
            or "reference" in low
            or "where used" in low
        ):
            return "usage"
        if "flow" in low or "sequence" in low or "pipeline" in low:
            return "flow"
        if cls._gap_contains_probable_symbol(low):
            return "usage_symbol_fallback"
        return "none"

    _SYMBOL_FALLBACK_STOP: frozenset[str] = frozenset(
        {
            "missing",
            "need",
            "more",
            "the",
            "for",
            "and",
            "chain",
            "path",
            "context",
            "details",
            "information",
            "insufficient",
            "unclear",
            "unknown",
            "some",
            "another",
            "code",
            "logic",
            "how",
            "what",
            "when",
            "where",
            "this",
            "that",
        }
    )

    @classmethod
    def _gap_contains_probable_symbol(cls, low: str) -> bool:
        for tok in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", low):
            if len(tok) >= 3 and tok.lower() not in cls._SYMBOL_FALLBACK_STOP:
                return True
        return False

    @classmethod
    def _extract_inject_keywords(cls, gap: str, category: str) -> list[str]:
        """1–2 keywords merged into discovery text channel (engine-local)."""
        out: list[str] = []
        low = (gap or "").lower()
        if category == "definition":
            out.append("definition")
        elif category == "config":
            out.append("config")
        elif category in ("usage", "usage_symbol_fallback"):
            out.append("usage")
        stop = frozenset(
            {
                "missing",
                "need",
                "more",
                "the",
                "for",
                "and",
                "chain",
                "path",
                "context",
                "details",
            }
        )
        for tok in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", gap or ""):
            tl = tok.lower()
            if len(tok) >= 2 and tl not in stop:
                out.append(tok)
        return list(dict.fromkeys(out))[:2]

    @staticmethod
    def _merge_expand_direction(
        prev: str | None, new: str | None
    ) -> str | None:
        if not new:
            return prev
        if not prev:
            return new
        if prev == new:
            return prev
        if prev in ("callers", "callees") and new in ("callers", "callees"):
            return "both"
        return new

    def __init__(
        self,
        *,
        dispatcher,
        intent_parser: QueryIntentParser,
        selector: CandidateSelector,
        inspection_reader: InspectionReader,
        analyzer: UnderstandingAnalyzer,
        graph_expander: GraphExpander,
        scoper: ExplorationScoper | None = None,
        inspector: Inspector | None = None,
        fetcher: Fetcher | None = None,
        context_block_builder: ContextBlockBuilder | None = None,
        decision_mapper: EngineDecisionMapper | None = None,
        slice_grouper: SliceGrouper | None = None,
        result_synthesis_llm: Callable[[str], str] | None = None,
        result_synthesis_model_name: str | None = None,
    ):
        self._dispatcher = dispatcher
        self._intent_parser = intent_parser
        self._selector = selector
        self._inspection_reader = inspection_reader
        self._analyzer = analyzer
        self._graph_expander = graph_expander
        self._scoper = scoper
        self._inspector = inspector or Inspector()
        self._fetcher = fetcher or Fetcher()
        self._context_block_builder = context_block_builder or ContextBlockBuilder()
        self._decision_mapper = decision_mapper or EngineDecisionMapper()
        self._slice_grouper = slice_grouper or SliceGrouper()
        self._result_synthesis_llm = result_synthesis_llm
        self._result_synthesis_model_name = result_synthesis_model_name
        self.last_working_memory: ExplorationWorkingMemory | None = None
        self.last_final_exploration: FinalExplorationSchema | None = None
        self._symbol_outline_cache: dict[str, list[dict[str, str]]] = {}

    def explore(
        self,
        instruction: str,
        *,
        state: Any,
        obs: Any = None,
        langfuse_trace: Any = None,
    ) -> FinalExplorationSchema:
        _LOG.debug("[ExplorationEngineV2.explore]")
        lf = langfuse_trace
        if lf is None and obs is not None:
            lf = getattr(obs, "langfuse_trace", None)
        fallback_lf: Any = None
        if lf is None:
            fallback_lf = create_agent_trace(
                instruction=instruction[:8000],
                mode="exploration",
                name=f"exploration_fallback_{uuid.uuid4().hex[:12]}",
            )
            lf = fallback_lf
        exploration_outer: Any = None
        if lf is not None and hasattr(lf, "span"):
            try:
                exploration_outer = lf.span("exploration", input={"instruction": instruction[:2000]})
                if obs is not None:
                    obs.exploration_parent_span = exploration_outer
            except Exception:
                exploration_outer = None

        self._last_termination_reason = "unknown"
        try:
            return self._explore_inner(
                instruction,
                state,
                obs,
                exploration_outer,
                lf,
            )
        finally:
            try:
                _emit_exploration_phase_events(
                    exploration_outer,
                    getattr(self, "_last_termination_reason", "unknown"),
                )
            except Exception:
                pass
            _lf_end(exploration_outer)
            if fallback_lf is not None:
                tid = getattr(fallback_lf, "trace_id", None)
                plan_id = f"explore_{tid}" if tid else "explore_local"
                finalize_agent_trace(fallback_lf, status="ok", plan_id=plan_id)
            if obs is not None:
                obs.exploration_parent_span = None
                obs.current_span = None

    def _explore_inner(
        self,
        instruction: str,
        state: Any,
        obs: Any,
        exploration_outer: Any,
        lf: Any,
    ) -> FinalExplorationSchema:
        _LOG.debug("[ExplorationEngineV2._explore_inner]")
        self._symbol_outline_cache = {}
        ex_state = ExplorationState(instruction=instruction)
        memory = ExplorationWorkingMemory(
            max_evidence=EXPLORATION_MAX_ITEMS,
            max_gaps=6,
            max_relationships=48,
        )
        evidence: list[tuple[str, dict, ExecutionResult]] = []
        termination_reason = "unknown"
        primary_symbol_body_seen = False  # SYSTEM ONLY: fact that we read the symbol body (bounded)
        # System-level evidence delta: (canonical_path, symbol, read_source) — no scoring, identity only
        evidence_keys_seen: set[tuple[str, str, str]] = set()
        stagnation_counter = 0

        intent_span: Any = None
        if exploration_outer is not None and hasattr(exploration_outer, "span"):
            try:
                intent_span = exploration_outer.span(
                    "exploration.query_intent",
                    input={
                        "instruction": instruction[:2000],
                        "instruction_chars": len(instruction),
                    },
                )
            except Exception:
                intent_span = None
        try:
            intent = self._intent_parser.parse(
                instruction,
                lf_exploration_parent=exploration_outer,
                lf_intent_span=intent_span,
            )
        finally:
            _lf_end(intent_span)
        write_query_intent_to_agent_state(state, intent)

        candidates, discovery_records, discovery_ms = self._run_discovery_traced(
            exploration_outer,
            "initial",
            intent,
            state,
            ex_state,
            refine_phase=False,
        )
        evidence.extend(discovery_records)
        memory.ingest_discovery_candidates(candidates, limit=EXPLORATION_MAX_ITEMS)
        t_enq0 = time.perf_counter()
        selection_none, _ = self._enqueue_ranked(
            instruction,
            intent,
            candidates,
            ex_state,
            limit=5,
            expl_parent=exploration_outer,
            obs=obs,
        )
        initial_enqueue_ms = int((time.perf_counter() - t_enq0) * 1000)
        if exploration_outer is not None:
            try:
                srcs = sorted({str(getattr(c, "source", "") or "unknown") for c in candidates})
                exploration_outer.update(
                    metadata={
                        "candidate_count": len(candidates),
                        "sources": srcs,
                        "discovery_ms": discovery_ms,
                        "initial_enqueue_ms": initial_enqueue_ms,
                    }
                )
            except Exception:
                pass

        # Intent bootstrap: re-parse + discovery when the work queue is still empty and selector
        # coverage is weak/empty/fragmented. This is NOT mapper REFINE (no EngineDecisionMapper);
        # refine_count is unchanged.
        while (
            not ex_state.pending_targets
            and ex_state.last_selector_batch is not None
            and ex_state.last_selector_batch.coverage_signal in ("empty", "weak", "fragmented")
            and ex_state.intent_bootstrap_pass_count < EXPLORATION_MAX_REFINE_CYCLES
        ):
            ex_state.intent_bootstrap_pass_count += 1
            memory_summary = memory.get_summary()
            memory_evidence = memory_summary.get("evidence") or []
            memory_symbols = sorted(
                {
                    str(row.get("symbol") or "").strip()
                    for row in memory_evidence
                    if isinstance(row, dict) and str(row.get("symbol") or "").strip()
                }
            )
            memory_files = sorted(
                {
                    str(row.get("file") or "").strip()
                    for row in memory_evidence
                    if isinstance(row, dict) and str(row.get("file") or "").strip()
                }
            )
            context_feedback = {
                "partial_findings": memory_evidence,
                "known_entities": {
                    "symbols": sorted(
                        set(memory_symbols) | {s for s in ex_state.seen_symbols if str(s).strip()}
                    ),
                    "files": sorted(
                        set(memory_files) | {f for f in ex_state.seen_files if str(f).strip()}
                    ),
                },
                "knowledge_gaps": memory_summary.get("gaps") or [],
                "relationships": memory_summary.get("relationships") or [],
            }
            self._log_exploration_context_feedback_trace(
                "intent_bootstrap_pass",
                context_feedback=context_feedback,
                ex_state=ex_state,
                failure_reason="low_relevance",
                extra={"refine_phase": True, "intent_bootstrap_pass": True},
                exploration_outer=exploration_outer,
            )
            intent = self._intent_parser.parse(
                instruction,
                previous_queries=intent,
                failure_reason="low_relevance",
                context_feedback=context_feedback,
                refine_context=memory_summary,
                lf_exploration_parent=exploration_outer,
            )
            write_query_intent_to_agent_state(state, intent)
            candidates, discovery_records, _ = self._run_discovery_traced(
                exploration_outer,
                "intent_bootstrap_pass",
                intent,
                state,
                ex_state,
                refine_phase=True,
            )
            evidence.extend(discovery_records)
            memory.ingest_discovery_candidates(candidates, limit=EXPLORATION_MAX_ITEMS)
            selection_none, _ = self._enqueue_ranked(
                instruction,
                intent,
                candidates,
                ex_state,
                limit=3,
                expl_parent=exploration_outer,
                obs=obs,
            )
            if not selection_none:
                break

        if selection_none and not ex_state.pending_targets:
            termination_reason = "no_relevant_candidate"

        while ex_state.steps_taken < EXPLORATION_MAX_STEPS:
            if termination_reason == "no_relevant_candidate":
                break
            if not ex_state.pending_targets:
                termination_reason = "pending_exhausted"
                break
            target = ex_state.pending_targets.pop(0)
            ex_state.current_target = target
            key = self._make_location_key(target.file_path, target.symbol)
            canon = key[0]
            if key in ex_state.explored_location_keys:
                stagnation_counter += 1
                if stagnation_counter >= EXPLORATION_STAGNATION_STEPS:
                    termination_reason = "stalled"
                    break
                continue
            ex_state.explored_location_keys.add(key)
            stagnation_counter = 0  # new (file_path, symbol) visit — not stalled on duplicate-queue skips

            pre_stop, pre_stop_reason = self._should_stop_pre(ex_state)
            if pre_stop:
                termination_reason = pre_stop_reason
                break

            ex_state.steps_taken += 1

            if ENABLE_SYMBOL_AWARE_EXPLORATION and EXPLORATION_SYMBOL_AWARE_LOG_PROGRESS:
                _LOG.info(
                    "exploration.symbol_aware inspect step=%s/%s file=%s read_mode=%s source=%s",
                    ex_state.steps_taken,
                    EXPLORATION_MAX_STEPS,
                    Path(str(target.file_path)).name or target.file_path,
                    "symbol" if target.symbol else "file_only",
                    target.source,
                )

            selected = ExplorationCandidate(
                symbol=target.symbol,
                file_path=target.file_path,
                snippet=None,
                source="graph" if target.source == "expansion" else "grep",
            )
            inspect_span: Any = None
            inspect_result: ExecutionResult | None = None
            read_packet = ReadPacket(file_path=target.file_path, symbol=target.symbol)
            if exploration_outer is not None and hasattr(exploration_outer, "span"):
                try:
                    inspect_span = exploration_outer.span(
                        "exploration.inspect",
                        input={
                            "file_path": target.file_path,
                            "symbol": (target.symbol or "")[:500],
                        },
                    )
                    if obs is not None:
                        obs.current_span = inspect_span
                except Exception:
                    inspect_span = None
            try:
                read_packet, inspect_result = self._inspection_reader.inspect_packet(
                    selected,
                    symbol=target.symbol,
                    line=target.line,
                    window=EXPLORATION_READ_WINDOW,
                    state=state,
                )
            finally:
                if obs is not None:
                    obs.current_span = None
                if inspect_span is not None and inspect_result is not None:
                    try:
                        inspect_span.end(
                            output=_exploration_inspect_langfuse_output(read_packet.content, inspect_result)
                        )
                    except Exception:
                        _lf_end(inspect_span)
                elif inspect_span is not None:
                    _lf_end(inspect_span)
            # Phase 12.6.D enforcement: exploration inspection must use bounded read tool.
            if str(getattr(inspect_result.metadata, "tool_name", "") or "") != "read_snippet":
                evidence.append(("inspection", {"path": target.file_path}, inspect_result))
                termination_reason = "policy_violation_full_read"
                break
            ex_state.seen_files.add(canon)
            if target.symbol:
                ex_state.seen_symbols.add(target.symbol)
                if ex_state.primary_symbol is None:
                    ex_state.primary_symbol = target.symbol
                if ex_state.primary_symbol == target.symbol:
                    data = inspect_result.output.data if inspect_result.output else {}
                    if isinstance(data, dict) and str(data.get("mode") or "") == "symbol_body":
                        primary_symbol_body_seen = True
            evidence.append(("inspection", {"path": target.file_path}, inspect_result))

            data = inspect_result.output.data if inspect_result.output else {}
            evidence_key = self._evidence_delta_key(canon, target, data)
            meaningful = self._is_meaningful_new_evidence(evidence_keys_seen, evidence_key)

            post_inspect_stop, post_inspect_reason = self._should_stop_pre(ex_state)
            if post_inspect_stop:
                termination_reason = post_inspect_reason
                break

            if meaningful:
                evidence_keys_seen.add(evidence_key)
                stagnation_counter = 0
                analyze_span: Any = None
                if exploration_outer is not None and hasattr(exploration_outer, "span"):
                    try:
                        analyze_span = exploration_outer.span(
                            "exploration.analyze",
                            input={"file_path": target.file_path},
                        )
                        if obs is not None:
                            obs.current_span = analyze_span
                    except Exception:
                        analyze_span = None
                understanding: UnderstandingResult | None = None
                try:
                    read_blocks, routing_meta = self._build_context_blocks_for_analysis(
                        intent,
                        [read_packet],
                    )
                    symbol_budget = min(MAX_ANALYZER_SYMBOL_CONTEXT_CHARS, MAX_ANALYZER_CONTEXT_CHARS)
                    read_budget = max(0, MAX_ANALYZER_CONTEXT_CHARS - symbol_budget)
                    read_blocks_bounded = self._bound_context_blocks_by_chars(
                        read_blocks,
                        max_chars=read_budget,
                    )
                    selector_symbol_blocks = self._build_analyzer_symbol_context_blocks(
                        target,
                        max_chars=symbol_budget,
                    )
                    # Analyzer currently consumes at most 6 context blocks; reserve one slot
                    # for symbol expansion block when present so selector symbols are not dropped.
                    if selector_symbol_blocks and len(read_blocks_bounded) > 5:
                        read_blocks_bounded = read_blocks_bounded[:5]
                    context_blocks = read_blocks_bounded + selector_symbol_blocks
                    context_blocks = self._bound_context_blocks_by_chars(
                        context_blocks,
                        max_chars=MAX_ANALYZER_CONTEXT_CHARS,
                    )
                    if exploration_outer is not None and hasattr(exploration_outer, "event"):
                        try:
                            exploration_outer.event(
                                name="exploration.routing",
                                metadata={
                                    **routing_meta,
                                    "read_context_blocks": len(read_blocks_bounded),
                                    "selector_symbol_context_blocks": len(selector_symbol_blocks),
                                    "analyzer_context_blocks": len(context_blocks),
                                    "max_analyzer_context_chars": MAX_ANALYZER_CONTEXT_CHARS,
                                    "max_analyzer_symbol_context_chars": symbol_budget,
                                },
                            )
                        except Exception:
                            pass
                    rel_block = self._symbol_relationships_block_for_target(target, state)
                    if ENABLE_SYMBOL_AWARE_EXPLORATION and EXPLORATION_SYMBOL_AWARE_LOG_PROGRESS:
                        _LOG.info(
                            "exploration.symbol_aware analyze relationships_chars=%s file=%s symbol=%s",
                            len(rel_block),
                            Path(str(target.file_path)).name or target.file_path,
                            target.symbol or "",
                        )
                    upstream_sel_conf: str | None = None
                    sb = getattr(target, "selector_batch", None)
                    if sb is not None and getattr(sb, "selection_confidence", None):
                        upstream_sel_conf = str(sb.selection_confidence)
                    understanding = self._analyzer.analyze(
                        instruction,
                        intent=", ".join([s for s in (intent.intents or []) if str(s).strip()])
                        or "no retrieval intents",
                        context_blocks=context_blocks,
                        task_intent_summary=task_intent_summary_for_analyzer(intent, instruction),
                        symbol_relationships_block=rel_block,
                        upstream_selection_confidence=upstream_sel_conf,
                        lf_analyze_span=analyze_span,
                        lf_exploration_parent=exploration_outer,
                    )
                    decision = self._decision_mapper.to_exploration_decision(understanding)
                    snippet_mem, read_src_mem = self._item_snippet_and_source(
                        "inspection", inspect_result
                    )
                    inspect_summary = ""
                    if inspect_result.output:
                        inspect_summary = str(inspect_result.output.summary or "").strip()
                    analyzer_summary = str(understanding.summary or "").strip()
                    summary_text = analyzer_summary or inspect_summary or "Context analyzed."
                    _conf = max(float(understanding.confidence), memory.min_confidence)
                    memory.add_evidence(
                        target.symbol,
                        canon,
                        (int(read_packet.line_start), int(read_packet.line_end)),
                        summary_text,
                        snippet=snippet_mem or None,
                        read_source=read_src_mem,
                        confidence=_conf,
                        source="analyzer",
                        tier=0,
                        tool_name="read_snippet",
                    )
                    for gap in understanding.knowledge_gaps or []:
                        gs = str(gap or "").strip()
                        if not gs:
                            continue
                        memory.add_gap(
                            self._classify_gap_category(gs),
                            gs,
                            confidence=_conf,
                            source="analyzer",
                        )
                finally:
                    if obs is not None:
                        obs.current_span = None
                    _lf_end(analyze_span)
                if understanding is None:
                    continue
                ex_state.last_decision = decision.status
                if decision.wrong_target_scope == "file":
                    ex_state.excluded_paths.add(canon)

                stop, stop_reason = self._should_stop(ex_state, decision)
                if stop:
                    termination_reason = stop_reason
                    break

                sel_batch = target.selector_batch or SelectorBatchResult(
                    selected_candidates=[],
                    selection_confidence="medium",
                    coverage_signal="unknown",
                )
                merged_symbols = self._merge_expansion_symbols(
                    understanding.required_symbols,
                    sel_batch.expanded_symbols,
                    cap=EXPLORATION_PENDING_EXPANSION_SYMBOLS_TOP_K,
                )
                ex_state.pending_expansion_symbols = merged_symbols
                for sym in merged_symbols:
                    if sym in (understanding.required_symbols or []):
                        ex_state.missing_symbols.add(sym)
                if exploration_outer is not None and hasattr(exploration_outer, "event"):
                    try:
                        exploration_outer.event(
                            name="exploration.signal_flow",
                            metadata={
                                "selector_expanded_symbols": list(sel_batch.expanded_symbols),
                                "analyzer_required_symbols": list(understanding.required_symbols),
                                "final_expansion_symbols": list(merged_symbols),
                            },
                        )
                    except Exception:
                        pass
                control = EngineDecisionMapper.decide_control(
                    understanding,
                    sel_batch,
                    intent.relationship_hint,
                    expanded_once=ex_state.expanded_once,
                    refine_count=ex_state.refine_count,
                    refine_limit=EXPLORATION_MAX_REFINE_CYCLES,
                )
                if control.action == "stop":
                    termination_reason = (
                        "analyzer_sufficient"
                        if control.reason == "analyzer_sufficient"
                        else "mapper_default_stop"
                    )
                    break

                if control.action == "expand":
                    rh = intent.relationship_hint
                    if rh in ("callers", "callees", "both"):
                        ex_state.expand_direction_hint = rh
                    if ex_state.expansion_depth >= EXPLORATION_EXPAND_MAX_DEPTH:
                        continue
                    expand_symbol = ""
                    for sym in list(ex_state.pending_expansion_symbols):
                        s = str(sym).strip()
                        if not s or s in ex_state.expanded_symbols:
                            continue
                        expand_symbol = s
                        break
                    if not expand_symbol and target.symbol and target.symbol not in ex_state.expanded_symbols:
                        expand_symbol = target.symbol
                    if not expand_symbol:
                        continue
                    ex_state.expanded_symbols.add(expand_symbol)
                    expand_span: Any = None
                    if exploration_outer is not None and hasattr(exploration_outer, "span"):
                        try:
                            expand_span = exploration_outer.span(
                                "exploration.expand",
                                input={
                                    "symbol": (target.symbol or "")[:500],
                                "expand_symbol": expand_symbol[:500],
                                    "file_path": str(target.file_path)[:1000],
                                },
                            )
                        except Exception:
                            expand_span = None
                    dir_hint = getattr(ex_state, "expand_direction_hint", None)
                    skip_files, skip_symbols = self._expand_skip_sets(ex_state)
                    try:
                        expanded, expand_result = self._graph_expander.expand(
                            expand_symbol,
                            target.file_path,
                            state,
                            max_nodes=EXPLORATION_EXPAND_MAX_NODES,
                            max_depth=EXPLORATION_EXPAND_MAX_DEPTH,
                            direction_hint=dir_hint,
                            skip_files=skip_files,
                            skip_symbols=skip_symbols,
                        )
                    except Exception as exc:
                        lf_span_end_output(
                            expand_span,
                            output={"tool": "expand", "error": str(exc)[:2000]},
                        )
                        raise
                    gk = (getattr(ex_state, "gap_bundle_key_for_expansion", None) or "").strip().lower()
                    expanded = self._prefilter_expansion_targets(ex_state, expanded, gk)
                    relation_bucket_by_key: dict[
                        tuple[str, str], Literal["primary", "related", "other"]
                    ] = {}
                    ex_data = expand_result.output.data if expand_result.output else {}
                    if isinstance(ex_data, dict):
                        expanded, relation_bucket_by_key = self._enforce_direction_routing(
                            expanded,
                            ex_data,
                            ex_state,
                            direction_hint=dir_hint,
                        )
                    lf_span_end_output(
                        expand_span,
                        output=_expand_tool_langfuse_output(expanded, expand_result),
                    )
                    evidence.append(("expansion", {"symbol": expand_symbol}, expand_result))
                    ex_tool = getattr(getattr(expand_result, "metadata", None), "tool_name", None)
                    ex_summary = ""
                    if expand_result.output:
                        ex_summary = str(expand_result.output.summary or "").strip()
                    memory.add_expansion_evidence_row(
                        canon,
                        expand_symbol,
                        ex_summary,
                        success=bool(expand_result.success),
                        tool_name=str(ex_tool or "graph_lookup"),
                    )
                    if isinstance(ex_data, dict):
                        memory.add_relationships_from_expand(canon, expand_symbol, ex_data)
                    if expanded:
                        ex_state.relationships_found = True
                        self._enqueue_targets(
                            ex_state,
                            expanded,
                            relation_bucket_by_key=relation_bucket_by_key,
                        )
                        ex_state.expansion_depth += 1
                        ex_state.expanded_once = True
                    ex_state.expand_direction_hint = None
                    ex_state.gap_bundle_key_for_expansion = ""
                    continue

                if control.action == "refine":
                    if ex_state.backtracks >= EXPLORATION_MAX_BACKTRACKS:
                        _LOG.info(
                            "exploration.control event=refine_blocked_by_backtrack_limit "
                            "backtracks=%s max=%s steps_taken=%s",
                            ex_state.backtracks,
                            EXPLORATION_MAX_BACKTRACKS,
                            ex_state.steps_taken,
                        )
                        if exploration_outer is not None and hasattr(
                            exploration_outer, "event"
                        ):
                            try:
                                exploration_outer.event(
                                    name="refine_blocked_by_backtrack_limit",
                                    metadata={
                                        "backtracks": ex_state.backtracks,
                                        "max_backtracks": EXPLORATION_MAX_BACKTRACKS,
                                        "steps_taken": ex_state.steps_taken,
                                    },
                                )
                            except Exception:
                                pass
                        continue
                    ex_state.refine_count += 1
                    ex_state.backtracks += 1
                    memory_summary = memory.get_summary()
                    memory_evidence = memory_summary.get("evidence") or []
                    memory_symbols = sorted(
                        {
                            str(row.get("symbol") or "").strip()
                            for row in memory_evidence
                            if isinstance(row, dict) and str(row.get("symbol") or "").strip()
                        }
                    )
                    memory_files = sorted(
                        {
                            str(row.get("file") or "").strip()
                            for row in memory_evidence
                            if isinstance(row, dict) and str(row.get("file") or "").strip()
                        }
                    )
                    context_feedback = {
                        "partial_findings": memory_evidence,
                        "known_entities": {
                            "symbols": sorted(
                                set(memory_symbols)
                                | {s for s in ex_state.seen_symbols if str(s).strip()}
                            ),
                            "files": sorted(
                                set(memory_files)
                                | {f for f in ex_state.seen_files if str(f).strip()}
                            ),
                        },
                        "knowledge_gaps": memory_summary.get("gaps") or [],
                        "relationships": memory_summary.get("relationships") or [],
                    }
                    self._log_exploration_context_feedback_trace(
                        "loop_refine",
                        context_feedback=context_feedback,
                        ex_state=ex_state,
                        failure_reason=str(control.reason),
                        extra={
                            "target_file": str(target.file_path)[:1000],
                            "target_symbol": (target.symbol or "")[:500],
                        },
                        exploration_outer=exploration_outer,
                    )
                    refined_intent = self._intent_parser.parse(
                        instruction,
                        previous_queries=intent,
                        failure_reason=control.reason,
                        context_feedback=context_feedback,
                        refine_context=memory_summary,
                        lf_exploration_parent=exploration_outer,
                    )
                    intent = refined_intent
                    write_query_intent_to_agent_state(state, intent)
                    candidates, discovery_records, _ = self._run_discovery_traced(
                        exploration_outer,
                        "refine",
                        intent,
                        state,
                        ex_state,
                        refine_phase=True,
                    )
                    evidence.extend(discovery_records)
                    memory.ingest_discovery_candidates(candidates, limit=EXPLORATION_MAX_ITEMS)
                    selection_none, _ = self._enqueue_ranked(
                        instruction,
                        intent,
                        candidates,
                        ex_state,
                        limit=3,
                        expl_parent=exploration_outer,
                        obs=obs,
                    )
                    if selection_none and not ex_state.pending_targets:
                        termination_reason = "no_relevant_candidate"
                        break
                    continue

            else:
                snippet_mem, read_src_mem = self._item_snippet_and_source(
                    "inspection", inspect_result
                )
                inspect_summary = ""
                if inspect_result.output:
                    inspect_summary = str(inspect_result.output.summary or "").strip()
                memory.add_evidence(
                    target.symbol,
                    canon,
                    (int(read_packet.line_start), int(read_packet.line_end)),
                    inspect_summary or "Inspection read; duplicate evidence key — analyzer skipped.",
                    snippet=snippet_mem or None,
                    read_source=read_src_mem,
                    confidence=max(memory.min_confidence, 0.35),
                    source="inspection",
                    tier=0,
                    tool_name="read_snippet",
                )
                stagnation_counter += 1
                if stagnation_counter >= EXPLORATION_STAGNATION_STEPS:
                    termination_reason = "stalled"
                    break
                decision = ExplorationDecision(
                    status="partial",
                    needs=["more_code"],
                    reason="No new evidence key (file, symbol, read_source); analyzer skipped.",
                    next_action="stop",
                )
                stop, stop_reason = self._should_stop(ex_state, decision)
                if stop:
                    termination_reason = stop_reason
                    break
                continue

        allow_definition_complete = "find_definition" in (intent.intents or [])
        completion_status = "incomplete"
        if ex_state.last_decision == "sufficient" and ex_state.primary_symbol:
            if (
                ex_state.relationships_found
                or primary_symbol_body_seen
                or allow_definition_complete
                or self._definition_like(instruction)
            ):
                completion_status = "complete"
        # pending_exhausted: ranked queue drained without early abort (not no_relevant_candidate,
        # stalled, or max_steps). Listing / broad tasks may never get analyzer "sufficient" but
        # still finished the worklist — treat as complete for planner gating (ModeManager).
        if termination_reason == "pending_exhausted":
            completion_status = "complete"
        if termination_reason == "unknown":
            termination_reason = "max_steps" if ex_state.steps_taken >= EXPLORATION_MAX_STEPS else "stopped"
        self._last_termination_reason = termination_reason
        self.last_working_memory = memory
        ctx = getattr(state, "context", None)
        if isinstance(ctx, dict):
            ctx["exploration_available_symbols"] = sorted(ex_state.available_symbols | ex_state.expanded_symbols)
            ctx["exploration_missing_symbols"] = sorted(ex_state.missing_symbols)
        return self._build_result_from_memory(
            memory,
            instruction,
            completion_status=completion_status,
            termination_reason=termination_reason,
            explored_files=len(ex_state.seen_files),
            explored_symbols=len(ex_state.seen_symbols),
            exploration_outer=exploration_outer,
            state=state,
            engine_loop_steps=ex_state.steps_taken,
        )

    def _run_discovery_traced(
        self,
        exploration_outer: Any,
        phase: str,
        intent: QueryIntent,
        state: Any,
        ex_state: ExplorationState,
        *,
        refine_phase: bool = False,
    ) -> tuple[list[ExplorationCandidate], list[tuple[str, dict, ExecutionResult]], int]:
        """
        Run ``_discovery`` under a Langfuse span ``exploration.discovery`` (batched SEARCH tools).

        Returns ``(candidates, discovery_records, duration_ms)``.
        """
        discovery_span: Any = None
        if exploration_outer is not None and hasattr(exploration_outer, "span"):
            try:
                discovery_span = exploration_outer.span(
                    "exploration.discovery",
                    input={
                        "phase": phase,
                        "steps_taken": ex_state.steps_taken,
                        "intent_symbols_n": len(intent.symbols or []),
                        "intent_keywords_n": len(intent.keywords or []),
                        "intent_regex_n": len(getattr(intent, "regex_patterns", None) or []),
                    },
                )
            except Exception:
                discovery_span = None
        t0 = time.perf_counter()
        try:
            candidates, discovery_records = self._discovery(
                intent, state, ex_state, refine_phase=refine_phase
            )
        except Exception as exc:
            lf_span_end_output(
                discovery_span,
                output={
                    "tool": "search",
                    "batch": True,
                    "phase": phase,
                    "error": str(exc)[:2000],
                },
            )
            raise
        ms = int((time.perf_counter() - t0) * 1000)
        lf_span_end_output(
            discovery_span,
            output=_discovery_tool_langfuse_output(
                phase=phase,
                intent=intent,
                candidates=candidates,
                records=discovery_records,
                discovery_ms=ms,
            ),
        )
        return candidates, discovery_records, ms

    @staticmethod
    def _discovery_query_channel_to_source(
        query_type: Literal["symbol", "regex", "text"],
    ) -> Literal["graph", "grep", "vector"]:
        """Map intent channel to ExplorationCandidate.source (schema literals)."""
        if query_type == "symbol":
            return "graph"
        if query_type == "regex":
            return "grep"
        return "vector"

    @staticmethod
    def _discovery_row_score(row: dict) -> float:
        try:
            return float(row.get("score") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _merge_discovery_snippets(parts: list[str], max_chars: int) -> str:
        seen: set[str] = set()
        out: list[str] = []
        for p in parts:
            p = (p or "").strip()
            if not p or p in seen:
                continue
            seen.add(p)
            out.append(p)
        joined = "\n---\n".join(out)
        if len(joined) > max_chars:
            return joined[:max_chars]
        return joined

    def _may_enqueue_file_candidate(
        self,
        ex_state: ExplorationState,
        path: str,
        symbols: list[str],
    ) -> bool:
        if not symbols:
            return self._may_enqueue(ex_state, path, None)
        for s in symbols:
            if self._may_enqueue(ex_state, path, s if s else None):
                return True
        return False

    def run_retrieval_pipeline(
        self,
        instruction: str,
        intent: QueryIntent,
    ) -> list[ExplorationCandidate]:
        """Mid-pipeline extraction point for validation harnesses.

        Runs the full discovery path — multi-retrieval → file-level merge →
        cross-encoder rerank → post-rerank top_k — and returns the resulting
        candidates without entering the scoper/selector/exploration loop.

        No LLM is called here.  The caller must have wired a real (or stub)
        dispatcher that responds to SEARCH steps.
        """
        _LOG.debug("[ExplorationEngineV2.run_retrieval_pipeline]")
        from types import SimpleNamespace  # late import: keeps method dependency-free

        ex_state = ExplorationState(instruction=instruction)
        state = SimpleNamespace(context={})
        candidates, _ = self._discovery(intent, state, ex_state)
        return candidates

    def _discovery_rerank_candidates(
        self,
        candidates: list[ExplorationCandidate],
        rank_query: str,
    ) -> list[ExplorationCandidate]:
        """Reorder file-level candidates by cross-encoder relevance (MiniLM ONNX CPU)."""
        if not candidates or not (rank_query or "").strip():
            return candidates
        try:
            from config.retrieval_config import (
                RERANKER_ENABLED,
                RERANK_FUSION_WEIGHT,
                RERANK_MIN_CANDIDATES,
                RETRIEVER_FUSION_WEIGHT,
            )
            from agent.retrieval.reranker.reranker_factory import create_reranker
        except Exception:
            return candidates

        if not EXPLORATION_DISCOVERY_RERANK_ENABLED or not RERANKER_ENABLED:
            return candidates

        exp_floor = EXPLORATION_DISCOVERY_RERANK_MIN_CANDIDATES
        if exp_floor >= 0 and len(candidates) < exp_floor:
            return candidates
        if len(candidates) < RERANK_MIN_CANDIDATES:
            return candidates

        reranker = create_reranker()
        if reranker is None:
            return candidates

        docs: list[str] = []
        for c in candidates:
            summ = (c.snippet_summary or c.snippet or "").strip()
            syms = ", ".join(c.symbols) if c.symbols else ""
            docs.append(f"{c.file_path}\nSymbols: {syms}\n{summ}")

        scored = reranker.rerank_batch([(rank_query, docs)])[0]

        score_by_doc = {d: float(s) for d, s in scored}

        def _fusion(c: ExplorationCandidate, doc: str) -> float:
            rs = float(score_by_doc.get(doc, 0.0))
            if not EXPLORATION_DISCOVERY_RERANK_USE_FUSION:
                return rs
            ds = float(c.discovery_max_score or 0.0)
            return rs * RERANK_FUSION_WEIGHT + ds * RETRIEVER_FUSION_WEIGHT

        paired = list(zip(candidates, docs))
        paired.sort(key=lambda t: _fusion(t[0], t[1]), reverse=True)
        out: list[ExplorationCandidate] = []
        for c, doc in paired:
            c.discovery_rerank_score = float(score_by_doc.get(doc, 0.0))
            out.append(c)
        return out

    def _discovery(
        self,
        intent: QueryIntent,
        state: Any,
        ex_state: ExplorationState,
        *,
        refine_phase: bool = False,
    ) -> tuple[list[ExplorationCandidate], list[tuple[str, dict, ExecutionResult]]]:
        records: list[tuple[str, dict, ExecutionResult]] = []
        base_root = get_project_root()

        if not refine_phase:
            ex_state.discovery_keyword_inject = []

        symbol_queries = list(dict.fromkeys(intent.symbols))[:DISCOVERY_SYMBOL_CAP]
        text_queries = list(dict.fromkeys(intent.keywords))[:DISCOVERY_TEXT_CAP]
        inject_kw = list(dict.fromkeys(getattr(ex_state, "discovery_keyword_inject", None) or []))[:]
        if inject_kw and refine_phase:
            text_queries = list(dict.fromkeys(text_queries + inject_kw))[:DISCOVERY_TEXT_CAP]
            ex_state.discovery_keyword_inject = []
        regex_src = getattr(intent, "regex_patterns", None)
        if not isinstance(regex_src, list):
            regex_src = []
        regex_queries = list(dict.fromkeys(str(x) for x in regex_src if str(x).strip()))[:DISCOVERY_REGEX_CAP]

        def _collect_pairs(
            query_type: Literal["symbol", "regex", "text"], queries: list[str]
        ) -> list[tuple[str, ExecutionResult]]:
            if not queries:
                return []
            prefix = f"discovery_{ex_state.steps_taken}_{query_type}"
            results = self._dispatcher.search_batch(
                queries,
                state,
                mode=query_type,
                step_id_prefix=prefix,
                max_workers=DISCOVERY_SEARCH_BATCH_MAX_WORKERS,
            )
            return list(zip(queries, results))

        with ThreadPoolExecutor(max_workers=DISCOVERY_QUERY_POOL_MAX_WORKERS) as outer:
            f_sym = outer.submit(_collect_pairs, "symbol", symbol_queries)
            f_reg = outer.submit(_collect_pairs, "regex", regex_queries)
            f_txt = outer.submit(_collect_pairs, "text", text_queries)
            sym_pairs = f_sym.result()
            reg_pairs = f_reg.result()
            txt_pairs = f_txt.result()

        # file_merge[canon_path] -> aggregates (one ExplorationCandidate per file after build)
        file_merge: dict[str, dict[str, Any]] = {}

        def _ingest_pairs(
            pairs: list[tuple[str, ExecutionResult]],
            query_type: Literal["symbol", "regex", "text"],
        ) -> None:
            src_lit = self._discovery_query_channel_to_source(query_type)
            ch = query_type
            for q, res in pairs:
                records.append(
                    (
                        "discovery",
                        {"query": q, "query_type": query_type, "mode": query_type},
                        res,
                    )
                )
                data = res.output.data if res.output else {}
                raw = data.get("results") or data.get("candidates") or []
                if not isinstance(raw, list):
                    continue
                for row in raw:
                    if not isinstance(row, dict):
                        continue
                    fp = str(row.get("file") or row.get("file_path") or "").strip()
                    if not fp:
                        continue
                    canon = self._canonical_path(fp, base_root=base_root)
                    sym_raw = row.get("symbol")
                    sym = str(sym_raw).strip() if sym_raw else None
                    sc = self._discovery_row_score(row)
                    snip = row.get("snippet") or row.get("content")
                    snip_s = str(snip).strip() if snip else None
                    if canon not in file_merge:
                        file_merge[canon] = {
                            "max_score": sc,
                            "breakdown": {"symbol": None, "regex": None, "text": None},
                            "symbols_order": [],
                            "symbols_set": set(),
                            "sources_order": [],
                            "sources_set": set(),
                            "snippets_order": [],
                            "snippets_set": set(),
                        }
                    m = file_merge[canon]
                    m["max_score"] = max(float(m["max_score"]), sc)
                    prev_ch = m["breakdown"].get(ch)
                    m["breakdown"][ch] = max(
                        prev_ch if prev_ch is not None else 0.0,
                        sc,
                    )
                    if sym and sym not in m["symbols_set"]:
                        m["symbols_set"].add(sym)
                        m["symbols_order"].append(sym)
                    if src_lit not in m["sources_set"]:
                        m["sources_set"].add(src_lit)
                        m["sources_order"].append(src_lit)
                    if snip_s and snip_s not in m["snippets_set"]:
                        m["snippets_set"].add(snip_s)
                        m["snippets_order"].append(snip_s)

        _ingest_pairs(sym_pairs, "symbol")
        _ingest_pairs(reg_pairs, "regex")
        _ingest_pairs(txt_pairs, "text")

        merge_cap = EXPLORATION_DISCOVERY_SNIPPET_MERGE_MAX_CHARS
        built: list[ExplorationCandidate] = []
        for canon, meta in file_merge.items():
            summary = self._merge_discovery_snippets(meta["snippets_order"], merge_cap)
            syms: list[str] = list(meta["symbols_order"])
            chans: list[str] = list(meta["sources_order"])
            prim_sym = syms[0] if syms else None
            prim_src = cast(
                Literal["graph", "grep", "vector"],
                chans[0] if chans else "vector",
            )
            ms = float(meta["max_score"])
            snippet_legacy = summary[: self.MAX_SNIPPET_CHARS] if summary else None
            repo_lbl: str | None = None
            try:
                from pathlib import Path as _Path

                from agent_v2.exploration_test_repos import label_for_path  # noqa: PLC0415

                repo_lbl = label_for_path(canon, _Path(get_project_root()))
            except Exception:
                repo_lbl = None
            cand = ExplorationCandidate(
                file_path=canon,
                symbol=prim_sym,
                symbols=syms,
                snippet=snippet_legacy,
                snippet_summary=summary or None,
                source=prim_src,
                source_channels=cast(
                    list[Literal["graph", "grep", "vector"]],
                    list(chans) if chans else [prim_src],
                ),
                discovery_max_score=ms,
                repo=repo_lbl,
            )
            try:
                object.__setattr__(cand, "_score_breakdown", dict(meta["breakdown"]))
            except Exception:
                pass
            built.append(cand)

        built.sort(key=lambda c: float(c.discovery_max_score or 0.0), reverse=True)
        built = built[: EXPLORATION_DISCOVERY_PRERERANK_POOL_MAX]

        rank_query = (ex_state.instruction or "").strip()
        if not rank_query:
            rank_query = " ".join(intent.keywords or [])[:2000]

        filtered: list[ExplorationCandidate] = [
            c
            for c in built
            if self._may_enqueue_file_candidate(ex_state, c.file_path, c.symbols)
        ]

        reranked = self._discovery_rerank_candidates(filtered, rank_query)
        deduped = reranked[: EXPLORATION_DISCOVERY_POST_RERANK_TOP_K]

        _LOG.info(
            "exploration.discovery steps_taken=%s budget_symbol=%s regex=%s text=%s "
            "merged_files=%s evidence_records=%s candidates_after_may_enqueue=%s "
            "post_rerank_top_k=%s",
            ex_state.steps_taken,
            len(symbol_queries),
            len(regex_queries),
            len(text_queries),
            len(file_merge),
            len(records),
            len(filtered),
            len(deduped),
        )
        return deduped, records

    def _outline_full_for_file(self, canon_path: str) -> list[dict[str, str]]:
        if not canon_path:
            return []
        if canon_path in self._symbol_outline_cache:
            return self._symbol_outline_cache[canon_path]
        from agent_v2.exploration.file_symbol_outline import load_python_file_outline

        raw = load_python_file_outline(canon_path)
        self._symbol_outline_cache[canon_path] = raw
        return raw

    def _outline_rows_for_selector_batch(
        self,
        top_slice: list[ExplorationCandidate],
        instruction: str,
        intent_text: str,
    ) -> list[list[dict[str, str]]] | None:
        if not ENABLE_SYMBOL_AWARE_EXPLORATION:
            return None
        from agent_v2.exploration.file_symbol_outline import rank_outline_for_selector_query

        base_root = get_project_root()
        rows: list[list[dict[str, str]]] = []
        q = f"{instruction}\n{intent_text}"
        for c in top_slice:
            canon = self._canonical_path(c.file_path, base_root=base_root)
            full = self._outline_full_for_file(canon) if canon else []
            rows.append(rank_outline_for_selector_query(full, q, EXPLORATION_OUTLINE_TOP_K_FOR_SELECTOR))
        if rows and EXPLORATION_SYMBOL_AWARE_LOG_PROGRESS:
            ranked_total = sum(len(r) for r in rows)
            ranked_max = max((len(r) for r in rows), default=0)
            _LOG.info(
                "exploration.symbol_aware outlines_ready files=%s ranked_symbol_slots_sum=%s max_per_file=%s",
                len(rows),
                ranked_total,
                ranked_max,
            )
        return rows

    @staticmethod
    def _bound_context_blocks_by_chars(
        blocks: list[ContextBlock],
        *,
        max_chars: int,
    ) -> list[ContextBlock]:
        if max_chars <= 0:
            return []
        out: list[ContextBlock] = []
        used = 0
        for b in blocks:
            text = str(getattr(b, "content", "") or "")
            if not text.strip():
                continue
            if used >= max_chars:
                break
            remaining = max_chars - used
            if len(text) <= remaining:
                out.append(b)
                used += len(text)
                continue
            clipped = text[:remaining]
            out.append(b.model_copy(update={"content": clipped}))
            break
        return out

    @staticmethod
    def _collect_all_selected_symbol_names(batch: SelectorBatchResult | None) -> list[str]:
        if batch is None:
            return []
        names: list[str] = []
        seen: set[str] = set()
        for k in sorted(batch.selected_symbols.keys(), key=lambda x: int(x) if str(x).isdigit() else 10**9):
            for raw in batch.selected_symbols.get(k, []) or []:
                s = str(raw).strip()
                if not s or s in seen:
                    continue
                seen.add(s)
                names.append(s)
        for raw in batch.expanded_symbols or []:
            s = str(raw).strip()
            if not s or s in seen:
                continue
            seen.add(s)
            names.append(s)
        return sorted(names, key=lambda x: x.lower())

    def _build_analyzer_symbol_context_blocks(
        self,
        target: ExplorationTarget,
        *,
        max_chars: int,
    ) -> list[ContextBlock]:
        """
        Build bounded symbol context for analyzer from ALL selected selector symbols.
        Full code first by deterministic budget, overflow as `[trimmed]` signatures.
        """
        batch = target.selector_batch
        names = self._collect_all_selected_symbol_names(batch)
        if not names:
            return []
        if batch is None:
            return []
        base_root = get_project_root()

        idx_to_file: dict[str, str] = {}
        for i, c in enumerate(batch.selected_candidates):
            top_idx = batch.selected_top_indices[i] if i < len(batch.selected_top_indices) else i
            canon = self._canonical_path(c.file_path, base_root=base_root)
            if canon:
                idx_to_file[str(top_idx)] = canon

        all_candidate_files = sorted(set(idx_to_file.values()))
        for c in batch.selected_candidates:
            canon = self._canonical_path(c.file_path, base_root=base_root)
            if canon and canon not in all_candidate_files:
                all_candidate_files.append(canon)

        by_pair: dict[tuple[str, str], dict[str, str]] = {}
        for canon in all_candidate_files:
            for row in self._outline_full_for_file(canon):
                n = str(row.get("name") or "").strip()
                if not n:
                    continue
                rec = dict(row)
                rec["file_path"] = canon
                by_pair[(canon, n)] = rec

        selected_rows: list[dict[str, str]] = []
        selected_seen: set[tuple[str, str]] = set()
        # First, keyed selection by selected_symbols index->candidate file.
        for k in sorted(batch.selected_symbols.keys(), key=lambda x: int(x) if str(x).isdigit() else 10**9):
            canon = idx_to_file.get(str(k), "")
            if not canon:
                continue
            for raw_name in batch.selected_symbols.get(k, []) or []:
                name = str(raw_name).strip()
                pair = (canon, name)
                row = by_pair.get(pair)
                if row is None or pair in selected_seen:
                    continue
                selected_seen.add(pair)
                selected_rows.append(row)
        # Then fill missing from expanded/all names by searching candidate files.
        for name in names:
            found = None
            for canon in all_candidate_files:
                pair = (canon, name)
                if pair in selected_seen:
                    found = pair
                    break
                row = by_pair.get(pair)
                if row is not None:
                    found = pair
                    break
            if not found:
                continue
            if found in selected_seen:
                continue
            row = by_pair.get(found)
            if row is None:
                continue
            selected_seen.add(found)
            selected_rows.append(row)

        bounded = build_bounded_symbol_context(
            selected_rows,
            max_code_chars=max_chars,
            trim_notice=ANALYZER_CODE_TRIM_NOTICE,
        )
        if not bounded:
            return []

        lines: list[str] = []
        for r in bounded:
            name = str(r.get("name") or "").strip()
            fp = str(r.get("file_path") or target.file_path)
            code = str(r.get("code") or "")
            if not code.strip():
                continue
            lines.append(f"{fp}::{name}")
            lines.append(code)
            lines.append("")
        content = "\n".join(lines).strip()
        if not content:
            return []
        return [
            ContextBlock(
                file_path=str(target.file_path),
                start=1,
                end=max(1, len(content.splitlines())),
                content=content,
                origin_reason="selector_symbol_context_bounded",
                symbol=None,
                relationship_refs=[],
            )
        ]

    def _symbol_relationships_block_for_target(self, target: ExplorationTarget, state: Any) -> str:
        if not ENABLE_SYMBOL_AWARE_EXPLORATION:
            return ""
        if not exploration_symbol_graph_lookup_enabled():
            return ""
        batch = target.selector_batch
        if batch is None:
            return ""
        j = target.selector_top_index
        names: list[str] = []
        if j is not None:
            names = list(batch.selected_symbols.get(str(j), []) or [])
        # Symbol-aware only: never mix graph hints with discovery `target.symbol` when
        # selector symbols were absent or fully invalidated (file-only inspect path).
        names = [n.strip() for n in names if str(n).strip()][:EXPLORATION_SYMBOL_RELATIONSHIPS_MAX_NAMES]
        if not names:
            return ""
        ctx = getattr(state, "context", None)
        project_root = ""
        if isinstance(ctx, dict):
            project_root = str(ctx.get("project_root") or "")
        base_root = project_root or get_project_root()
        from agent_v2.exploration.graph_symbol_edges_batch import (
            fetch_callers_callees_batch,
            format_symbol_relationships_block,
        )

        canon = self._canonical_path(target.file_path, base_root=base_root)
        items: list[tuple[str, str]] = []
        seen_pairs: set[tuple[str, str]] = set()
        for n in names:
            pair = (canon, n)
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            items.append((canon, n))
        if EXPLORATION_SYMBOL_AWARE_LOG_PROGRESS:
            _LOG.info(
                "exploration.symbol_aware graph_fetch start symbols=%s deduped_pairs=%s file=%s",
                names,
                len(items),
                Path(canon).name if canon else "",
            )
        edges = fetch_callers_callees_batch(
            items,
            project_root or base_root,
            k_each=EXPLORATION_SYMBOL_GRAPH_CONTEXT_K,
        )
        block = format_symbol_relationships_block(
            edges,
            max_chars=EXPLORATION_SYMBOL_RELATIONSHIPS_MAX_CHARS,
        )
        if EXPLORATION_SYMBOL_AWARE_LOG_PROGRESS:
            if block.strip():
                _LOG.info(
                    "exploration.symbol_aware graph_fetch block_chars=%s max=%s",
                    len(block),
                    EXPLORATION_SYMBOL_RELATIONSHIPS_MAX_CHARS,
                )
            else:
                _LOG.info("exploration.symbol_aware graph_fetch block_empty (no edges or truncated config)")
        return block

    def _enqueue_ranked(
        self,
        instruction: str,
        intent: QueryIntent,
        candidates: list[ExplorationCandidate],
        ex_state: ExplorationState,
        *,
        limit: int,
        expl_parent: Any = None,
        obs: Any = None,
    ) -> tuple[bool, SelectorBatchResult]:
        """
        Returns (selection_none, selector_batch). selection_none is True when no targets were enqueued.
        Always sets ex_state.last_selector_batch to the selector output (incl. empty/weak coverage).
        """
        empty_batch = SelectorBatchResult(
            selected_candidates=[],
            selection_confidence="low",
            coverage_signal="empty",
        )
        if not candidates:
            ex_state.last_selector_batch = empty_batch
            return True, empty_batch
        candidates = [c for c in candidates if self._may_enqueue(ex_state, c.file_path, c.symbol)]
        if not candidates:
            skipped = SelectorBatchResult(
                selected_candidates=[],
                selection_confidence="low",
                coverage_signal="weak",
            )
            ex_state.last_selector_batch = skipped
            return True, skipped
        capped = candidates[:EXPLORATION_SCOPER_K]
        need_scope_llm = self._scoper is not None and len(capped) > EXPLORATION_SCOPER_SKIP_BELOW
        scope_span: Any = None
        if need_scope_llm and expl_parent is not None and hasattr(expl_parent, "span"):
            try:
                scope_span = expl_parent.span("exploration.scope", input={"phase": "scope"})
                if obs is not None:
                    obs.current_span = scope_span
            except Exception:
                scope_span = None
        try:
            if need_scope_llm:
                scoped = self.scope(
                    instruction,
                    capped,
                    lf_scope_span=scope_span,
                    lf_exploration_parent=expl_parent,
                )
            else:
                scoped = capped
                if self._scoper is not None:
                    _LOG.debug(
                        "exploration_scoper skip: scoper_skipped=true scoper_input_n=%s (skip_below=%s)",
                        len(capped),
                        EXPLORATION_SCOPER_SKIP_BELOW,
                    )
            if scope_span is not None:
                try:
                    scope_span.update(
                        metadata={
                            "input_count": len(capped),
                            "output_count": len(scoped),
                        }
                    )
                except Exception:
                    pass
        finally:
            if obs is not None:
                obs.current_span = None
            _lf_end(scope_span)

        # Observability: log exact scoper->selector index handoff to avoid
        # confusing selector-local indices with scoper selection indices.
        capped_obj_idx = {id(c): i for i, c in enumerate(capped)}
        scoped_indices_in_capped: list[int] = []
        for c in scoped:
            idx = capped_obj_idx.get(id(c))
            if idx is not None:
                scoped_indices_in_capped.append(idx)
        if len(scoped_indices_in_capped) != len(scoped):
            # Fallback match for any missed identity mapping (defensive).
            used_fallback: set[int] = set(scoped_indices_in_capped)
            for c in scoped:
                idx = capped_obj_idx.get(id(c))
                if idx is not None:
                    continue
                for j, cand in enumerate(capped):
                    if j in used_fallback:
                        continue
                    if (
                        cand.file_path == c.file_path
                        and (cand.symbol or "") == (c.symbol or "")
                        and (cand.source or "") == (c.source or "")
                    ):
                        scoped_indices_in_capped.append(j)
                        used_fallback.add(j)
                        break

        select_span: Any = None
        if expl_parent is not None and hasattr(expl_parent, "span"):
            try:
                select_span = expl_parent.span("exploration.select", input={"phase": "select"})
                if obs is not None:
                    obs.current_span = select_span
            except Exception:
                select_span = None
        try:
            intent_text = ", ".join([s for s in (intent.intents or []) if str(s).strip()]) or "no intent"
            top_slice = scoped[:EXPLORATION_SELECTOR_TOP_K]
            top_slice_indices_in_capped = scoped_indices_in_capped[: len(top_slice)]
            _LOG.info(
                "exploration.selector_handoff scoper_selected_indices=%s selector_prompt_indices=%s "
                "scoped_n=%s prompt_n=%s",
                scoped_indices_in_capped,
                top_slice_indices_in_capped,
                len(scoped),
                len(top_slice),
            )
            outline_rows = self._outline_rows_for_selector_batch(top_slice, instruction, intent_text)
            ranked = self._selector.select_batch(
                instruction,
                intent_text,
                scoped,
                limit=min(limit, len(scoped)),
                explored_location_keys=ex_state.explored_location_keys,
                lf_select_span=select_span,
                lf_exploration_parent=expl_parent,
                outline_rows=outline_rows,
            )
        finally:
            if obs is not None:
                obs.current_span = None
            _lf_end(select_span)
        ex_state.last_selector_batch = ranked
        for sym in ranked.expanded_symbols:
            s = str(sym).strip()
            if s:
                ex_state.available_symbols.add(s)
        if (
            ENABLE_SYMBOL_AWARE_EXPLORATION
            and EXPLORATION_SYMBOL_AWARE_LOG_PROGRESS
            and outline_rows is not None
        ):
            _LOG.info(
                "exploration.symbol_aware selector_batch done selected=%s top_idx=%s "
                "selected_symbol_keys=%s coverage=%s conf=%s",
                len(ranked.selected_candidates),
                ranked.selected_top_indices,
                sorted(ranked.selected_symbols.keys()),
                ranked.coverage_signal,
                ranked.selection_confidence,
            )
        targets: list[ExplorationTarget] = []
        for i, c in enumerate(ranked.selected_candidates):
            j = (
                ranked.selected_top_indices[i]
                if i < len(ranked.selected_top_indices)
                else None
            )
            syms = ranked.selected_symbols.get(str(j), []) if j is not None else []
            if ENABLE_SYMBOL_AWARE_EXPLORATION:
                # Clean split: validated selector symbols → symbol-body read; else file-level only
                # (do not fall back to discovery candidate.symbol — avoids silent mismatch).
                primary: str | None = syms[0] if syms else None
                if syms:
                    _LOG.debug(
                        "exploration.enqueue discovery target symbol_aware=true file=%s symbol=%s",
                        c.file_path,
                        primary,
                    )
                else:
                    _LOG.debug(
                        "exploration.enqueue discovery symbol_aware=file_only file=%s (no validated selected_symbols)",
                        c.file_path,
                    )
            else:
                primary = syms[0] if syms else c.symbol
            targets.append(
                ExplorationTarget(
                    file_path=str(c.file_path),
                    symbol=primary,
                    source="discovery",
                    selector_batch=ranked,
                    selector_top_index=j,
                )
            )
        if not targets:
            return True, ranked
        if ENABLE_SYMBOL_AWARE_EXPLORATION and EXPLORATION_SYMBOL_AWARE_LOG_PROGRESS:
            n_sym = sum(1 for t in targets if t.symbol)
            _LOG.info(
                "exploration.symbol_aware enqueue_targets n=%s symbol_read=%s file_only=%s",
                len(targets),
                n_sym,
                len(targets) - n_sym,
            )
        self._enqueue_targets(ex_state, targets)
        return False, ranked

    @staticmethod
    def _merge_expansion_symbols(
        required_symbols: list[str],
        expanded_symbols: list[str],
        *,
        cap: int,
    ) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for sym in list(required_symbols or []) + list(expanded_symbols or []):
            s = str(sym).strip()
            if not s or s in seen:
                continue
            seen.add(s)
            out.append(s)
            if len(out) >= cap:
                break
        return out

    @staticmethod
    def _canonical_path(path: str, *, base_root: str) -> str:
        raw = str(path or "").strip()
        if not raw:
            return ""
        p = Path(raw)
        if not p.is_absolute():
            p = Path(base_root) / raw
        try:
            return str(p.resolve())
        except Exception:
            return str(p)

    def _make_location_key(self, path: str, symbol: str | None) -> tuple[str, str]:
        base_root = get_project_root()
        canon = self._canonical_path(path, base_root=base_root)
        return (canon, (symbol or "").strip())

    def _may_enqueue(self, ex_state: ExplorationState, path: str, symbol: str | None) -> bool:
        key = self._make_location_key(path, symbol)
        if key in ex_state.explored_location_keys:
            return False
        if key[0] in ex_state.excluded_paths:
            return False
        for t in ex_state.pending_targets:
            if self._make_location_key(t.file_path, t.symbol) == key:
                return False
        return True

    def _expand_skip_sets(self, ex_state: ExplorationState) -> tuple[set[str], set[str]]:
        """Skip files/symbols already seen or pending before graph expansion."""
        base_root = get_project_root()
        skip_files: set[str] = set(ex_state.seen_files)
        skip_symbols: set[str] = set(ex_state.seen_symbols)
        for fp, _sym in ex_state.explored_location_keys:
            if fp:
                skip_files.add(fp)
        for t in ex_state.pending_targets:
            canon = self._canonical_path(t.file_path, base_root=base_root)
            if canon:
                skip_files.add(canon)
            if t.symbol and str(t.symbol).strip():
                skip_symbols.add(str(t.symbol).strip())
        return skip_files, skip_symbols

    def _prefilter_expansion_targets(
        self,
        ex_state: ExplorationState,
        targets: list[ExplorationTarget],
        gap_bundle_key: str,
    ) -> list[ExplorationTarget]:
        """Drop duplicates before enqueue; record (gap, file, symbol) attempts."""
        base_root = get_project_root()
        gk = (gap_bundle_key or "").strip().lower()
        out: list[ExplorationTarget] = []
        for t in targets:
            canon = self._canonical_path(t.file_path, base_root=base_root)
            sym = (t.symbol or "").strip()
            if not self._may_enqueue(ex_state, canon, t.symbol):
                continue
            tri = (gk, canon, sym)
            if gk and tri in ex_state.attempted_gap_targets:
                continue
            if gk:
                ex_state.attempted_gap_targets.add(tri)
            if canon and canon != t.file_path:
                out.append(t.model_copy(update={"file_path": canon}))
            else:
                out.append(t)
        return out

    @staticmethod
    def _definition_like(instruction: str) -> bool:
        """
        System-only heuristic for definition/location queries.
        This avoids relying on LLM intent classification for planner gating.
        """
        s = (instruction or "").strip().lower()
        return (
            "where is" in s
            or "where are" in s
            or "defined" in s
            or "definition" in s
            or "locate" in s
        )

    @staticmethod
    def _context_feedback_payload_counts(cf: dict[str, Any] | None) -> dict[str, int]:
        """Counts aligned with QueryIntentParser Langfuse input_extra (observability)."""
        if not isinstance(cf, dict):
            return {
                "context_feedback_present": 0,
                "partial_findings_count": 0,
                "known_symbols_count": 0,
                "known_files_count": 0,
                "knowledge_gaps_count": 0,
                "relationships_count": 0,
            }
        pf = cf.get("partial_findings")
        n_pf = len(pf) if isinstance(pf, list) else 0
        ke = cf.get("known_entities")
        n_sym = n_kf = 0
        if isinstance(ke, dict):
            ks = ke.get("symbols")
            if isinstance(ks, list):
                n_sym = len(ks)
            kf = ke.get("files")
            if isinstance(kf, list):
                n_kf = len(kf)
        kg = cf.get("knowledge_gaps")
        n_kg = len(kg) if isinstance(kg, list) else 0
        rel = cf.get("relationships")
        n_rel = len(rel) if isinstance(rel, list) else 0
        return {
            "context_feedback_present": 1,
            "partial_findings_count": n_pf,
            "known_symbols_count": n_sym,
            "known_files_count": n_kf,
            "knowledge_gaps_count": n_kg,
            "relationships_count": n_rel,
        }

    def _log_exploration_context_feedback_trace(
        self,
        phase: str,
        *,
        context_feedback: dict[str, Any],
        ex_state: ExplorationState,
        failure_reason: str | None = None,
        extra: dict[str, Any] | None = None,
        exploration_outer: Any = None,
    ) -> None:
        counts = self._context_feedback_payload_counts(context_feedback)
        payload: dict[str, Any] = {
            "event": "exploration.context_feedbacktrace",
            "phase": phase,
            **counts,
            "steps_taken": int(ex_state.steps_taken),
            "expansion_depth": int(ex_state.expansion_depth),
            "backtracks": int(ex_state.backtracks),
            "seen_symbols_count": len(ex_state.seen_symbols),
            "seen_files_count": len(ex_state.seen_files),
        }
        if failure_reason:
            payload["failure_reason"] = str(failure_reason)[:500]
        if extra:
            payload.update(extra)
        try:
            line = json.dumps(payload, ensure_ascii=False, default=str)
        except Exception:
            line = str(payload)
        _LOG.info("exploration.context_feedbacktrace %s", line)
        if exploration_outer is not None and hasattr(exploration_outer, "event"):
            try:
                exploration_outer.event(
                    name="exploration.context_feedbacktrace",
                    metadata=payload,
                )
            except Exception:
                pass

    def _enqueue_targets(
        self,
        ex_state: ExplorationState,
        targets: list[ExplorationTarget],
        *,
        relation_bucket_by_key: dict[tuple[str, str], Literal["primary", "related", "other"]] | None = None,
    ) -> None:
        tier_primary_novel: list[ExplorationTarget] = []
        tier_primary_seen: list[ExplorationTarget] = []
        tier_related_novel: list[ExplorationTarget] = []
        tier_related_seen: list[ExplorationTarget] = []
        tier_other_novel: list[ExplorationTarget] = []
        tier_other_seen: list[ExplorationTarget] = []
        gk = (getattr(ex_state, "gap_bundle_key_for_expansion", None) or "").strip().lower()
        for target in targets:
            edge_hash = self._edge_hash_for_target(ex_state.current_target, target)
            if edge_hash in ex_state.seen_relation_edges:
                continue
            key = self._make_location_key(target.file_path, target.symbol)
            canon, sym = key[0], key[1]
            if not self._may_enqueue(ex_state, canon, target.symbol):
                continue
            tri = (gk, canon, sym)
            # Hard constraint: do not revisit same (gap, file, symbol) target.
            if gk and tri in ex_state.attempted_gap_targets:
                continue
            ex_state.seen_relation_edges.add(edge_hash)
            if gk:
                ex_state.attempted_gap_targets.add(tri)

            relation_bucket = self._relation_bucket_for_target(
                target,
                ex_state,
                relation_bucket_by_key=relation_bucket_by_key,
            )
            novelty_bucket = self._target_priority_score(target, ex_state)
            normalized = target.model_copy(update={"file_path": canon})

            if relation_bucket == "primary":
                if novelty_bucket == "novel":
                    tier_primary_novel.append(normalized)
                else:
                    tier_primary_seen.append(normalized)
            elif relation_bucket == "related":
                if novelty_bucket == "novel":
                    tier_related_novel.append(normalized)
                else:
                    tier_related_seen.append(normalized)
            else:
                if novelty_bucket == "novel":
                    tier_other_novel.append(normalized)
                else:
                    tier_other_seen.append(normalized)

        ordered = (
            tier_primary_novel
            + tier_primary_seen
            + tier_other_novel
            + tier_other_seen
            + tier_related_novel
            + tier_related_seen
        )
        ex_state.pending_targets.extend(ordered)

    @classmethod
    def _is_generic_gap(cls, normalized_gap: str) -> bool:
        if len(normalized_gap) < 8:
            return True
        return any(marker in normalized_gap for marker in cls._GENERIC_GAP_MARKERS)

    @staticmethod
    def _target_priority_score(target: ExplorationTarget, ex_state: ExplorationState) -> Literal["novel", "seen"]:
        if target.symbol and target.symbol not in ex_state.seen_symbols:
            return "novel"
        # Keep novelty categorical and symbol-first to stabilize traversal.
        if not target.symbol and target.file_path not in ex_state.seen_files:
            return "novel"
        return "seen"

    @staticmethod
    def _edge_hash_for_target(current: ExplorationTarget | None, nxt: ExplorationTarget) -> str:
        src = (current.file_path, current.symbol or "") if current is not None else ("", "")
        dst = (nxt.file_path, nxt.symbol or "")
        return f"{src[0]}::{src[1]}=>{dst[0]}::{dst[1]}"

    @staticmethod
    def _relevance_rank(value: str) -> int:
        if value == "high":
            return 3
        if value == "medium":
            return 2
        return 1

    @staticmethod
    def _merge_candidates(
        base: list[ExplorationCandidate],
        new: list[ExplorationCandidate],
    ) -> list[ExplorationCandidate]:
        seen: set[tuple[str, str]] = set()
        out: list[ExplorationCandidate] = []
        for item in base + new:
            key = (item.file_path, item.symbol or "")
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
        return out

    @staticmethod
    def _to_candidates(raw_results: list[dict], source: str) -> list[ExplorationCandidate]:
        out: list[ExplorationCandidate] = []
        if not isinstance(raw_results, list):
            return out
        src = "vector" if source == "vector" else ("graph" if source == "graph" else "grep")
        for row in raw_results:
            if not isinstance(row, dict):
                continue
            file_path = str(row.get("file") or row.get("file_path") or "").strip()
            if not file_path:
                continue
            out.append(
                ExplorationCandidate(
                    symbol=(str(row.get("symbol")).strip() if row.get("symbol") else None),
                    file_path=file_path,
                    snippet=(str(row.get("snippet")).strip() if row.get("snippet") else None),
                    source=src,
                )
            )
        return out

    @staticmethod
    def _next_action(decision: ExplorationDecision) -> str:
        if decision.next_action in ("expand", "refine", "stop"):
            return decision.next_action
        if decision.status == "partial" and ("callers" in decision.needs or "callees" in decision.needs):
            return "expand"
        if decision.status == "wrong_target" or "different_symbol" in decision.needs:
            return "refine"
        return "stop"

    @staticmethod
    def _should_expand(
        action: str,
        decision: ExplorationDecision,
        target: ExplorationTarget,
        ex_state: ExplorationState,
    ) -> bool:
        wants_expand = action == "expand" or (
            decision.status == "sufficient" and not ex_state.relationships_found
        )
        if not wants_expand:
            return False
        if not target.symbol:
            return False
        if ex_state.expansion_depth >= EXPLORATION_EXPAND_MAX_DEPTH:
            return False
        if target.symbol in ex_state.expanded_symbols:
            return False
        if not ({"callers", "callees"} & set(decision.needs) or decision.status == "partial"):
            return False
        ex_state.expanded_symbols.add(target.symbol)
        return True

    @staticmethod
    def _should_refine(
        action: str,
        decision: ExplorationDecision,
        ex_state: ExplorationState,
        *,
        target: ExplorationTarget | None = None,
        memory: ExplorationWorkingMemory | None = None,
    ) -> bool:
        if ex_state.backtracks >= EXPLORATION_MAX_BACKTRACKS:
            return False
        if decision.status == "wrong_target":
            return True
        reason_l = str(decision.reason or "").lower()
        if "low relevance" in reason_l:
            return True
        if action != "refine":
            return False
        # Relationship-oriented gaps in memory + graph expansion still viable → do not refine here.
        if target is not None and memory is not None:
            sim_needs = list(decision.needs)
            rel_signal = bool({"callers", "callees"} & set(sim_needs))
            for row in memory.get_summary().get("gaps") or []:
                if not isinstance(row, dict):
                    continue
                cat = ExplorationEngineV2._classify_gap_category(str(row.get("description") or ""))
                if cat == "caller":
                    if "callers" not in sim_needs:
                        sim_needs.append("callers")
                    rel_signal = True
                elif cat in ("callee", "flow"):
                    if "callees" not in sim_needs:
                        sim_needs.append("callees")
                    rel_signal = True
            if rel_signal:
                sym = (target.symbol or "").strip()
                if (
                    sym
                    and ex_state.expansion_depth < EXPLORATION_EXPAND_MAX_DEPTH
                    and target.symbol not in ex_state.expanded_symbols
                ):
                    return False
        return decision.status == "partial"

    def _enforce_direction_routing(
        self,
        expanded: list[ExplorationTarget],
        expand_data: dict[str, Any],
        ex_state: ExplorationState,
        *,
        direction_hint: str | None,
    ) -> tuple[list[ExplorationTarget], dict[tuple[str, str], Literal["primary", "related", "other"]]]:
        hint = (direction_hint or "").strip().lower()
        if hint not in ("callers", "callees", "both"):
            return expanded, {}
        primary_keys: set[tuple[str, str]] = set()
        related_keys: set[tuple[str, str]] = set()
        callers = expand_data.get("callers") or []
        callees = expand_data.get("callees") or []
        related = expand_data.get("related") or []
        if hint == "callers":
            primary_keys = self._expand_bucket_keys(callers)
        elif hint == "callees":
            primary_keys = self._expand_bucket_keys(callees)
        else:
            primary_keys = self._expand_bucket_keys(callers) | self._expand_bucket_keys(callees)
        related_keys = self._expand_bucket_keys(related)
        if not primary_keys and not related_keys:
            # Missing bucket metadata: preserve original expander output.
            return expanded, {}

        primary: list[ExplorationTarget] = []
        fallback_related: list[ExplorationTarget] = []
        relation_bucket_by_key: dict[tuple[str, str], Literal["primary", "related", "other"]] = {}
        for t in expanded:
            key = self._make_location_key(t.file_path, t.symbol)
            if key in primary_keys:
                primary.append(t)
                relation_bucket_by_key[key] = "primary"
            elif key in related_keys:
                fallback_related.append(t)
                relation_bucket_by_key[key] = "related"
            else:
                relation_bucket_by_key[key] = "other"

        routed = primary if primary else fallback_related
        if not routed:
            return [], relation_bucket_by_key

        # Keep only routed targets as hard direction decision.
        routed_keys = {self._make_location_key(t.file_path, t.symbol) for t in routed}
        relation_bucket_by_key = {
            k: v for k, v in relation_bucket_by_key.items() if k in routed_keys
        }
        if ex_state.expand_direction_hint is not None:
            _LOG.debug(
                "exploration.direction_routing hint=%s routed=%s primary=%s related_fallback=%s",
                hint,
                len(routed),
                len(primary),
                len(fallback_related),
            )
        return routed, relation_bucket_by_key

    def _relation_bucket_for_target(
        self,
        target: ExplorationTarget,
        ex_state: ExplorationState,
        *,
        relation_bucket_by_key: dict[tuple[str, str], Literal["primary", "related", "other"]] | None = None,
    ) -> Literal["primary", "related", "other"]:
        key = self._make_location_key(target.file_path, target.symbol)
        if relation_bucket_by_key and key in relation_bucket_by_key:
            return relation_bucket_by_key[key]
        if target.source != "expansion":
            return "other"
        if ex_state.expand_direction_hint in ("callers", "callees", "both"):
            return "primary"
        return "related"

    def _expand_bucket_keys(self, rows: Any) -> set[tuple[str, str]]:
        out: set[tuple[str, str]] = set()
        if not isinstance(rows, list):
            return out
        for row in rows:
            if not isinstance(row, dict):
                continue
            fp = str(row.get("file_path") or row.get("file") or "").strip()
            if not fp:
                continue
            sym_raw = row.get("symbol")
            sym = str(sym_raw).strip() if sym_raw else ""
            out.add(self._make_location_key(fp, sym if sym else None))
        return out

    @staticmethod
    def _has_unresolved_memory_gaps(memory: ExplorationWorkingMemory) -> bool:
        try:
            summary = memory.get_summary()
            gaps = summary.get("gaps") if isinstance(summary, dict) else []
            return isinstance(gaps, list) and len(gaps) > 0
        except Exception:
            return False

    @staticmethod
    def _intent_signature(
        intent: QueryIntent,
    ) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
        return (
            tuple(sorted({str(x).strip() for x in (intent.symbols or []) if str(x).strip()})),
            tuple(sorted({str(x).strip() for x in (intent.keywords or []) if str(x).strip()})),
            tuple(
                sorted(
                    {
                        str(x).strip()
                        for x in (getattr(intent, "regex_patterns", None) or [])
                        if str(x).strip()
                    }
                )
            ),
            tuple(sorted({str(x).strip() for x in (intent.intents or []) if str(x).strip()})),
        )

    def _intent_oscillation_detected(
        self,
        history: list[tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]]],
        current_intent: QueryIntent,
    ) -> bool:
        if len(history) < 2:
            return False
        current = self._intent_signature(current_intent)
        return history[-1] == history[-2] == current

    @staticmethod
    def _should_stop(
        ex_state: ExplorationState,
        _decision: ExplorationDecision,
    ) -> tuple[bool, str]:
        # Hard limit only; sufficient / relationship completion is handled by EngineDecisionMapper.
        if ex_state.steps_taken >= EXPLORATION_MAX_STEPS:
            return True, "max_steps"
        return False, ""

    @staticmethod
    def _should_stop_pre(ex_state: ExplorationState) -> tuple[bool, str]:
        if ex_state.steps_taken >= EXPLORATION_MAX_STEPS:
            return True, "max_steps"
        return False, ""

    @staticmethod
    def _read_source_for_delta(data: dict | Any) -> str:
        """Maps bounded-read mode to Schema 4 read_source identity (symbol|line|head)."""
        if not isinstance(data, dict):
            return ""
        mode = str(data.get("mode") or "")
        if mode == "symbol_body":
            return "symbol"
        if mode == "line_window":
            return "line"
        if mode == "file_head":
            return "head"
        return ""

    @staticmethod
    def _evidence_delta_key(
        canonical_path: str,
        target: ExplorationTarget,
        data: dict | Any,
    ) -> tuple[str, str, str]:
        sym = str(target.symbol or "").strip()
        return (canonical_path, sym, ExplorationEngineV2._read_source_for_delta(data))

    @staticmethod
    def _is_meaningful_new_evidence(
        seen: set[tuple[str, str, str]],
        key: tuple[str, str, str],
    ) -> bool:
        return key not in seen

    @staticmethod
    def _prioritize_evidence_for_items(
        evidence: list[tuple[str, dict, ExecutionResult]],
    ) -> list[tuple[str, dict, ExecutionResult]]:
        """
        Schema 4 items cap at EXPLORATION_MAX_ITEMS; discovery can flood the list before inspection.
        Surface inspection (bounded reads) and expansion before discovery so planner sees grounded snippets.
        """
        if not evidence:
            return []
        inspection = [e for e in evidence if e[0] == "inspection"]
        expansion = [e for e in evidence if e[0] == "expansion"]
        discovery = [e for e in evidence if e[0] == "discovery"]
        return inspection + expansion + discovery

    def _build_result_from_memory(
        self,
        memory: ExplorationWorkingMemory,
        instruction: str,
        *,
        completion_status: str,
        termination_reason: str,
        explored_files: int,
        explored_symbols: int,
        exploration_outer: Any = None,
        state: Any = None,
        engine_loop_steps: int = 0,
    ) -> FinalExplorationSchema:
        """Planner contract via ExplorationResultAdapter (single mapping path)."""
        final = ExplorationResultAdapter.build(
            memory,
            instruction,
            completion_status=completion_status,
            termination_reason=termination_reason,
            explored_files=explored_files,
            explored_symbols=explored_symbols,
            max_items=EXPLORATION_MAX_ITEMS,
            max_snippet_chars=self.MAX_SNIPPET_CHARS,
            state=state,
            engine_loop_steps=engine_loop_steps,
        )
        if ENABLE_EXPLORATION_RESULT_LLM_SYNTHESIS and self._result_synthesis_llm is not None:
            final = apply_optional_llm_synthesis(
                final,
                memory,
                instruction,
                self._result_synthesis_llm,
                lf_exploration_parent=exploration_outer,
                model_name=self._result_synthesis_model_name,
            )
        self.last_final_exploration = final
        return final

    @staticmethod
    def _top_discovery_score(candidates: list[ExplorationCandidate]) -> float:
        best = 0.0
        for c in candidates or []:
            try:
                best = max(best, float(getattr(c, "discovery_max_score", 0.0) or 0.0))
            except Exception:
                continue
        return best

    @staticmethod
    def _has_retry_improvement(
        *,
        old_candidates: list[ExplorationCandidate],
        new_candidates: list[ExplorationCandidate],
        old_top: float,
        new_top: float,
    ) -> bool:
        return (len(new_candidates or []) > len(old_candidates or [])) or (new_top > old_top)

    @staticmethod
    def _emit_query_retry_telemetry(
        *,
        exploration_outer: Any,
        original_queries: QueryIntent,
        refined_queries: QueryIntent,
        failure_reason: FailureReason | str,
        original_candidate_count: int,
        refined_candidate_count: int,
        original_top_score: float,
        refined_top_score: float,
        improved: bool,
    ) -> None:
        if exploration_outer is None or not hasattr(exploration_outer, "event"):
            return
        try:
            exploration_outer.event(
                name="exploration.query_refinement",
                metadata={
                    "failure_reason": str(failure_reason),
                    "original_queries": original_queries.model_dump(),
                    "refined_queries": refined_queries.model_dump(),
                    "improved": bool(improved),
                    "improvement_delta": {
                        "candidate_count": int(refined_candidate_count - original_candidate_count),
                        "top_score": float(refined_top_score - original_top_score),
                    },
                },
            )
        except Exception:
            pass

    @staticmethod
    def _classify_initial_refinement_reason(
        intent: QueryIntent,
        candidates: list[ExplorationCandidate],
        top_score: float,
    ) -> FailureReason | None:
        """
        Classify whether initial discovery quality warrants one query-intent refinement retry.

        NOTE: This is a retrieval-quality signal, not parser execution failure.
        """
        if not candidates:
            if not intent.symbols:
                return "missing_symbol_signal"
            if len(intent.symbols) >= 3 and len(intent.keywords) <= 2:
                return "too_narrow"
            if len(intent.keywords) >= 6 and len(intent.symbols) == 0:
                return "too_broad"
            return "no_results"
        threshold = max(0.0, min(1.0, EXPLORATION_RETRY_LOW_RELEVANCE_THRESHOLD / 100.0))
        if top_score < threshold:
            if len(intent.symbols) == 0:
                return "missing_symbol_signal"
            if len(intent.keywords) >= 7 and len(intent.symbols) <= 1:
                return "too_broad"
            if len(intent.symbols) >= 4 and len(intent.keywords) <= 2:
                return "too_narrow"
            return "low_relevance"
        return None

    @staticmethod
    def _classify_initial_failure_reason(
        intent: QueryIntent,
        candidates: list[ExplorationCandidate],
        top_score: float,
    ) -> FailureReason | None:
        """Backward-compatible alias for older call sites/telemetry wording."""
        return ExplorationEngineV2._classify_initial_refinement_reason(
            intent,
            candidates,
            top_score,
        )

    def _item_snippet_and_source(
        self, phase: str, result: ExecutionResult
    ) -> tuple[str, str | None]:
        """
        Phase 12.6.E:
        - Populate ExplorationItem.snippet + read_source with FACTS only.
        - Only inspection (bounded read) yields snippets.
        """
        if phase != "inspection":
            return "", None
        data = result.output.data if result.output else {}
        if not isinstance(data, dict):
            return "", None
        raw = data.get("content") or ""
        if not isinstance(raw, str):
            raw = ""
        mode = str(data.get("mode") or "")
        if mode == "symbol_body":
            rs = "symbol"
        elif mode == "line_window":
            rs = "line"
        elif mode == "file_head":
            rs = "head"
        else:
            rs = None
        return raw[: self.MAX_SNIPPET_CHARS], rs

    def _build_context_blocks_for_analysis(
        self,
        intent: QueryIntent,
        packets: list[ReadPacket],
    ) -> tuple[list, dict[str, Any]]:
        groups = self._slice_grouper.group(packets)
        packet_count = sum(len(g) for g in groups)
        max_line_count = max((p.line_count for p in packets), default=0)
        unique_symbols = {
            (p.symbol or "").strip()
            for p in packets
            if (p.symbol or "").strip()
        }
        symbol_count = len(unique_symbols)
        intent_count = len([x for x in (intent.intents or []) if str(x).strip()])
        intent_symbol_count = len([x for x in (intent.symbols or []) if str(x).strip()])

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
        if len(groups) > 1:
            score += 1.0
        if intent_count >= 3:
            score += 1.0
        elif intent_count == 2:
            score += 0.5
        if intent_symbol_count >= 4:
            score += 1.0
        elif intent_symbol_count >= 2:
            score += 0.5

        if score >= 3.0:
            complexity_bucket = "high"
        elif score >= 1.5:
            complexity_bucket = "medium"
        else:
            complexity_bucket = "low"

        # Avoid extractor false positives for simple single-slice tasks.
        use_inspector = complexity_bucket == "high" or (
            complexity_bucket == "medium"
            and (packet_count > 1 or max_line_count > EXPLORATION_ROUTING_COMPLEX_MAX_LINES or symbol_count >= 3)
        )
        inspector_skipped_reason = None
        if not use_inspector:
            if max_line_count <= EXPLORATION_ROUTING_SIMPLE_MAX_LINES:
                inspector_skipped_reason = "small_input"
            elif complexity_bucket == "low":
                inspector_skipped_reason = "low_complexity"
            else:
                inspector_skipped_reason = "low_noise"

        if use_inspector and groups:
            signals = self._inspector.inspect(groups[0], max_ranges=EXPLORATION_CONTEXT_TOP_K_RANGES)
            fetched = self._fetcher.fetch(
                groups[0],
                signals,
                top_k_ranges=EXPLORATION_CONTEXT_TOP_K_RANGES,
                max_total_lines=EXPLORATION_CONTEXT_MAX_TOTAL_LINES,
            )
            blocks = self._context_block_builder.finalize(
                fetched,
                max_total_lines=EXPLORATION_CONTEXT_MAX_TOTAL_LINES,
            )
            routing_path = "complex_path"
            routing_reason = "multi_slice_or_complex"
        else:
            blocks = self._context_block_builder.from_packets(
                packets,
                max_total_lines=EXPLORATION_CONTEXT_MAX_TOTAL_LINES,
            )
            routing_path = "simple_path"
            routing_reason = inspector_skipped_reason or "simple_input"

        telemetry = {
            "routing_path": routing_path,
            "routing_reason": routing_reason,
            "complexity_signal": complexity_bucket,
            "group_count": len(groups),
            "inspector_used": bool(use_inspector),
            "inspector_skipped_reason": inspector_skipped_reason,
            "complexity_score": score,
        }
        return blocks, telemetry
