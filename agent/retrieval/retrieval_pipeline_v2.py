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
  Reranker:      Option B. Entry point is ``retrieve(queries)`` only: batched
                 vector search, then mandatory ``rerank_batch`` (fail-fast, no fallbacks).
                 Legacy ``retrieve_v2`` raises unless ALLOW_RETRIEVE_V2_LEGACY=1.
  Graph:         graph_lookup only — no NL extraction, no expansion.
  Prune:         dedup key (path_norm, symbol_norm, snippet_hash[:16]),
                 preserves post-reranker/RRF order strictly.
  Heuristics:    NONE. No filter_and_rank_search_results, no intent bias,
                 no test-file penalties, no extension filters.

Feature flags:  RETRIEVAL_PIPELINE_V2=1  — activate v2 pipeline (default ON)
                V2_RERANKER_ENABLED=1    — enable cross-encoder reranker (default ON)
                ENABLE_GRAPH_LOOKUP, ENABLE_BM25_SEARCH, ENABLE_VECTOR_SEARCH — v2 parallel sources
"""

from __future__ import annotations

import logging
import os
import time
from collections import defaultdict
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agent.retrieval.adapters.bm25 import fetch_bm25
from agent.retrieval.adapters.graph import fetch_graph
from agent.retrieval.adapters.serena import fetch_serena
from agent.retrieval.adapters.vector import fetch_vector
from agent.retrieval.candidate_schema import Candidate, RetrievalInput, RetrievalOutput, Source
from agent.retrieval.multi_root_fetch import fetch_merged
from agent.retrieval.path_validate import validate_paths
from agent.retrieval.prune_deterministic import prune_deterministic
from agent.retrieval.rank_fusion import reciprocal_rank_fusion
from config.retrieval_config import (
    ENABLE_BM25_SEARCH,
    ENABLE_GRAPH_LOOKUP,
    ENABLE_VECTOR_SEARCH,
)

logger = logging.getLogger(__name__)


def _retrieval_roots(
    project_root: str | None,
    extra: tuple[str, ...] | None,
) -> tuple[str, ...]:
    """Primary workspace + optional extra indexed repos (deduped, resolved)."""
    primary = str(Path(project_root or os.environ.get("SERENA_PROJECT_DIR") or os.getcwd()).resolve())
    if not extra:
        return (primary,)
    out: list[str] = [primary]
    seen: set[str] = {primary}
    for x in extra:
        if not x or not str(x).strip():
            continue
        try:
            xr = str(Path(x).resolve())
        except OSError:
            continue
        if xr not in seen:
            seen.add(xr)
            out.append(xr)
    return tuple(out)


def _embedding_dict_to_vector_rows(embed_out: dict | None) -> tuple[list[dict], list[str]]:
    """Convert search_by_embedding / search_batch slot dict to fetch_vector-shaped rows."""
    if embed_out is None:
        return [], ["vector_unavailable"]
    raw = embed_out.get("results") or []
    results: list[dict] = []
    for i, r in enumerate(raw):
        results.append({
            "file": r.get("file") or "",
            "symbol": r.get("symbol") or "",
            "line": r.get("line") or 0,
            "snippet": (r.get("snippet") or "")[:500],
            "source": "vector",
            "metadata": {
                "rank_in_source": i,
                "raw_score": None,
                "source_specific": {},
            },
        })
    return results, []


def _gather_parallel_sources(
    query: str,
    inp: RetrievalInput,
    state,
    roots: tuple[str, ...],
    project_root: str,
    *,
    vector_rows_override: list[dict] | None = None,
) -> tuple[dict[str, list[dict]], list[str]]:
    """Stage 1: parallel graph / BM25 / vector / Serena. Optional injected vector rows (single-root)."""
    source_results: dict[str, list[dict]] = {
        "graph": [],
        "bm25": [],
        "vector": [],
        "serena": [],
    }
    warnings: list[str] = []
    cap_merge = max(inp.rrf_top_n, inp.top_k_per_source * max(1, len(roots)))
    use_vector_override = (
        vector_rows_override is not None
        and len(roots) <= 1
        and ENABLE_VECTOR_SEARCH
    )
    _t_gather0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs: dict = {}
        if len(roots) <= 1:
            pr = roots[0]
            if ENABLE_GRAPH_LOOKUP:
                futs["graph"] = ex.submit(fetch_graph, query, pr, inp.top_k_per_source)
            if ENABLE_BM25_SEARCH:
                futs["bm25"] = ex.submit(fetch_bm25, query, pr, inp.top_k_per_source)
            if ENABLE_VECTOR_SEARCH:
                if use_vector_override:
                    source_results["vector"] = list(vector_rows_override or [])
                else:
                    futs["vector"] = ex.submit(fetch_vector, query, pr, inp.top_k_per_source)
            futs["serena"] = ex.submit(fetch_serena, query, pr, inp.top_k_per_source)
        else:
            if ENABLE_GRAPH_LOOKUP:
                futs["graph"] = ex.submit(
                    fetch_merged,
                    fetch_graph,
                    query,
                    roots,
                    inp.top_k_per_source,
                    max_rows=cap_merge,
                )
            if ENABLE_BM25_SEARCH:
                futs["bm25"] = ex.submit(
                    fetch_merged,
                    fetch_bm25,
                    query,
                    roots,
                    inp.top_k_per_source,
                    max_rows=cap_merge,
                )
            if ENABLE_VECTOR_SEARCH:
                futs["vector"] = ex.submit(
                    fetch_merged,
                    fetch_vector,
                    query,
                    roots,
                    inp.top_k_per_source,
                    max_rows=cap_merge,
                )
            futs["serena"] = ex.submit(fetch_serena, query, project_root, inp.top_k_per_source)
        for name, fut in futs.items():
            rows, warns = fut.result()
            source_results[name] = rows
            warnings.extend(warns)
    _gather_ms = (time.perf_counter() - _t_gather0) * 1000.0
    qpv = (query[:64] + "…") if len(query) > 64 else query
    logger.info(
        "[v2.timing] gather_parallel_sources_ms=%.1f query_preview=%r roots=%d",
        _gather_ms,
        qpv,
        len(roots),
    )
    return source_results, warnings


@dataclass
class _V2PreRerank:
    """Validated rows split for reranker input vs tail (post-rerank concat order)."""

    rerank_input: list[dict]
    tail: list[dict]


def _apply_rerank_score_policy(pairs: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """Match ``BaseReranker.rerank`` threshold behavior (lines 91–96)."""
    from config.retrieval_config import (  # noqa: PLC0415
        RERANK_MIN_RESULTS_AFTER_THRESHOLD,
        RERANK_SCORE_THRESHOLD,
    )

    if not pairs:
        return []
    above_threshold = [(d, s) for d, s in pairs if s >= RERANK_SCORE_THRESHOLD]
    if len(above_threshold) >= RERANK_MIN_RESULTS_AFTER_THRESHOLD:
        return above_threshold
    return pairs


def _merge_rerank_scored_into_rows(
    rows: list[dict],
    scored: list[tuple[str, float]],
) -> list[dict]:
    """Map reranked (snippet, score) pairs back to row dicts (first snippet match wins)."""
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
    for i, row in enumerate(rows):
        if i not in seen:
            out = dict(row)
            out["_rerank_score"] = -1.0
            result.append(out)
    return result


def _v2_stages_through_validate(
    query: str,
    inp: RetrievalInput,
    state,
    project_root: str,
    roots: tuple[str, ...],
    source_results: dict[str, list[dict]],
    warnings: list[str],
) -> tuple[dict, _V2PreRerank | None]:
    """Build stages through path_validate; return ``None`` pre-rerank payload if no candidates."""
    stages: dict = {}
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

    nonempty = [rows for rows in source_results.values() if rows]
    if not nonempty:
        return stages, None

    _t_rrf0 = time.perf_counter()
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

    merged = _symbol_boost(merged)
    stages["symbol_boost"] = {
        "exact_count": sum(
            1 for r in merged if (r.get("metadata") or {}).get("exact_graph_match")
        ),
    }

    extra_root = _state_context(state).get("source_root") if state else None
    validate_extras = tuple(roots[1:]) + ((str(extra_root),) if extra_root else ())
    validated = validate_paths(
        merged,
        project_root,
        extra_roots=validate_extras,
    )
    stages["post_validate"] = {
        "count": len(validated),
        "dropped": len(merged) - len(validated),
    }

    from config.retrieval_config import V2_RERANK_TOP_N  # noqa: PLC0415

    _rrf_validate_ms = (time.perf_counter() - _t_rrf0) * 1000.0
    qpv_fin = (query[:64] + "…") if len(query) > 64 else query
    logger.info(
        "[v2.timing] finalize_rrf_symbol_validate_ms=%.1f merged=%d validated=%d query_preview=%r",
        _rrf_validate_ms,
        len(merged),
        len(validated),
        qpv_fin,
    )

    rerank_input = validated[:V2_RERANK_TOP_N]
    tail = validated[V2_RERANK_TOP_N:]
    return stages, _V2PreRerank(rerank_input=rerank_input, tail=tail)


def _finalize_v2_post_rerank_prune(
    query: str,
    inp: RetrievalInput,
    state,
    stages: dict,
    pre: _V2PreRerank,
    reranked: list[dict],
    rerank_warn: str | None,
    warnings: list[str],
) -> RetrievalOutput:
    """post_rerank stage metadata, deterministic prune, trace, output."""
    reranked = reranked + pre.tail
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

    pruned = prune_deterministic(
        reranked,
        max_snippets=inp.max_snippets,
        max_chars=inp.max_chars,
    )
    stages["post_prune"] = {"count": len(pruned)}

    candidates = [_row_to_candidate(r) for r in pruned]

    _write_trace(state, query, stages, warnings)
    return RetrievalOutput(
        candidates=candidates,
        query=query,
        warnings=warnings,
        stages=stages,
    )


def _retrieve_v2_pre_only(
    inp: RetrievalInput,
    state=None,
    *,
    vector_rows_override: list[dict] | None = None,
) -> tuple[dict, _V2PreRerank | None, list[str]]:
    """Stages through path_validate only; used by ``retrieve`` batch rerank path."""
    query = inp.query
    if not query or not query.strip():
        return {}, None, []

    project_root = (
        inp.project_root
        or (_state_context(state).get("project_root") if state else None)
        or os.environ.get("SERENA_PROJECT_DIR")
        or os.getcwd()
    )

    roots = _retrieval_roots(str(project_root), inp.extra_project_roots)
    source_results, warnings = _gather_parallel_sources(
        query,
        inp,
        state,
        roots,
        str(project_root),
        vector_rows_override=vector_rows_override,
    )
    stages, pre = _v2_stages_through_validate(
        query, inp, state, str(project_root), roots, source_results, warnings
    )
    return stages, pre, warnings


def retrieve_v2(
    inp: RetrievalInput,
    state=None,
) -> RetrievalOutput:
    """Deprecated. Use ``retrieve([query], ...)``. Opt-in legacy: ``ALLOW_RETRIEVE_V2_LEGACY=1``."""
    if os.getenv("ALLOW_RETRIEVE_V2_LEGACY") == "1":
        if not inp.query or not str(inp.query).strip():
            return RetrievalOutput(candidates=[], query=inp.query or "")
        return retrieve([str(inp.query).strip()], state=state, project_root=inp.project_root)[0]
    raise RuntimeError(
        "retrieve_v2 is deprecated. Use retrieve(queries: list[str], ...) "
        "with one or more queries (e.g. retrieve([q], ...) for a single query)."
    )


def retrieve(
    queries: list[str],
    state=None,
    project_root: str | None = None,
) -> list[RetrievalOutput]:
    """Single v2 entry: ``search_batch``, parallel pre-rerank, mandatory ``rerank_batch`` (fail-fast)."""
    from agent.retrieval.reranker.reranker_factory import create_reranker  # noqa: PLC0415
    from agent.retrieval.vector_retriever import search_batch  # noqa: PLC0415
    from config.retrieval_config import (  # noqa: PLC0415
        RERANK_MIN_CANDIDATES,
        RERANKER_ENABLED,
        V2_MAX_SNIPPETS,
        V2_RERANKER_ENABLED,
        V2_RRF_K,
        V2_RRF_TOP_N,
        V2_TOP_K_PER_SOURCE,
        get_retrieval_extra_roots,
    )

    norm = [str(q).strip() for q in queries if q is not None and str(q).strip()]
    if not norm:
        raise ValueError("retrieve: at least one non-empty query is required")
    queries = norm
    n = len(queries)

    if not ENABLE_VECTOR_SEARCH:
        raise RuntimeError(
            "retrieve requires ENABLE_VECTOR_SEARCH=1 (batched vector injection)."
        )

    pr = (
        project_root
        or (_state_context(state).get("project_root") if state else None)
        or os.environ.get("SERENA_PROJECT_DIR")
        or os.getcwd()
    )
    extras = get_retrieval_extra_roots()
    extras_t: tuple[str, ...] | None = extras if extras else None
    roots = _retrieval_roots(str(pr), extras_t)
    if len(roots) != 1:
        raise RuntimeError(
            "retrieve requires exactly one project root; "
            "clear RETRIEVAL_EXTRA_PROJECT_ROOTS or use a single workspace."
        )

    def _inp_for(q: str) -> RetrievalInput:
        return RetrievalInput(
            query=q,
            project_root=pr,
            extra_project_roots=extras_t,
            top_k_per_source=V2_TOP_K_PER_SOURCE,
            rrf_top_n=V2_RRF_TOP_N,
            rrf_k=V2_RRF_K,
            max_snippets=V2_MAX_SNIPPETS,
        )

    logger.info("[retrieve] [VECTOR_BATCH] queries=%d", n)
    _t_vec0 = time.perf_counter()
    batch = search_batch(queries, project_root=str(roots[0]), top_k=V2_TOP_K_PER_SOURCE)
    _vec_ms = (time.perf_counter() - _t_vec0) * 1000.0
    logger.info("[v2.timing] retrieve vector_search_batch_ms=%.1f queries=%d", _vec_ms, n)
    if not isinstance(batch, list) or len(batch) != n:
        raise ValueError("vector batch shape mismatch")
    vector_slots: list[list[dict]] = []
    for item in batch:
        if item is None or not isinstance(item, dict) or "results" not in item:
            raise ValueError("vector batch slot invalid")
        rows, _w = _embedding_dict_to_vector_rows(item)
        vector_slots.append(rows)

    workers = min(6, max(1, n))

    def _pre_one(i: int) -> tuple[dict, _V2PreRerank | None, list[str]]:
        return _retrieve_v2_pre_only(
            _inp_for(queries[i]),
            state,
            vector_rows_override=vector_slots[i],
        )

    _t_pool0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {i: ex.submit(_pre_one, i) for i in range(n)}
        pres_results: list[tuple[dict, _V2PreRerank | None, list[str]]] = []
        for i in range(n):
            raw = futs[i].result()
            if not isinstance(raw, tuple) or len(raw) != 3:
                raise RuntimeError(
                    f"_retrieve_v2_pre_only must return a 3-tuple; got {type(raw).__name__!r} at index {i}"
                )
            pres_results.append(raw)
    _pool_ms = (time.perf_counter() - _t_pool0) * 1000.0
    logger.info(
        "[v2.timing] retrieve parallel_pre_rerank_ms=%.1f queries=%d workers=%d",
        _pool_ms,
        n,
        workers,
    )

    outputs: list[RetrievalOutput | None] = [None] * n
    for i, (stages, pre, w) in enumerate(pres_results):
        if pre is None:
            _write_trace(state, queries[i], stages, w)
            outputs[i] = RetrievalOutput(
                candidates=[],
                query=queries[i] or "",
                warnings=w,
                stages=stages,
            )

    pending = [i for i in range(n) if outputs[i] is None]
    if not pending:
        logger.info(
            "BATCH_PIPELINE_EXECUTED queries=%d rerank_slots=0 pairs=0 rerank_batch_calls=0",
            n,
        )
        return [outputs[i] for i in range(n)]  # type: ignore[list-item]

    if not (RERANKER_ENABLED and V2_RERANKER_ENABLED):
        raise RuntimeError(
            "retrieve requires RERANKER_ENABLED=1 and V2_RERANKER_ENABLED=1 for batch reranking"
        )

    for i in pending:
        _stages, pre, _w = pres_results[i]
        if pre is None:
            raise RuntimeError("internal error: pending index without pre-rerank payload")
        ni = len(pre.rerank_input)
        if ni == 0:
            raise RuntimeError(f"retrieve: query index {i} has empty rerank_input")
        if ni < RERANK_MIN_CANDIDATES:
            raise RuntimeError(
                f"retrieve: query index {i} has {ni} rerank candidates; "
                f"need >= {RERANK_MIN_CANDIDATES} (RERANK_MIN_CANDIDATES) for batch reranking"
            )

    batch_reqs: list[tuple[str, list[str]]] = [
        (queries[i], [r.get("snippet") or "" for r in pres_results[i][1].rerank_input])
        for i in pending
    ]
    for br_idx, (q, docs) in enumerate(batch_reqs):
        if not isinstance(q, str) or not isinstance(docs, list):
            raise RuntimeError(f"retrieve: invalid batch_req at {br_idx}")

    reranker = create_reranker()
    if reranker is None:
        raise RuntimeError("reranker_unavailable: create_reranker() returned None")

    _t_rb0 = time.perf_counter()
    all_scored = reranker.rerank_batch(batch_reqs)
    _rb_ms = (time.perf_counter() - _t_rb0) * 1000.0
    n_pairs = sum(len(d) for _, d in batch_reqs)
    if len(all_scored) != len(pending):
        raise ValueError(
            f"rerank_batch length mismatch: got {len(all_scored)} slots, expected {len(pending)}"
        )

    logger.info(
        "BATCH_PIPELINE_EXECUTED queries=%d pairs=%d rerank_slots=%d rerank_batch_calls=1 rerank_batch_ms=%.1f",
        n,
        n_pairs,
        len(pending),
        _rb_ms,
    )
    logger.info(
        "[v2.timing] retrieve rerank_batch_ms=%.1f queries=%d pairs=%d",
        _rb_ms,
        len(batch_reqs),
        n_pairs,
    )

    for j, i in enumerate(pending):
        stages, pre, w = pres_results[i]
        pairs = _apply_rerank_score_policy(all_scored[j])
        rr = _merge_rerank_scored_into_rows(pre.rerank_input, pairs)
        outputs[i] = _finalize_v2_post_rerank_prune(
            queries[i],
            _inp_for(queries[i]),
            state,
            stages,
            pre,
            rr,
            None,
            list(w),
        )

    return [outputs[i] for i in range(n)]  # type: ignore[list-item]


retrieve_v2_multi = retrieve


def search_payload_from_retrieval_output(out: RetrievalOutput) -> dict:
    """Map ``RetrievalOutput`` to step_dispatcher / ReAct tool shape ({results, query, v2, v2_warnings})."""
    return {
        "results": [c.to_legacy_dict() for c in out.candidates],
        "query": out.query or "",
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
