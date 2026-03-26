from __future__ import annotations

import json
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Literal
from pathlib import Path

from agent_v2.config import (
    DISCOVERY_MERGE_TOP_K,
    DISCOVERY_REGEX_CAP,
    DISCOVERY_SYMBOL_CAP,
    DISCOVERY_TEXT_CAP,
    EXPLORATION_MAX_QUERY_RETRIES,
    EXPLORATION_EXPAND_MAX_DEPTH,
    EXPLORATION_EXPAND_MAX_NODES,
    EXPLORATION_MAX_BACKTRACKS,
    EXPLORATION_MAX_ITEMS,
    EXPLORATION_MAX_STEPS,
    EXPLORATION_READ_WINDOW,
    EXPLORATION_SCOPER_K,
    EXPLORATION_SCOPER_SKIP_BELOW,
    EXPLORATION_STAGNATION_STEPS,
    EXPLORATION_RETRY_LOW_RELEVANCE_THRESHOLD,
    EXPLORATION_ROUTING_SIMPLE_MAX_LINES,
    EXPLORATION_ROUTING_COMPLEX_MAX_LINES,
    EXPLORATION_CONTEXT_MAX_TOTAL_LINES,
    EXPLORATION_CONTEXT_TOP_K_RANGES,
    get_project_root,
)
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
from agent_v2.exploration.understanding_analyzer import UnderstandingAnalyzer
from agent_v2.schemas.execution import ExecutionResult
from agent_v2.schemas.exploration import (
    ExplorationCandidate,
    ExplorationContent,
    ExplorationDecision,
    ExplorationItem,
    ExplorationItemMetadata,
    ExplorationRelevance,
    ExplorationResult,
    ExplorationResultMetadata,
    ExplorationSource,
    ExplorationState,
    ExplorationSummary,
    ExplorationTarget,
    ReadPacket,
    QueryIntent,
    FailureReason,
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

    def explore(
        self,
        instruction: str,
        *,
        state: Any,
        obs: Any = None,
        langfuse_trace: Any = None,
    ) -> ExplorationResult:
        lf = langfuse_trace
        if lf is None and obs is not None:
            lf = getattr(obs, "langfuse_trace", None)
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
    ) -> ExplorationResult:
        ex_state = ExplorationState(instruction=instruction)
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

        candidates, discovery_records, discovery_ms = self._run_discovery_traced(
            exploration_outer,
            "initial",
            intent,
            state,
            ex_state,
        )
        evidence.extend(discovery_records)
        retry_attempts = 0
        top_score_initial = self._top_discovery_score(candidates)
        initial_failure_reason = self._classify_initial_failure_reason(intent, candidates, top_score_initial)
        if initial_failure_reason and retry_attempts < EXPLORATION_MAX_QUERY_RETRIES:
            retry_attempts += 1
            prev_queries = intent
            refined_intent_span: Any = None
            if exploration_outer is not None and hasattr(exploration_outer, "span"):
                try:
                    refined_intent_span = exploration_outer.span(
                        "exploration.query_intent.retry",
                        input={
                            "failure_reason": initial_failure_reason,
                            "retry_attempt": retry_attempts,
                        },
                    )
                except Exception:
                    refined_intent_span = None
            try:
                refined_intent = self._intent_parser.parse(
                    instruction,
                    previous_queries=prev_queries,
                    failure_reason=initial_failure_reason,
                    lf_exploration_parent=exploration_outer,
                    lf_intent_span=refined_intent_span,
                )
            finally:
                _lf_end(refined_intent_span)
            refined_candidates, refined_discovery_records, _ = self._run_discovery_traced(
                exploration_outer,
                "retry",
                refined_intent,
                state,
                ex_state,
            )
            evidence.extend(refined_discovery_records)
            top_score_refined = self._top_discovery_score(refined_candidates)
            original_count = len(candidates)
            improved = self._has_retry_improvement(
                old_candidates=candidates,
                new_candidates=refined_candidates,
                old_top=top_score_initial,
                new_top=top_score_refined,
            )
            if improved:
                intent = refined_intent
                candidates = refined_candidates
            self._emit_query_retry_telemetry(
                exploration_outer=exploration_outer,
                original_queries=prev_queries,
                refined_queries=refined_intent,
                failure_reason=initial_failure_reason,
                original_candidate_count=original_count,
                refined_candidate_count=len(refined_candidates),
                original_top_score=top_score_initial,
                refined_top_score=top_score_refined,
                improved=improved,
            )
        t_enq0 = time.perf_counter()
        selection_none = self._enqueue_ranked(
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

            pre_stop, pre_stop_reason = self._should_stop_pre(
                ex_state,
                primary_symbol_body_seen=primary_symbol_body_seen,
            )
            if pre_stop:
                termination_reason = pre_stop_reason
                break

            ex_state.steps_taken += 1

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

            post_inspect_stop, post_inspect_reason = self._should_stop_pre(
                ex_state,
                primary_symbol_body_seen=primary_symbol_body_seen,
            )
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
                try:
                    context_blocks, routing_meta = self._build_context_blocks_for_analysis(
                        intent,
                        [read_packet],
                    )
                    if exploration_outer is not None and hasattr(exploration_outer, "event"):
                        try:
                            exploration_outer.event(
                                name="exploration.routing",
                                metadata=routing_meta,
                            )
                        except Exception:
                            pass
                    understanding = self._analyzer.analyze(
                        instruction,
                        intent=", ".join([s for s in (intent.intents or []) if str(s).strip()]) or "no intent",
                        context_blocks=context_blocks,
                        lf_analyze_span=analyze_span,
                        lf_exploration_parent=exploration_outer,
                    )
                    decision = self._decision_mapper.to_exploration_decision(understanding)
                finally:
                    if obs is not None:
                        obs.current_span = None
                    _lf_end(analyze_span)
                ex_state.last_decision = decision.status
                if decision.wrong_target_scope == "file":
                    ex_state.excluded_paths.add(canon)
            else:
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

            stop, stop_reason = self._should_stop(
                ex_state,
                decision,
                primary_symbol_body_seen=primary_symbol_body_seen,
            )
            if stop:
                termination_reason = stop_reason
                break

            action = self._next_action(decision)
            if self._should_expand(action, decision, target, ex_state):
                expand_span: Any = None
                if exploration_outer is not None and hasattr(exploration_outer, "span"):
                    try:
                        expand_span = exploration_outer.span(
                            "exploration.expand",
                            input={
                                "symbol": (target.symbol or "")[:500],
                                "file_path": str(target.file_path)[:1000],
                            },
                        )
                    except Exception:
                        expand_span = None
                try:
                    expanded, expand_result = self._graph_expander.expand(
                        target.symbol or "",
                        target.file_path,
                        state,
                        max_nodes=EXPLORATION_EXPAND_MAX_NODES,
                        max_depth=EXPLORATION_EXPAND_MAX_DEPTH,
                    )
                except Exception as exc:
                    lf_span_end_output(
                        expand_span,
                        output={"tool": "expand", "error": str(exc)[:2000]},
                    )
                    raise
                lf_span_end_output(
                    expand_span,
                    output=_expand_tool_langfuse_output(expanded, expand_result),
                )
                evidence.append(("expansion", {"symbol": target.symbol or ""}, expand_result))
                if expanded:
                    ex_state.relationships_found = True
                    self._enqueue_targets(ex_state, expanded)
                continue

            if self._should_refine(action, decision, ex_state):
                ex_state.backtracks += 1
                candidates, discovery_records, _ = self._run_discovery_traced(
                    exploration_outer,
                    "refine",
                    intent,
                    state,
                    ex_state,
                )
                evidence.extend(discovery_records)
                selection_none = self._enqueue_ranked(
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
        return self._build_result(
            instruction,
            evidence,
            completion_status=completion_status,
            termination_reason=termination_reason,
            explored_files=len(ex_state.seen_files),
            explored_symbols=len(ex_state.seen_symbols),
        )

    def _run_discovery_traced(
        self,
        exploration_outer: Any,
        phase: str,
        intent: QueryIntent,
        state: Any,
        ex_state: ExplorationState,
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
            candidates, discovery_records = self._discovery(intent, state, ex_state)
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

    def _discovery(
        self,
        intent: QueryIntent,
        state: Any,
        ex_state: ExplorationState,
    ) -> tuple[list[ExplorationCandidate], list[tuple[str, dict, ExecutionResult]]]:
        records: list[tuple[str, dict, ExecutionResult]] = []
        base_root = get_project_root()

        symbol_queries = list(dict.fromkeys(intent.symbols))[:DISCOVERY_SYMBOL_CAP]
        text_queries = list(dict.fromkeys(intent.keywords))[:DISCOVERY_TEXT_CAP]
        regex_src = getattr(intent, "regex_patterns", None)
        if not isinstance(regex_src, list):
            regex_src = []
        regex_queries = list(dict.fromkeys(str(x) for x in regex_src if str(x).strip()))[
            :DISCOVERY_REGEX_CAP
        ]

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
                max_workers=4,
            )
            return list(zip(queries, results))

        with ThreadPoolExecutor(max_workers=3) as outer:
            f_sym = outer.submit(_collect_pairs, "symbol", symbol_queries)
            f_reg = outer.submit(_collect_pairs, "regex", regex_queries)
            f_txt = outer.submit(_collect_pairs, "text", text_queries)
            sym_pairs = f_sym.result()
            reg_pairs = f_reg.result()
            txt_pairs = f_txt.result()

        # merge[(canon_path, sym_dedupe_key)] -> { max_score, candidate, breakdown }
        merge: dict[tuple[str, str], dict[str, Any]] = {}

        def _ingest_pairs(
            pairs: list[tuple[str, ExecutionResult]],
            query_type: Literal["symbol", "regex", "text"],
        ) -> None:
            src_lit = self._discovery_query_channel_to_source(query_type)
            ch = query_type  # breakdown channel key
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
                    sym_dedupe = sym if sym else "__file__"
                    key = (canon, sym_dedupe)
                    sc = self._discovery_row_score(row)
                    snip = row.get("snippet") or row.get("content")
                    snip_s = str(snip).strip() if snip else None
                    cand = ExplorationCandidate(
                        symbol=sym,
                        file_path=canon,
                        snippet=snip_s,
                        source=src_lit,
                    )
                    if key not in merge:
                        merge[key] = {
                            "max_score": sc,
                            "candidate": cand,
                            "breakdown": {"symbol": None, "regex": None, "text": None},
                        }
                        merge[key]["breakdown"][ch] = sc
                    else:
                        m = merge[key]
                        prev_ch = m["breakdown"].get(ch)
                        m["breakdown"][ch] = max(
                            prev_ch if prev_ch is not None else 0.0,
                            sc,
                        )
                        if sc > m["max_score"]:
                            m["max_score"] = sc
                            m["candidate"] = cand

        _ingest_pairs(sym_pairs, "symbol")
        _ingest_pairs(reg_pairs, "regex")
        _ingest_pairs(txt_pairs, "text")

        sorted_entries = sorted(
            merge.items(),
            key=lambda kv: kv[1]["max_score"],
            reverse=True,
        )[:DISCOVERY_MERGE_TOP_K]

        deduped: list[ExplorationCandidate] = []
        for _k, meta in sorted_entries:
            c = meta["candidate"]
            bd = dict(meta["breakdown"])
            ms = float(meta["max_score"])
            try:
                object.__setattr__(c, "_score_breakdown", bd)
                object.__setattr__(c, "_discovery_max_score", ms)
            except Exception:
                pass
            if self._may_enqueue(ex_state, c.file_path, c.symbol):
                deduped.append(c)

        _LOG.info(
            "exploration.discovery steps_taken=%s budget_symbol=%s regex=%s text=%s "
            "merged_keys=%s evidence_records=%s candidates_after_may_enqueue=%s",
            ex_state.steps_taken,
            len(symbol_queries),
            len(regex_queries),
            len(text_queries),
            len(merge),
            len(records),
            len(deduped),
        )
        return deduped, records

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
    ) -> bool:
        if not candidates:
            return False
        candidates = [c for c in candidates if self._may_enqueue(ex_state, c.file_path, c.symbol)]
        if not candidates:
            return True
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
                scoped = self._scoper.scope(
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

        select_span: Any = None
        if expl_parent is not None and hasattr(expl_parent, "span"):
            try:
                select_span = expl_parent.span("exploration.select", input={"phase": "select"})
                if obs is not None:
                    obs.current_span = select_span
            except Exception:
                select_span = None
        try:
            local_seen_files = set(ex_state.seen_files)
            intent_text = ", ".join([s for s in (intent.intents or []) if str(s).strip()]) or "no intent"
            ranked = self._selector.select_batch(
                instruction,
                intent_text,
                scoped,
                local_seen_files,
                limit=min(limit, len(scoped)),
                explored_location_keys=ex_state.explored_location_keys,
                lf_select_span=select_span,
                lf_exploration_parent=expl_parent,
            )
        finally:
            if obs is not None:
                obs.current_span = None
            _lf_end(select_span)
        if ranked is None:
            return True
        targets = [
            ExplorationTarget(
                file_path=str(c.file_path),
                symbol=c.symbol,
                source="discovery",
            )
            for c in (ranked or [])
        ]
        self._enqueue_targets(ex_state, targets)
        return False

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

    def _enqueue_targets(self, ex_state: ExplorationState, targets: list[ExplorationTarget]) -> None:
        for target in targets:
            if not self._may_enqueue(ex_state, target.file_path, target.symbol):
                continue
            key = self._make_location_key(target.file_path, target.symbol)
            canon = key[0]
            ex_state.pending_targets.append(target.model_copy(update={"file_path": canon}))

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
        if target.symbol in ex_state.expanded_symbols:
            return False
        if not ({"callers", "callees"} & set(decision.needs) or decision.status == "partial"):
            return False
        ex_state.expanded_symbols.add(target.symbol)
        return True

    @staticmethod
    def _should_refine(action: str, decision: ExplorationDecision, ex_state: ExplorationState) -> bool:
        if ex_state.backtracks >= EXPLORATION_MAX_BACKTRACKS:
            return False
        if action == "refine":
            return True
        return decision.status == "wrong_target"

    @staticmethod
    def _should_stop(
        ex_state: ExplorationState,
        decision: ExplorationDecision,
        *,
        primary_symbol_body_seen: bool = False,
    ) -> tuple[bool, str]:
        if ex_state.steps_taken >= EXPLORATION_MAX_STEPS:
            return True, "max_steps"
        if (
            ex_state.primary_symbol
            and primary_symbol_body_seen
            and decision.status == "sufficient"
        ):
            return True, "primary_symbol_sufficient"
        if (
            ex_state.primary_symbol
            and ex_state.relationships_found
            and decision.status == "sufficient"
        ):
            return True, "relationships_satisfied"
        return False, ""

    @staticmethod
    def _should_stop_pre(
        ex_state: ExplorationState,
        *,
        primary_symbol_body_seen: bool = False,
    ) -> tuple[bool, str]:
        if ex_state.steps_taken >= EXPLORATION_MAX_STEPS:
            return True, "max_steps"
        if (
            ex_state.primary_symbol
            and primary_symbol_body_seen
            and ex_state.last_decision == "sufficient"
        ):
            return True, "primary_symbol_sufficient"
        if (
            ex_state.primary_symbol
            and ex_state.relationships_found
            and ex_state.last_decision == "sufficient"
        ):
            return True, "relationships_satisfied"
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

    def _build_result(
        self,
        instruction: str,
        evidence: list[tuple[str, dict, ExecutionResult]],
        *,
        completion_status: str,
        termination_reason: str,
        explored_files: int,
        explored_symbols: int,
    ) -> ExplorationResult:
        items: list[ExplorationItem] = []
        ordered = self._prioritize_evidence_for_items(evidence)[:EXPLORATION_MAX_ITEMS]
        for idx, (phase, payload, result) in enumerate(ordered, start=1):
            ref = payload.get("path") or payload.get("query") or payload.get("symbol") or "unknown"
            summary = (result.output.summary if result.output else "")[:600]
            if not summary.strip():
                summary = f"{phase} completed"
            key_points = [summary]
            score = 0.8 if result.success else 0.3
            item_type = "file" if phase == "inspection" else "search"

            snippet, read_source = self._item_snippet_and_source(phase, result)
            items.append(
                ExplorationItem(
                    item_id=f"item_{idx}",
                    type=item_type,
                    source=ExplorationSource(ref=str(ref), location=None),
                    content=ExplorationContent(summary=summary, key_points=key_points, entities=[str(ref)]),
                    relevance=ExplorationRelevance(score=score, reason=f"{phase} {'ok' if result.success else 'failed'}"),
                    metadata=ExplorationItemMetadata(
                        timestamp=result.metadata.timestamp,
                        tool_name=result.metadata.tool_name,
                    ),
                    snippet=snippet,
                    read_source=read_source,
                )
            )

        key_findings = [it.content.summary for it in items[:3]]
        if items:
            summary = ExplorationSummary(
                overall=f"Exploration v2 gathered {len(items)} evidence items for instruction.",
                key_findings=key_findings,
                knowledge_gaps=["Potentially missing deeper call-chain context"],
                knowledge_gaps_empty_reason=None,
            )
        else:
            summary = ExplorationSummary(
                overall="Exploration v2 did not gather evidence.",
                key_findings=[],
                knowledge_gaps=[],
                knowledge_gaps_empty_reason="No candidates discovered from instruction intent.",
            )

        return ExplorationResult(
            exploration_id=f"exp_{uuid.uuid4().hex[:8]}",
            instruction=instruction,
            items=items,
            summary=summary,
            metadata=ExplorationResultMetadata(
                total_items=len(items),
                created_at=datetime.now(timezone.utc).isoformat(),
                completion_status=("complete" if completion_status == "complete" else "incomplete"),
                termination_reason=termination_reason,
                explored_files=explored_files,
                explored_symbols=explored_symbols,
                source_summary=self._source_summary_from_items(items),
            ),
        )

    @staticmethod
    def _top_discovery_score(candidates: list[ExplorationCandidate]) -> float:
        best = 0.0
        for c in candidates or []:
            try:
                best = max(best, float(getattr(c, "_discovery_max_score", 0.0) or 0.0))
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
    def _classify_initial_failure_reason(
        intent: QueryIntent,
        candidates: list[ExplorationCandidate],
        top_score: float,
    ) -> FailureReason | None:
        if not (intent.symbols or intent.keywords or intent.regex_patterns):
            return "ambiguous_intent"
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

    @staticmethod
    def _source_summary_from_items(items: list[ExplorationItem]) -> dict[str, int]:
        counts = {"symbol": 0, "line": 0, "head": 0}
        for it in items:
            rs = getattr(it, "read_source", None)
            if rs in counts:
                counts[rs] += 1
        return counts

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
