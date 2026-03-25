from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any
import os
from pathlib import Path

from agent_v2.config import (
    EXPLORATION_MAX_BACKTRACKS,
    EXPLORATION_MAX_ITEMS,
    EXPLORATION_MAX_STEPS,
    EXPLORATION_SCOPER_K,
    EXPLORATION_SCOPER_SKIP_BELOW,
    EXPLORATION_STAGNATION_STEPS,
)
from agent_v2.exploration.candidate_selector import CandidateSelector
from agent_v2.exploration.exploration_scoper import ExplorationScoper
from agent_v2.exploration.graph_expander import GraphExpander
from agent_v2.exploration.inspection_reader import InspectionReader
from agent_v2.exploration.query_intent_parser import QueryIntentParser
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
    QueryIntent,
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
    ):
        self._dispatcher = dispatcher
        self._intent_parser = intent_parser
        self._selector = selector
        self._inspection_reader = inspection_reader
        self._analyzer = analyzer
        self._graph_expander = graph_expander
        self._scoper = scoper

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
        base_root = os.environ.get("SERENA_PROJECT_DIR") or os.getcwd()
        seen_targets: set[tuple[str, str]] = set()  # Phase 12.6.R4 structural dedup (canon_path, symbol)
        primary_symbol_body_seen = False  # SYSTEM ONLY: fact that we read the symbol body (bounded)
        # System-level evidence delta: (canonical_path, symbol, read_source) — no scoring, identity only
        evidence_keys_seen: set[tuple[str, str, str]] = set()
        stagnation_counter = 0

        intent = self._intent_parser.parse(instruction)
        t_disc0 = time.perf_counter()
        candidates, discovery_records = self._discovery(intent, state, ex_state)
        discovery_ms = int((time.perf_counter() - t_disc0) * 1000)
        evidence.extend(discovery_records)
        t_enq0 = time.perf_counter()
        selection_none = self._enqueue_ranked(
            instruction,
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
            canon = self._canonical_path(target.file_path, base_root=base_root)
            key = (canon, str(target.symbol or "").strip())
            if key in seen_targets:
                stagnation_counter += 1
                if stagnation_counter >= EXPLORATION_STAGNATION_STEPS:
                    termination_reason = "stalled"
                    break
                continue
            seen_targets.add(key)
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
                snippet, inspect_result = self._inspection_reader.inspect(
                    selected,
                    symbol=target.symbol,
                    line=target.line,
                    window=80,
                    state=state,
                )
            finally:
                if obs is not None:
                    obs.current_span = None
                if inspect_span is not None and inspect_result is not None:
                    try:
                        inspect_span.end(
                            output=_exploration_inspect_langfuse_output(snippet, inspect_result)
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
                    decision = self._analyzer.analyze(
                        instruction,
                        target.file_path,
                        snippet,
                        lf_analyze_span=analyze_span,
                        lf_exploration_parent=exploration_outer,
                    )
                finally:
                    if obs is not None:
                        obs.current_span = None
                    _lf_end(analyze_span)
                ex_state.last_decision = decision.status
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
                expanded, expand_result = self._graph_expander.expand(
                    target.symbol or "",
                    target.file_path,
                    state,
                    max_nodes=10,
                    max_depth=1,
                )
                evidence.append(("expansion", {"symbol": target.symbol or ""}, expand_result))
                if expanded:
                    ex_state.relationships_found = True
                    self._enqueue_targets(ex_state, expanded)
                continue

            if self._should_refine(action, decision, ex_state):
                ex_state.backtracks += 1
                candidates, discovery_records = self._discovery(intent, state, ex_state)
                evidence.extend(discovery_records)
                selection_none = self._enqueue_ranked(
                    instruction,
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

    def _discovery(
        self,
        intent: QueryIntent,
        state: Any,
        ex_state: ExplorationState,
    ) -> tuple[list[ExplorationCandidate], list[tuple[str, dict, ExecutionResult]]]:
        records: list[tuple[str, dict, ExecutionResult]] = []
        candidates: list[ExplorationCandidate] = []
        queries = list(dict.fromkeys(intent.symbols + intent.keywords))[:8]
        base_root = os.environ.get("SERENA_PROJECT_DIR") or os.getcwd()
        for token in queries:
            step = {
                "id": f"discovery_{ex_state.steps_taken}_{token}",
                "action": "SEARCH",
                "_react_action_raw": "search",
                "_react_args": {"query": token},
                "query": token,
                "description": token,
            }
            result = self._dispatcher.execute(step, state)
            records.append(("discovery", {"query": token}, result))
            data = result.output.data if result.output else {}
            candidates.extend(self._to_candidates(data.get("results") or data.get("candidates") or [], "search"))
        # Structural dedup correctness: normalize paths before dedup keying.
        normalized: list[ExplorationCandidate] = []
        for c in candidates:
            normalized.append(
                c.model_copy(
                    update={
                        "file_path": self._canonical_path(c.file_path, base_root=base_root),
                    }
                )
            )
        deduped = self._merge_candidates([], normalized)
        return deduped, records

    def _enqueue_ranked(
        self,
        instruction: str,
        candidates: list[ExplorationCandidate],
        ex_state: ExplorationState,
        *,
        limit: int,
        expl_parent: Any = None,
        obs: Any = None,
    ) -> bool:
        if not candidates:
            return False
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
            ranked = self._selector.select_batch(
                instruction,
                scoped,
                local_seen_files,
                limit=min(limit, len(scoped)),
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
        base_root = os.environ.get("SERENA_PROJECT_DIR") or os.getcwd()
        existing = {
            (
                self._canonical_path((t.file_path or "").strip(), base_root=base_root),
                (t.symbol or "").strip(),
            )
            for t in ex_state.pending_targets
        }
        for target in targets:
            canon = self._canonical_path((target.file_path or "").strip(), base_root=base_root)
            key = (canon, (target.symbol or "").strip())
            if key in existing:
                continue
            existing.add(key)
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
