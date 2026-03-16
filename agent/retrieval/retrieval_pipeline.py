"""Retrieval pipeline: anchor detection → expand → read → find_references → build_context. Dispatcher calls this only."""

import logging
import os
import time
from pathlib import Path

from agent.memory.state import AgentState
from agent.retrieval.anchor_detector import detect_anchors
from agent.retrieval.context_builder import build_context_from_symbols
from agent.retrieval.context_pruner import prune_context
from agent.retrieval.context_ranker import rank_context
from agent.retrieval.retrieval_expander import expand_search_results
from agent.retrieval.reranker.cache import cache_stats
from agent.retrieval.reranker.deduplicator import deduplicate_candidates
from agent.retrieval.reranker.reranker_factory import create_reranker
from agent.retrieval.reranker.symbol_query_detector import is_symbol_query
from agent.retrieval.symbol_expander import expand_from_anchors
from agent.tools import find_referencing_symbols, read_file, read_symbol_body
from config.agent_config import MAX_RETRIEVAL_RESULTS
from config.repo_graph_config import INDEX_SQLITE, SYMBOL_GRAPH_DIR
from config.retrieval_config import (
    DEFAULT_MAX_CHARS,
    ENABLE_CONTEXT_RANKING,
    ENABLE_LOCALIZATION_ENGINE,
    MAX_CONTEXT_SNIPPETS,
    MAX_RERANK_CANDIDATES,
    MAX_SEARCH_RESULTS,
    RERANK_FUSION_WEIGHT,
    RERANK_MIN_CANDIDATES,
    RERANKER_CPU_MODEL,
    RERANKER_ENABLED,
    RERANKER_GPU_MODEL,
    RERANKER_TOP_K,
    RETRIEVER_FUSION_WEIGHT,
)

logger = logging.getLogger(__name__)


def _apply_reranker_scores(
    candidates: list[dict],
    scored: list[tuple[str, float]],
    top_k: int,
) -> list[dict]:
    """Merge reranker scores with retriever scores via weighted fusion and slice to top_k."""
    score_map = {doc: score for doc, score in scored}
    for c in candidates:
        reranker_score = score_map.get(c.get("snippet") or "", 0.0)
        retriever_score = float(c.get("retriever_score") or 0.0)
        c["final_score"] = (
            reranker_score * RERANK_FUSION_WEIGHT
            + retriever_score * RETRIEVER_FUSION_WEIGHT
        )
    candidates.sort(key=lambda c: c.get("final_score", 0.0), reverse=True)
    return candidates[:top_k]


def _compute_rerank_impact(before: list[dict], after: list[dict]) -> dict:
    """Compute position change metrics between pre- and post-rerank order."""
    def _key(c: dict) -> str:
        return f"{c.get('file', '')}|{c.get('symbol', '')}|{c.get('snippet', '')[:100]}"

    before_order = {_key(c): i for i, c in enumerate(before)}
    after_order = {_key(c): i for i, c in enumerate(after)}
    shifts = []
    position_changes = 0
    for c in after:
        k = _key(c)
        if k in before_order:
            b_rank = before_order[k]
            a_rank = after_order.get(k, b_rank)
            shift = abs(a_rank - b_rank)
            shifts.append(shift)
            if shift > 0:
                position_changes += 1
    top1_changed = 1 if (before and after and _key(before[0]) != _key(after[0])) else 0
    avg_shift = sum(shifts) / len(shifts) if shifts else 0.0
    return {
        "rerank_position_changes": position_changes,
        "rerank_avg_rank_shift": round(avg_shift, 2),
        "rerank_top1_changed": top1_changed,
    }


def _log_rerank_telemetry(
    state: AgentState,
    rerank_ms: int,
    device: str,
    candidates_in: int,
    candidates_after_dedup: int,
    candidates_out: int,
    total_tokens: int,
    skipped_reason: str | None,
    impact: dict | None = None,
) -> None:
    """Emit rerank metrics into state.context['retrieval_metrics']."""
    stats = cache_stats()
    metrics = state.context.get("retrieval_metrics") or {}
    metrics.update({
        "rerank_latency_ms": rerank_ms,
        "rerank_model": RERANKER_GPU_MODEL if device == "gpu" else RERANKER_CPU_MODEL,
        "rerank_device": device,
        "candidates_in": candidates_in,
        "candidates_out": candidates_out,
        "rerank_dedup_removed": candidates_in - candidates_after_dedup,
        "rerank_cache_hits": stats["hits"],
        "rerank_cache_misses": stats["misses"],
        "rerank_tokens": total_tokens,
        "rerank_batch_size": int(os.getenv("RERANKER_BATCH_SIZE", "16")),
        "rerank_skipped_reason": skipped_reason,
    })
    if impact:
        metrics.update(impact)
    state.context["retrieval_metrics"] = metrics


def _resolve_path(path: str, project_root: str | None) -> str:
    """Resolve path to absolute; use project_root when path is relative."""
    if not path:
        return path
    p = Path(path)
    if not p.is_absolute() and project_root:
        p = Path(project_root) / path
    return str(p.resolve())


