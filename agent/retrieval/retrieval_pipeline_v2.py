"""retrieval_pipeline_v2 — minimal, heuristic-free retrieval.

Pipeline (Option B — RRF then optional cross-encoder reranker):

  query
    → [parallel] graph_lookup | bm25 | vector | serena
    → RRF (initial merge)
    → symbol_boost (exact graph match → guaranteed rank 0, pre-reranker)
    → path_validate (safety: exists + under project_root + no blocked segments)
    → reranker (optional cross-encoder, V2_RERANKER_ENABLED=1)
        — reranker is the SOLE ranking authority when active (no score fusion)
    → prune_deterministic (dedup + budget, preserve reranker/RRF order)
    → RetrievalOutput

Design decisions:
  Symbol boost:  exact graph name match → injected at position 0 after RRF.
                 Safe: symbol name is ground-truth signal, not a heuristic.
                 LIKE matches are NOT boosted — only exact name.
  Reranker:      Option B. When V2_RERANKER_ENABLED=1, cross-encoder is the
                 final ranking authority. No score fusion with RRF weights.
                 Falls back to RRF order silently when unavailable.
  Graph:         graph_lookup only — no NL extraction, no expansion.
  Prune:         dedup key (path_norm, symbol_norm, snippet_hash[:16]),
                 preserves post-reranker/RRF order strictly.
  Heuristics:    NONE. No filter_and_rank_search_results, no intent bias,
                 no test-file penalties, no extension filters.

Feature flags:  RETRIEVAL_PIPELINE_V2=1  — activate v2 pipeline (default ON)
                V2_RERANKER_ENABLED=1    — enable cross-encoder reranker (default ON)
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor

from agent.retrieval.adapters.bm25 import fetch_bm25
from agent.retrieval.adapters.graph import fetch_graph
from agent.retrieval.adapters.serena import fetch_serena
from agent.retrieval.adapters.vector import fetch_vector
from agent.retrieval.candidate_schema import Candidate, RetrievalInput, RetrievalOutput, Source
from agent.retrieval.path_validate import validate_paths
from agent.retrieval.prune_deterministic import prune_deterministic
from agent.retrieval.rank_fusion import reciprocal_rank_fusion

logger = logging.getLogger(__name__)


def retrieve_v2(
    inp: RetrievalInput,
    state=None,
) -> RetrievalOutput:
    """Execute v2 retrieval pipeline.

    Args:
        inp: RetrievalInput with query and tuning params.
        state: optional AgentState — used to read project_root and to write
               v2_retrieval_trace into state.context for observability.

    Returns:
        RetrievalOutput with candidates and full stages trace.
        stages keys:
          pre_rrf   — per-source counts and top rows before fusion.
          post_rrf  — merged candidate count and top rows after fusion.
          post_validate — count after path safety check.
          post_prune    — final candidate count.
    """
    query = inp.query
    if not query or not query.strip():
        return RetrievalOutput(candidates=[], query=query or "")

    project_root = (
        inp.project_root
        or (_state_context(state).get("project_root") if state else None)
        or os.environ.get("SERENA_PROJECT_DIR")
        or os.getcwd()
    )

    warnings: list[str] = []
    stages: dict = {}

    # ── Stage 1: Parallel retrieval ─────────────────────────────────────────
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {
            "graph":  ex.submit(fetch_graph,  query, project_root, inp.top_k_per_source),
            "bm25":   ex.submit(fetch_bm25,   query, project_root, inp.top_k_per_source),
            "vector": ex.submit(fetch_vector, query, project_root, inp.top_k_per_source),
            "serena": ex.submit(fetch_serena, query, project_root, inp.top_k_per_source),
        }
        source_results: dict[str, list[dict]] = {}
        for name, fut in futs.items():
            rows, warns = fut.result()
            source_results[name] = rows
            warnings.extend(warns)

    stages["pre_rrf"] = {
        src: {
            "count": len(rows),
            "candidates": [
                {
                    "file": r.get("file", ""),
                    "symbol": r.get("symbol", ""),
                    "source": r.get("source", ""),
                    "snippet": (r.get("snippet") or "")[:120],
                }
                for r in rows
            ],
        }
        for src, rows in source_results.items()
    }

    # ── Stage 2: RRF merge ───────────────────────────────────────────────────
    nonempty = [rows for rows in source_results.values() if rows]
    if not nonempty:
        _write_trace(state, query, stages, warnings)
        return RetrievalOutput(candidates=[], query=query, warnings=warnings, stages=stages)

    merged = reciprocal_rank_fusion(nonempty, k=inp.rrf_k, top_n=inp.rrf_top_n)

    stages["post_rrf"] = {
        "count": len(merged),
        "candidates": [
            {
                "file": r.get("file", ""),
                "symbol": r.get("symbol", ""),
                "source": r.get("source", ""),
                "snippet": (r.get("snippet") or "")[:120],
            }
            for r in merged
        ],
    }

    # ── Stage 3: Symbol boost — exact graph name match → guaranteed rank 0 ──
    # Applied before reranker so the reranker can confirm or re-score,
    # but the exact match is never buried by BM25/vector noise.
    merged = _symbol_boost(merged)
    stages["symbol_boost"] = {
        "exact_count": sum(
            1 for r in merged if (r.get("metadata") or {}).get("exact_graph_match")
        ),
    }

    # ── Stage 4: Path validation (safety only) ──────────────────────────────
    extra_root = _state_context(state).get("source_root") if state else None
    validated = validate_paths(
        merged,
        project_root,
        extra_roots=(str(extra_root),) if extra_root else (),
    )
    stages["post_validate"] = {
        "count": len(validated),
        "dropped": len(merged) - len(validated),
    }

    # ── Stage 5: Reranker (Option B — sole ranking authority) ────────────────
    from config.retrieval_config import V2_RERANK_TOP_N  # noqa: PLC0415

    rerank_input = validated[:V2_RERANK_TOP_N]
    reranked, rerank_warn = _apply_reranker(query, rerank_input)
    # Append any candidates beyond V2_RERANK_TOP_N that weren't reranked
    reranked = reranked + validated[V2_RERANK_TOP_N:]
    if rerank_warn:
        warnings.append(rerank_warn)
    stages["post_rerank"] = {
        "reranker_active": rerank_warn is None,
        "count": len(reranked),
        "top5": [
            {
                "file": r.get("file", ""),
                "symbol": r.get("symbol", ""),
                "source": r.get("source", ""),
                "rerank_score": r.get("_rerank_score"),
            }
            for r in reranked[:5]
        ],
    }

    # ── Stage 6: Deterministic prune ─────────────────────────────────────────
    pruned = prune_deterministic(
        reranked,
        max_snippets=inp.max_snippets,
        max_chars=inp.max_chars,
    )
    stages["post_prune"] = {"count": len(pruned)}

    # ── Stage 7: Convert to Candidates ───────────────────────────────────────
    candidates = [_row_to_candidate(r) for r in pruned]

    _write_trace(state, query, stages, warnings)
    return RetrievalOutput(
        candidates=candidates,
        query=query,
        warnings=warnings,
        stages=stages,
    )


def retrieve_v2_as_legacy(
    query: str,
    state=None,
    project_root: str | None = None,
) -> dict:
    """Run retrieve_v2 and return legacy {results, query} dict for step_dispatcher.

    Maps Candidate list back to [{file, symbol, line, snippet}] so that the
    existing pipeline consumers (anchor detection, expansion, etc.) receive the
    same shape they expect — just with clean, RRF-ranked, heuristic-free input.
    """
    from config.retrieval_config import (  # noqa: PLC0415
        V2_MAX_SNIPPETS,
        V2_RRF_K,
        V2_RRF_TOP_N,
        V2_TOP_K_PER_SOURCE,
    )

    inp = RetrievalInput(
        query=query,
        project_root=project_root,
        top_k_per_source=V2_TOP_K_PER_SOURCE,
        rrf_top_n=V2_RRF_TOP_N,
        rrf_k=V2_RRF_K,
        max_snippets=V2_MAX_SNIPPETS,
    )
    out = retrieve_v2(inp, state=state)
    return {
        "results": [c.to_legacy_dict() for c in out.candidates],
        "query": query or "",
        "v2": True,
        "v2_warnings": out.warnings,
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _state_context(state) -> dict:
    try:
        return state.context or {}
    except Exception:
        return {}


def _write_trace(state, query: str, stages: dict, warnings: list[str]) -> None:
    """Persist trace into state.context for external inspection."""
    if state is None:
        return
    try:
        state.context["v2_retrieval_trace"] = {
            "query": query,
            "stages": stages,
            "warnings": warnings,
        }
    except Exception:
        pass


def _symbol_boost(merged: list[dict]) -> list[dict]:
    """Guarantee exact graph name matches appear at rank 0 after RRF.

    Safe: symbol name = ground-truth signal, not a heuristic.
    LIKE matches are NOT boosted — only rows with exact_graph_match=True (from fetch_graph).
    """
    exact = [r for r in merged if (r.get("metadata") or {}).get("exact_graph_match")]
    rest  = [r for r in merged if not (r.get("metadata") or {}).get("exact_graph_match")]
    if exact:
        logger.debug("[v2.symbol_boost] promoted %d exact-match rows to front", len(exact))
    return exact + rest


def _apply_reranker(query: str, rows: list[dict]) -> tuple[list[dict], str | None]:
    """Cross-encoder rerank rows. Returns (reranked_rows, warn_or_None).

    Option B contract — no score fusion:
      - Reranker order is final.
      - Each returned row has '_rerank_score' set.
      - Rows dropped by threshold filter appended at end with score -1.0.
      - If reranker unavailable → input order unchanged + warning string returned.
    """
    from config.retrieval_config import RERANKER_ENABLED, V2_RERANKER_ENABLED  # noqa: PLC0415

    if not (RERANKER_ENABLED and V2_RERANKER_ENABLED):
        return rows, "reranker_disabled"

    try:
        from agent.retrieval.reranker.reranker_factory import create_reranker  # noqa: PLC0415
    except ImportError as exc:
        return rows, f"reranker_import_error:{exc}"

    reranker = create_reranker()
    if reranker is None:
        return rows, "reranker_unavailable"

    snippets = [row.get("snippet") or "" for row in rows]

    try:
        scored = reranker.rerank(query, snippets)  # [(snippet, score),...] sorted desc
    except Exception as exc:
        logger.warning("[v2.reranker] inference error: %s", exc)
        return rows, f"reranker_error:{type(exc).__name__}"

    # Map scored snippets back to original rows by content.
    # First-occurrence wins when multiple rows share a snippet (handled by prune later).
    from collections import defaultdict  # noqa: PLC0415
    snip_to_idxs: dict[str, list[int]] = defaultdict(list)
    for i, row in enumerate(rows):
        snip_to_idxs[row.get("snippet") or ""].append(i)

    seen: set[int] = set()
    result: list[dict] = []
    for snippet, score in scored:
        for idx in snip_to_idxs.get(snippet, []):
            if idx not in seen:
                seen.add(idx)
                out = dict(rows[idx])
                out["_rerank_score"] = score
                result.append(out)
                break

    # Rows not returned (below score threshold) appended at end
    for i, row in enumerate(rows):
        if i not in seen:
            out = dict(row)
            out["_rerank_score"] = -1.0
            result.append(out)

    logger.debug("[v2.reranker] input=%d scored=%d threshold_dropped=%d",
                 len(rows), len(scored), len(rows) - len(scored))
    return result, None


def _row_to_candidate(row: dict) -> Candidate:
    src_str = row.get("source", "")
    try:
        source = Source(src_str)
    except ValueError:
        source = Source.GRAPH
    raw_rerank = row.get("_rerank_score")
    return Candidate(
        path=row.get("file") or row.get("path") or "",
        snippet=row.get("snippet") or "",
        symbol=row.get("symbol") or None,
        line=row.get("line") or None,
        source=source,
        retrieval_score=None,
        rerank_score=float(raw_rerank) if raw_rerank is not None and raw_rerank >= 0 else None,
        metadata=row.get("metadata") or {},
    )