def _build_candidates_from_context(built: dict) -> list[dict]:
    """Build ranker candidates from context_builder output. Snippets are {file, symbol, snippet}."""
    candidates: list[dict] = []
    for s in built.get("symbols") or []:
        if isinstance(s, dict):
            candidates.append({
                "file": s.get("file") or "",
                "symbol": s.get("symbol") or "",
                "snippet": s.get("snippet") or "",
                "type": "symbol",
                **({"line": s["line"]} if s.get("line") is not None else {}),
            })
    for r in built.get("references") or []:
        if isinstance(r, dict):
            snippet = r.get("snippet") or f"{r.get('symbol', '')} at line {r.get('line', '?')}"
            candidates.append({
                "file": r.get("file") or "",
                "symbol": r.get("symbol") or "",
                "snippet": snippet,
                "type": "reference",
                **({"line": r["line"]} if r.get("line") is not None else {}),
            })
    for snip in built.get("snippets") or []:
        if isinstance(snip, dict):
            candidates.append({
                "file": snip.get("file") or "",
                "symbol": snip.get("symbol") or "",
                "snippet": snip.get("snippet") or "",
                "type": "file",
            })
        elif isinstance(snip, str) and snip:
            candidates.append({"file": "", "symbol": "", "snippet": snip, "type": "file"})
    return candidates


def run_retrieval_pipeline(
    search_results: list[dict],
    state: AgentState,
    query: str | None = None,
) -> dict:
    """
    Anchor detection → expand → read_symbol_body/read_file → find_referencing_symbols → build_context.
    Updates state.context (retrieved_*, context_snippets as list of {file, symbol, snippet}, ranked_context).
    Returns aggregated result for the SEARCH step.
    """
    results = (search_results or [])[:MAX_SEARCH_RESULTS]
    if not results:
        return {"results": [], "query": query or "", "anchors": 0}

    anchors = detect_anchors(results, query)
    if not anchors:
        return {"results": results, "query": query or "", "anchors": 0}

    project_root = state.context.get("project_root") or os.environ.get("SERENA_PROJECT_DIR") or os.getcwd()

    localization_candidates: list[dict] = []
    if ENABLE_LOCALIZATION_ENGINE and anchors:
        from agent.retrieval.localization.localization_engine import localize_issue

        trace_id = state.context.get("trace_id") or ""
        localization_candidates = localize_issue(
            query or "", anchors, project_root, trace_id=trace_id
        )
        state.context["localization_candidates"] = localization_candidates

    # Initialize retrieval metrics for graph telemetry, dedupe, budget
    retrieval_metrics: dict = state.context.get("retrieval_metrics") or {}

    # Ext Step 4: Graph index fallback — skip graph expansion when index absent
    index_path = Path(project_root) / SYMBOL_GRAPH_DIR / INDEX_SQLITE
    graph_stage_skipped = not index_path.is_file()
    if graph_stage_skipped:
        retrieval_metrics["graph_stage_skipped"] = True
        symbol_snippets = []
    else:
        graph_telemetry: dict = {}
        symbol_snippets = expand_from_anchors(
            anchors, query or "", project_root, graph_telemetry_out=graph_telemetry
        )
        retrieval_metrics.update(graph_telemetry)
        retrieval_metrics["graph_stage_skipped"] = False

    state.context["retrieval_metrics"] = retrieval_metrics

    expanded = expand_search_results(anchors)
    symbol_results = []
    reference_results = []
    file_snippets = []
    for item in expanded:
        path = _resolve_path(item.get("file") or "", project_root)
        symbol = item.get("symbol") or ""
        action_type = item.get("action") or "read_file"
        line = item.get("line")
        try:
            if action_type == "read_symbol_body" and symbol:
                body = read_symbol_body(symbol, path, line=line)
                file_snippets.append({"file": path, "snippet": body, "symbol": symbol})
                sr = {"file": path, "symbol": symbol, "snippet": body[:500]}
                if line is not None:
                    sr["line"] = line
                symbol_results.append(sr)
            else:
                content = read_file(path)
                snip = (content or "")[:2000]
                file_snippets.append({"file": path, "snippet": snip, "symbol": ""})
            refs = find_referencing_symbols(symbol or path, path, project_root=project_root)
            if isinstance(refs, dict):
                for key in ("callers", "callees", "imports", "referenced_by"):
                    reference_results.extend(refs.get(key) or [])
            else:
                reference_results.extend(refs if isinstance(refs, list) else [])
        except Exception as e:
            logger.warning("[retrieval_pipeline] expand %s: %s", path, e)

    built = build_context_from_symbols(
        symbol_results, reference_results, file_snippets, project_root=project_root
    )
    state.context["retrieved_symbols"] = built.get("symbols", [])
    state.context["retrieved_references"] = built.get("references", [])
    state.context["retrieved_files"] = built.get("files", [])
    # context_snippets: list of {file, symbol, snippet} per plan
    state.context["context_snippets"] = built.get("snippets", [])

    candidates = _build_candidates_from_context(built)
    if symbol_snippets:
        candidates = symbol_snippets + candidates
    candidates = localization_candidates + candidates
    candidates = candidates[:MAX_RETRIEVAL_RESULTS]
    state.context["context_candidates"] = candidates

    # Step 5: Unconditional deduplication before reranker
    pre_dedupe_count = len(candidates)
    candidates = deduplicate_candidates(candidates)
    dedupe_removed_count = pre_dedupe_count - len(candidates)
    retrieval_metrics = state.context.get("retrieval_metrics") or {}
    retrieval_metrics["dedupe_removed_count"] = dedupe_removed_count
    retrieval_metrics["candidate_count"] = len(candidates)
    state.context["retrieval_metrics"] = retrieval_metrics

    # Ext Step 2: Candidate budget before reranker
    pre_budget_count = len(candidates)
    candidates = candidates[:MAX_RERANK_CANDIDATES]
    candidate_budget_applied = pre_budget_count - len(candidates)
    retrieval_metrics["candidate_budget_applied"] = candidate_budget_applied
    state.context["retrieval_metrics"] = retrieval_metrics

    rank_query = query or state.instruction or ""

    # --- Cross-encoder reranker gate ---
    _skipped_reason: str | None = None
    _reranker = create_reranker() if RERANKER_ENABLED else None
    _bypass, _bypass_reason = is_symbol_query(rank_query)
    final_context: list[dict] = []

    if _reranker and not _bypass and len(candidates) >= RERANK_MIN_CANDIDATES:
        candidates_in = len(candidates)
        try:
            t0 = time.monotonic()
            deduped = candidates  # already deduped above
            snippets = [c.get("snippet") or "" for c in deduped]
            scored = _reranker.rerank(rank_query, snippets)
            rerank_ms = int((time.monotonic() - t0) * 1000)
            total_tokens = sum(len(s.split()) for s in snippets)

            from agent.retrieval.reranker.hardware import detect_hardware  # noqa: PLC0415
            device = detect_hardware()

            reranked = _apply_reranker_scores(deduped, scored, RERANKER_TOP_K)
            impact = _compute_rerank_impact(deduped, reranked)
            final_context = prune_context(
                reranked, max_snippets=MAX_CONTEXT_SNIPPETS, max_chars=DEFAULT_MAX_CHARS
            )
            _log_rerank_telemetry(
                state, rerank_ms, device,
                candidates_in, len(deduped), len(final_context),
                total_tokens, skipped_reason=None, impact=impact,
            )
        except Exception as exc:
            logger.warning("[retrieval_pipeline] reranker inference failed — falling back to LLM ranker: %s", exc)
            _skipped_reason = f"inference_error:{type(exc).__name__}"
            _reranker = None  # trigger fallback below
    elif _reranker is None and RERANKER_ENABLED:
        _skipped_reason = "disabled"
    elif _bypass:
        _skipped_reason = f"symbol_query:{_bypass_reason}"
    elif candidates and len(candidates) < RERANK_MIN_CANDIDATES:
        _skipped_reason = "below_min_candidates"

    # Fallback: existing LLM ranker when reranker was skipped or failed
    if not final_context:
        if ENABLE_CONTEXT_RANKING and candidates:
            ranked = rank_context(rank_query, candidates)
            final_context = prune_context(
                ranked, max_snippets=MAX_CONTEXT_SNIPPETS, max_chars=DEFAULT_MAX_CHARS
            )
        if _skipped_reason:
            _log_rerank_telemetry(
                state, 0, "none",
                len(candidates), len(candidates), len(final_context),
                0, skipped_reason=_skipped_reason,
            )

    # Phase 10: optional context compression in repo-scale mode
    if final_context and state.context.get("repo_summary"):
        from agent.repo_intelligence.context_compressor import compress_context  # noqa: PLC0415
        from config.repo_intelligence_config import MAX_CONTEXT_TOKENS  # noqa: PLC0415

        final_context, compression_ratio = compress_context(
            final_context,
            repo_summary=state.context.get("repo_summary"),
            task_goal=state.instruction or "",
            max_tokens=MAX_CONTEXT_TOKENS,
        )
        state.context["context_compression_ratio"] = compression_ratio

    state.context["ranked_context"] = final_context
    state.context["ranking_scores"] = []

    search_memory = state.context.get("search_memory") or {}
    if isinstance(search_memory, dict):
        search_memory = dict(search_memory)
        existing = search_memory.get("results") or []
        for s in built.get("snippets", [])[:5]:
            snip = s.get("snippet", "") if isinstance(s, dict) else str(s)
            existing.append({"file": "", "snippet": snip[:500]})
        search_memory["results"] = existing
        state.context["search_memory"] = search_memory

    return {
        "results": results,
        "query": query or "",
        "anchors": len(anchors),
        "expanded": len(expanded),
        "symbols": len(built.get("symbols", [])),
        "references": len(built.get("references", [])),
    }
