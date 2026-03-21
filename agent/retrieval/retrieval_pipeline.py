"""Retrieval pipeline: anchor detection → expand → read → find_references → build_context. Dispatcher calls this only."""

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from agent.memory.state import AgentState
from agent.retrieval.query_expansion import generate_query_expansions
from agent.retrieval.rank_fusion import reciprocal_rank_fusion
from agent.retrieval.retrieval_cache import get_candidate_cached, set_candidate_cached
from agent.retrieval.retrieval_metrics import RetrievalMetrics
from agent.retrieval.reranker.symbol_query_detector import is_symbol_query
from agent.retrieval.anchor_detector import detect_anchors
from agent.retrieval.search_target_filter import filter_and_rank_search_results
from agent.retrieval.context_builder import build_context_from_symbols
from agent.retrieval.context_pruner import prune_context
from agent.retrieval.retrieval_expander import (
    expand_file_header,
    expand_region_bounded,
    expand_search_results,
    extract_enclosing_class_name,
    normalize_file_path,
)
from agent.retrieval.retrieval_intent import INTENT_ARCHITECTURE, apply_intent_bias, classify_query_intent
from agent.retrieval.reranker.cache import cache_stats
from agent.retrieval.reranker.deduplicator import deduplicate_candidates, retrieval_row_identity_key
from agent.retrieval.reranker.reranker_factory import create_reranker
from agent.retrieval.snippet_text import coerce_snippet_text
from agent.retrieval.task_semantics import instruction_path_hints, instruction_suggests_docs_consistency
from agent.retrieval.result_contract import (
    RETRIEVAL_RESULT_TYPE_FILE_HEADER,
    RETRIEVAL_RESULT_TYPE_REGION_BODY,
    RETRIEVAL_RESULT_TYPE_SYMBOL_BODY,
)
from agent.retrieval.selector_candidate_pool import build_selector_candidate_pool
from agent.retrieval.symbol_expander import expand_from_anchors
from agent.tools import find_referencing_symbols, read_file, read_symbol_body
from config.agent_config import MAX_RETRIEVAL_RESULTS
from config.repo_graph_config import INDEX_SQLITE, SYMBOL_GRAPH_DIR
from config.retrieval_config import (
    DEFAULT_MAX_CHARS,
    ENABLE_CONTEXT_RANKING,
    ENABLE_LOCALIZATION_ENGINE,
    FALLBACK_TOP_N,
    MAX_CONTEXT_SNIPPETS,
    MAX_RERANK_CANDIDATES,
    MAX_SEARCH_RESULTS,
    MAX_SELECTOR_CANDIDATE_POOL,
    MIN_SELECTOR_CANDIDATE_POOL,
    RERANK_FUSION_WEIGHT,
    RERANK_MIN_CANDIDATES,
    RERANKER_CPU_MODEL,
    RERANKER_ENABLED,
    RERANKER_GPU_MODEL,
    RERANKER_TOP_K,
    RETRIEVAL_AUTO_DETECT_SERVICE_DIRS,
    RETRIEVAL_SERVICE_DIRS,
    RETRIEVAL_TEST_DOWNWEIGHT,
    RETRIEVAL_USE_SERVICE_DIRS,
    RETRIEVER_FUSION_WEIGHT,
)

logger = logging.getLogger(__name__)

SEARCH_CANDIDATES_TOP_K = 20


def _rows_have_implementation_body(rows: list | None) -> bool:
    return any(isinstance(r, dict) and r.get("implementation_body_present") for r in (rows or []))


def _maybe_seed_ranked_context_when_search_empty(
    state: AgentState,
    project_root: str,
    query: str | None,
) -> None:
    """When SEARCH yields no usable filtered hits or anchors, still load instruction path files."""
    merged_ctx, inject_n = _inject_instruction_path_snippets([], state, project_root, query)
    if inject_n:
        state.context["ranked_context"] = merged_ctx
        rm = state.context.get("retrieval_metrics") or {}
        rm["instruction_path_injects"] = inject_n
        rm["search_recovered_via_instruction_paths"] = True
        state.context["retrieval_metrics"] = rm


def _inject_instruction_path_snippets(
    final_context: list[dict],
    state: AgentState,
    project_root: str,
    query: str | None,
) -> tuple[list[dict], int]:
    """
    Prepend full-file snippets for paths extracted from the merged instruction when docs/code
    alignment is suggested. Ensures README.md / benchmark_local notes reach ranked_context even
    when the search index ranked them out.
    """
    ctx = state.context or {}
    merged = " ".join(
        x for x in [query, ctx.get("parent_instruction"), state.instruction]
        if x
    ).strip()
    if not instruction_suggests_docs_consistency(merged):
        return final_context, 0
    hints = instruction_path_hints(merged)
    root = Path(project_root).resolve()
    existing: set[str] = set()
    for c in final_context:
        if isinstance(c, dict) and c.get("file"):
            try:
                existing.add(str(Path(c["file"]).resolve()))
            except (OSError, ValueError):
                pass
    injected: list[dict] = []
    for h in hints:
        h = h.strip()
        if not h.endswith((".py", ".pyi", ".md", ".mdx")):
            continue
        p = (root / h).resolve()
        try:
            p.relative_to(root)
        except ValueError:
            continue
        if not p.is_file():
            continue
        sp = str(p)
        if sp in existing:
            continue
        try:
            body = read_file(sp)
        except Exception:
            continue
        snip = (body or "")[:12000]
        injected.append({
            "file": sp,
            "symbol": "",
            "snippet": snip,
            "retrieval_result_type": "instruction_path_inject",
            "candidate_kind": "file",
        })
        existing.add(sp)
    if not injected:
        return final_context, 0
    return injected + final_context, len(injected)


def search_candidates(query: str, project_root: str | None = None, state: AgentState | None = None) -> list[dict]:
    """
    Candidate discovery only: BM25, vector, repo_map, grep. No graph expansion, ranking, or pruning.
    Returns list of {symbol, file, snippet, score, source} up to SEARCH_CANDIDATES_TOP_K.
    """
    if not query or not query.strip():
        return []

    root = project_root or os.environ.get("SERENA_PROJECT_DIR") or os.getcwd()
    cached = get_candidate_cached(query, root)
    if cached is not None:
        return cached
    _bypass, _ = is_symbol_query(query)
    if _bypass:
        expansions = [query.strip()]
    else:
        expansions = generate_query_expansions(query)
        if not expansions:
            expansions = [query.strip()]

    def _to_candidate(r: dict, source: str, score: float = 1.0) -> dict:
        return {
            "symbol": (r.get("symbol") or r.get("anchor") or "").strip(),
            "file": (r.get("file") or r.get("path") or "").strip(),
            "snippet": (r.get("snippet") or r.get("symbol") or r.get("anchor") or "")[:500],
            "score": float(r.get("score", score)),
            "source": source,
        }

    def _normalize_scores(lst: list[dict]) -> None:
        """Task 12: normalized_score = raw_score / max_score per list."""
        if not lst:
            return
        max_s = max(float(c.get("score", 0) or 0) for c in lst)
        if max_s <= 0:
            return
        for c in lst:
            c["score"] = float(c.get("score", 0) or 0) / max_s

    ctx = (state.context or {}) if state else {}
    metrics = RetrievalMetrics(
        trace_id=ctx.get("trace_id"),
        query_id=ctx.get("query_id"),
        step_id=ctx.get("current_step_id"),
    )
    all_lists: list[list[dict]] = []

    def _run_bm25() -> list[dict]:
        metrics.start("bm25")
        try:
            from agent.retrieval.bm25_retriever import search_bm25
            from config.retrieval_config import BM25_TOP_K, ENABLE_BM25_SEARCH

            if not ENABLE_BM25_SEARCH:
                return []
            raw = search_bm25(expansions[0], root, top_k=BM25_TOP_K)
            lst = [_to_candidate(r, "bm25", score=1.0 / (i + 1)) for i, r in enumerate(raw)]
            _normalize_scores(lst)
            return lst
        except Exception as e:
            logger.debug("[search_candidates] bm25 failed: %s", e)
            return []
        finally:
            metrics.end("bm25")

    def _run_vector() -> list[dict]:
        metrics.start("vector")
        try:
            from agent.retrieval.vector_retriever import search_by_embedding
            from config.retrieval_config import ENABLE_VECTOR_SEARCH

            if not ENABLE_VECTOR_SEARCH:
                return []
            out = search_by_embedding(expansions[0], root, top_k=10)
            if not out or not out.get("results"):
                return []
            lst = [_to_candidate(r, "vector", score=1.0 / (i + 1)) for i, r in enumerate(out["results"])]
            _normalize_scores(lst)
            return lst
        except Exception as e:
            logger.debug("[search_candidates] vector failed: %s", e)
            return []
        finally:
            metrics.end("vector")

    def _run_grep() -> list[dict]:
        metrics.start("grep")
        try:
            from agent.tools.serena_adapter import search_code

            out = search_code(query, tool_hint="search_for_pattern")
            if not out or not out.get("results"):
                return []
            lst = [_to_candidate(r, "grep", score=1.0 / (i + 1)) for i, r in enumerate(out["results"])]
            _normalize_scores(lst)
            return lst
        except Exception as e:
            logger.debug("[search_candidates] grep failed: %s", e)
            return []
        finally:
            metrics.end("grep")

    def _run_repo_map() -> list[dict]:
        metrics.start("repo_map")
        try:
            from agent.retrieval.repo_map_lookup import lookup_repo_map

            raw = lookup_repo_map(query, root)
            lst = []
            for i, r in enumerate(raw):
                c = {"anchor": r.get("anchor", ""), "file": r.get("file", ""), "snippet": r.get("anchor", "")}
                lst.append(_to_candidate(c, "symbol", score=1.0 / (i + 1)))
            _normalize_scores(lst)
            return lst
        except Exception as e:
            logger.debug("[search_candidates] repo_map failed: %s", e)
            return []
        finally:
            metrics.end("repo_map")

    # Task 17: Parallel candidate retrieval
    with ThreadPoolExecutor(max_workers=4) as ex:
        f_bm25 = ex.submit(_run_bm25)
        f_vector = ex.submit(_run_vector)
        f_grep = ex.submit(_run_grep)
        f_repo = ex.submit(_run_repo_map)
        for fut in as_completed([f_bm25, f_vector, f_grep, f_repo]):
            lst = fut.result()
            if lst:
                all_lists.append(lst)

    # Task 13: Penalize test files before RRF (extended patterns)
    TEST_PATH_PATTERNS = ("/tests/", "/test/", "test_", "_test.py", "conftest.py", "\\tests\\", "\\test\\")
    downweight = RETRIEVAL_TEST_DOWNWEIGHT
    for lst in all_lists:
        for c in lst:
            path = (c.get("file") or "").lower()
            if any(p in path for p in TEST_PATH_PATTERNS):
                c["score"] = float(c.get("score", 1.0)) * downweight

    lists = all_lists
    if not lists:
        return []

    metrics.start("rrf_merge")
    merged = reciprocal_rank_fusion(lists, top_n=SEARCH_CANDIDATES_TOP_K)
    metrics.end("rrf_merge")

    if RETRIEVAL_USE_SERVICE_DIRS:
        root = project_root or os.environ.get("SERENA_PROJECT_DIR") or os.getcwd()
        service_dirs = RETRIEVAL_SERVICE_DIRS
        if RETRIEVAL_AUTO_DETECT_SERVICE_DIRS:
            candidates = ["src", "lib", "app", "services"]
            detected = [d for d in candidates if (Path(root) / d).is_dir()]
            if detected:
                service_dirs = detected
        merged = _filter_by_service_dirs(merged, service_dirs, root)

    result: list[dict] = []
    for i, m in enumerate(merged[:SEARCH_CANDIDATES_TOP_K]):
        rrf_score = 1.0 / (60 + i + 1) if i < len(merged) else 0.0
        result.append({
            "symbol": m.get("symbol", ""),
            "file": m.get("file", ""),
            "snippet": m.get("snippet", ""),
            "score": rrf_score,
            "source": m.get("source", "rrf"),
        })
    metrics.log()
    try:
        from agent.observability.metrics import record_metric
        total_s = sum(metrics.get_durations().values())
        record_metric("retrieval_latency_ms", total_s * 1000, trace_id=ctx.get("trace_id"), project_root=root, append_jsonl=False)
    except Exception:
        pass
    set_candidate_cached(query, root, result)
    return result


def _filter_by_service_dirs(candidates: list[dict], service_dirs: list[str], project_root: str) -> list[dict]:
    """Keep only paths under any of service_dirs."""
    if not service_dirs or not candidates:
        return candidates
    root = Path(project_root or ".").resolve()
    allowed = {d.strip().lower() for d in service_dirs if d.strip()}
    out: list[dict] = []
    for c in candidates:
        f = (c.get("file") or "").strip()
        if not f:
            out.append(c)
            continue
        p = Path(f)
        if not p.is_absolute():
            p = root / f
        parts = p.parts
        if any(part.lower() in allowed for part in parts):
            out.append(c)
    return out


def _apply_reranker_scores(
    candidates: list[dict],
    scored: list[tuple[str, float]],
    top_k: int,
) -> list[dict]:
    """Merge reranker scores with retriever scores via weighted fusion and slice to top_k."""
    score_map = {doc: score for doc, score in scored}
    for c in candidates:
        reranker_score = score_map.get(c.get("snippet") or "", 0.0)
        base = c.get("selection_score")
        if base is None:
            base = c.get("retriever_score")
        retriever_score = float(base or 0.0)
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
        "ranking_method": "reranker" if skipped_reason is None else "retriever_score",
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


def _typed_fields_from_row(src: dict) -> dict:
    """Copy optional grounding/type metadata from a context row onto a ranker candidate."""
    out: dict = {}
    for k in (
        "retrieval_result_type",
        "implementation_body_present",
        "candidate_kind",
        "line_range",
        "source",
        "localization_score",
        "relations",
        "enclosing_class",
        "intent_boost",
        "selection_score",
    ):
        if k in src and src[k] is not None:
            out[k] = src[k]
    return out


def _path_key(p: str) -> str:
    try:
        return str(Path(p).resolve())
    except OSError:
        return p or ""


def _build_search_debug_record(
    state: AgentState,
    query: str | None,
    raw_results: list[dict],
    candidate_pool: list[dict],
    final_rows: list[dict],
) -> dict:
    """Build structured search_debug record for pipeline diagnostics (non-invasive, observable)."""
    search_debug: dict = {
        "query": query or "",
        "retrieved_count": len(raw_results),
        "retrieved_files": [str(r.get("file", "")) for r in raw_results[:10] if isinstance(r, dict) and r.get("file")],
        "candidate_pool_count": len(candidate_pool),
        "candidate_pool_files": [
            str(r.get("file", "")) for r in candidate_pool[:10] if isinstance(r, dict) and r.get("file")
        ],
        "has_impl_in_pool": any(
            isinstance(r, dict) and r.get("implementation_body_present") for r in candidate_pool
        ),
        "has_linked_in_pool": any(
            isinstance(r, dict)
            and isinstance(r.get("relations"), list)
            and r.get("relations")
            for r in candidate_pool
        ),
    }
    final_count = len(final_rows)
    final_files = [str(r.get("file", "")) for r in final_rows[:10] if isinstance(r, dict) and r.get("file")]
    final_has_impl = any(
        isinstance(r, dict) and r.get("implementation_body_present") for r in final_rows
    )
    final_has_linked = any(
        isinstance(r, dict)
        and isinstance(r.get("relations"), list)
        and r.get("relations")
        for r in final_rows
    )
    search_debug.update({
        "final_count": final_count,
        "final_files": final_files,
        "final_has_impl": final_has_impl,
        "final_has_linked": final_has_linked,
        "selector_used": bool(state.context.get("bundle_selector_used")),
    })
    # Phase 2: derived signals (no heuristics, just signals)
    search_debug["retrieval_empty"] = search_debug["retrieved_count"] == 0
    search_debug["pool_has_signal"] = (
        search_debug["has_impl_in_pool"] or search_debug["has_linked_in_pool"]
    )
    search_debug["final_has_signal"] = search_debug["final_has_impl"] or search_debug["final_has_linked"]
    search_debug["selection_loss"] = (
        search_debug["pool_has_signal"] and not search_debug["final_has_signal"]
    )
    return search_debug


MAX_RELATIONS_PER_ROW = 2
MAX_RELATIONS_TOTAL = 8


def _file_to_first_symbol_id(storage: Any) -> dict[str, int]:
    """One pass over nodes: map resolved file path -> first symbol id (O(n), not O(n*candidates))."""
    out: dict[str, int] = {}
    try:
        for n in storage.get_all_nodes():
            fk = _path_key(n.get("file") or "")
            if not fk or fk in out:
                continue
            sid = n.get("id")
            if sid is not None:
                out[fk] = int(sid)
    except (TypeError, ValueError, OSError):
        return out
    return out


def _attach_relationship_links(
    candidates: list[dict],
    project_root: str,
    graph_skipped: bool,
) -> list[dict]:
    """
    Attach bounded relations: ownership (symbol->file), import (file->file via graph),
    call only when a direct graph edge exists to another retrieved symbol/file.
    """
    if graph_skipped or not candidates:
        return candidates
    index_path = Path(project_root) / SYMBOL_GRAPH_DIR / INDEX_SQLITE
    if not index_path.is_file():
        return candidates
    try:
        from repo_graph.graph_query import get_callees, get_imports
        from repo_graph.graph_storage import GraphStorage
    except ImportError:
        return candidates

    files_pool = {_path_key(c.get("file") or "") for c in candidates if isinstance(c, dict) and c.get("file")}
    files_pool.discard("")

    symbol_keys: set[tuple[str, str]] = set()
    for c in candidates:
        if not isinstance(c, dict):
            continue
        fp = _path_key(c.get("file") or "")
        sym = (c.get("symbol") or "").strip()
        if fp and sym:
            symbol_keys.add((fp, sym.lower()))

    total_links = 0
    storage = GraphStorage(str(index_path))
    try:
        try:
            file_first_id = _file_to_first_symbol_id(storage)
        except Exception as e:
            logger.warning("[retrieval_pipeline] relationship link file index failed: %s", e)
            return candidates
        out: list[dict] = []
        for c in candidates:
            if not isinstance(c, dict):
                out.append(c)
                continue
            row = dict(c)
            rels: list[dict] = []
            fp = row.get("file") or ""
            fp_key = _path_key(fp)
            sym = (row.get("symbol") or "").strip()

            if total_links >= MAX_RELATIONS_TOTAL:
                out.append(row)
                continue

            # 1) Ownership (symbol belongs to file)
            if sym and fp_key and total_links < MAX_RELATIONS_TOTAL and len(rels) < MAX_RELATIONS_PER_ROW:
                rels.append({"kind": "ownership", "target_file": fp_key})
                total_links += 1

            sid: int | None = None
            if sym:
                node = storage.get_symbol_by_name(sym)
                if node and _path_key(node.get("file") or "") == fp_key:
                    sid = node.get("id")
                    if sid is not None:
                        sid = int(sid)
            if sid is None and fp_key:
                sid = file_first_id.get(fp_key)

            # 2) Import edges (file-path based; graph primitive only)
            if (
                sid is not None
                and len(rels) < MAX_RELATIONS_PER_ROW
                and total_links < MAX_RELATIONS_TOTAL
            ):
                for imp in get_imports(sid, storage):
                    if total_links >= MAX_RELATIONS_TOTAL or len(rels) >= MAX_RELATIONS_PER_ROW:
                        break
                    tfile = _path_key(imp.get("file") or "")
                    tname = (imp.get("name") or "")[:128]
                    if tfile and tfile in files_pool and tfile != fp_key:
                        rels.append({"kind": "import", "target_file": tfile, "target_symbol": tname})
                        total_links += 1
                        break

            # 3) Call edges: direct graph callee only, both endpoints in candidate pool
            if (
                sid is not None
                and len(rels) < MAX_RELATIONS_PER_ROW
                and total_links < MAX_RELATIONS_TOTAL
            ):
                for callee in get_callees(sid, storage):
                    if total_links >= MAX_RELATIONS_TOTAL or len(rels) >= MAX_RELATIONS_PER_ROW:
                        break
                    tfile = _path_key(callee.get("file") or "")
                    tname = (callee.get("name") or "").strip()
                    if not tfile or not tname:
                        continue
                    if (tfile, tname.lower()) not in symbol_keys:
                        continue
                    rels.append({"kind": "call", "target_file": tfile, "target_symbol": tname[:128]})
                    total_links += 1
                    break

            if rels:
                row["relations"] = rels[:MAX_RELATIONS_PER_ROW]
            out.append(row)
        return out
    finally:
        storage.close()


def _build_candidates_from_context(built: dict) -> list[dict]:
    """Build ranker candidates from context_builder output. Snippets are {file, symbol, snippet}."""
    candidates: list[dict] = []
    for s in built.get("symbols") or []:
        if isinstance(s, dict):
            row = {
                "file": s.get("file") or "",
                "symbol": s.get("symbol") or "",
                "snippet": s.get("snippet") or "",
                "type": "symbol",
                "candidate_kind": s.get("candidate_kind") or "symbol",
                **({"line": s["line"]} if s.get("line") is not None else {}),
            }
            row.update(_typed_fields_from_row(s))
            candidates.append(row)
    for r in built.get("references") or []:
        if isinstance(r, dict):
            snippet = r.get("snippet") or f"{r.get('symbol', '')} at line {r.get('line', '?')}"
            row = {
                "file": r.get("file") or "",
                "symbol": r.get("symbol") or "",
                "snippet": snippet,
                "type": "reference",
                "candidate_kind": r.get("candidate_kind") or "reference",
                **({"line": r["line"]} if r.get("line") is not None else {}),
            }
            row.update(_typed_fields_from_row(r))
            candidates.append(row)
    for snip in built.get("snippets") or []:
        if isinstance(snip, dict):
            row = {
                "file": snip.get("file") or "",
                "symbol": snip.get("symbol") or "",
                "snippet": snip.get("snippet") or "",
                "type": "file",
                "candidate_kind": snip.get("candidate_kind") or "file",
            }
            row.update(_typed_fields_from_row(snip))
            candidates.append(row)
        elif isinstance(snip, str) and snip:
            candidates.append({"file": "", "symbol": "", "snippet": snip, "type": "file", "candidate_kind": "file"})
    return candidates


def run_retrieval_pipeline(
    search_results: list[dict],
    state: AgentState,
    query: str | None = None,
) -> dict:
    """
    Anchor detection → expand → read_symbol_body/read_file → find_referencing_symbols → build_context.
    When the retrieval daemon is available, embedding and reranking are routed through it
    (daemon-backed inference path). Updates state.context (retrieved_*, context_snippets, ranked_context).
    Returns aggregated result for the SEARCH step.
    """
    raw_results = (search_results or [])[:MAX_SEARCH_RESULTS]
    # Reset selector-derived compaction state for this retrieval pass.
    state.context["bundle_selector_used"] = False
    state.context["bundle_selector_selected_pool"] = []
    state.context["bundle_selector_dropped_ids"] = []
    project_root = state.context.get("project_root") or os.environ.get("SERENA_PROJECT_DIR") or os.getcwd()
    # Probe BM25 without crashing the pipeline: rank_bm25 pulls numpy; some numpy builds
    # recurse on import in nested loader contexts (RecursionError, not ImportError).
    try:
        import rank_bm25  # noqa: F401

        state.context["bm25_available"] = True
    except ImportError:
        state.context["bm25_available"] = False
    except RecursionError:
        logger.warning(
            "[retrieval_pipeline] rank_bm25 import failed with RecursionError "
            "(numpy/import loader); bm25 marked unavailable"
        )
        state.context["bm25_available"] = False
    state.context["reranker_failed"] = False
    state.context["reranker_failed_fallback_used"] = False
    ctx = state.context or {}
    _src = ctx.get("source_root")
    _extra_roots = (str(_src),) if _src else None
    results = filter_and_rank_search_results(
        raw_results,
        query,
        str(project_root),
        parent_instruction=ctx.get("parent_instruction"),
        extra_path_roots=_extra_roots,
    )
    for _r in results:
        if isinstance(_r, dict):
            _r["snippet"] = coerce_snippet_text(_r.get("snippet"))
    state.context["search_viable_raw_hits"] = len(raw_results)
    state.context["search_viable_file_hits"] = len(results)
    if not results:
        _maybe_seed_ranked_context_when_search_empty(state, project_root, query)
        state.context["retrieval_candidate_pool"] = []
        state.context["retrieval_candidate_pool_count"] = 0
        state.context["retrieval_candidate_pool_has_impl"] = False
        state.context["retrieval_candidate_pool_linked_count"] = 0
        _record = _build_search_debug_record(
            state, query, raw_results, [], state.context.get("ranked_context") or []
        )
        state.context.setdefault("search_debug_records", []).append(_record)
        return {"results": [], "query": query or "", "anchors": 0}

    # Explicitly detect daemon availability; when active, route embedding + rerank through daemon only
    try:
        from agent.retrieval.daemon_client import retrieval_daemon_available

        if retrieval_daemon_available():
            state.context["retrieval_via_daemon"] = True
            logger.info(
                "[retrieval_pipeline] daemon active — routing retrieval through daemon (embed + rerank)"
            )
        else:
            state.context["retrieval_via_daemon"] = False
    except Exception as e:
        logger.debug("[retrieval_pipeline] daemon check skipped: %s", e)
        state.context["retrieval_via_daemon"] = False

    anchors = detect_anchors(results, query)
    if not anchors and results:
        anchors = list(results[:FALLBACK_TOP_N])
        logger.info("[retrieval_pipeline] anchor fallback: using top %d filtered hits", len(anchors))
    if not anchors:
        _maybe_seed_ranked_context_when_search_empty(state, project_root, query)
        state.context["retrieval_candidate_pool"] = []
        state.context["retrieval_candidate_pool_count"] = 0
        state.context["retrieval_candidate_pool_has_impl"] = False
        state.context["retrieval_candidate_pool_linked_count"] = 0
        _record = _build_search_debug_record(
            state, query, raw_results, [], state.context.get("ranked_context") or []
        )
        state.context.setdefault("search_debug_records", []).append(_record)
        return {"results": results, "query": query or "", "anchors": 0}

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
    pipe_metrics = RetrievalMetrics(
        trace_id=state.context.get("trace_id"),
        query_id=state.context.get("query_id"),
        step_id=state.context.get("current_step_id"),
    )

    # Ext Step 4: Graph index fallback — skip graph expansion when index absent
    index_path = Path(project_root) / SYMBOL_GRAPH_DIR / INDEX_SQLITE
    graph_stage_skipped = not index_path.is_file()
    if graph_stage_skipped:
        retrieval_metrics["graph_stage_skipped"] = True
        symbol_snippets = []
    else:
        pipe_metrics.start("graph_expand")
        graph_telemetry: dict = {}
        symbol_snippets = expand_from_anchors(
            anchors, query or "", project_root, graph_telemetry_out=graph_telemetry
        )
        pipe_metrics.end("graph_expand")
        retrieval_metrics.update(graph_telemetry)
        retrieval_metrics["graph_stage_skipped"] = False

    state.context["retrieval_metrics"] = retrieval_metrics

    pipe_metrics.start("symbol_expand")
    expanded = expand_search_results(anchors)
    symbol_results = []
    reference_results = []
    file_snippets = []
    for item in expanded:
        path = _resolve_path(item.get("file") or "", project_root)
        symbol = item.get("symbol") or ""
        action_type = item.get("action") or "read_file"
        line = item.get("line")
        ck_in = (item.get("candidate_kind") or "").strip().lower()
        try:
            if action_type == "read_symbol_body" and symbol:
                body = read_symbol_body(symbol, path, line=line)
                impl_ok = bool(body and str(body).strip())
                ck = ck_in or "symbol"
                fs_row: dict = {"file": path, "snippet": body, "symbol": symbol, "candidate_kind": ck}
                sr: dict = {"file": path, "symbol": symbol, "snippet": body[:500], "candidate_kind": ck}
                if impl_ok:
                    fs_row["retrieval_result_type"] = RETRIEVAL_RESULT_TYPE_SYMBOL_BODY
                    fs_row["implementation_body_present"] = True
                    sr["retrieval_result_type"] = RETRIEVAL_RESULT_TYPE_SYMBOL_BODY
                    sr["implementation_body_present"] = True
                lr = item.get("line_range")
                if lr is not None:
                    fs_row["line_range"] = lr
                    sr["line_range"] = lr
                file_snippets.append(fs_row)
                if line is not None:
                    sr["line"] = line
                symbol_results.append(sr)
                # Enclosing class name (cheap metadata); must not block symbol body retrieval on read errors
                try:
                    ft = read_file(path)
                    lines = (ft or "").splitlines()
                    eline = int(line) if line is not None else 1
                    enc = extract_enclosing_class_name(lines, eline) if lines else ""
                    if enc:
                        fs_row["enclosing_class"] = enc
                        sr["enclosing_class"] = enc
                except Exception:
                    pass
            elif action_type == "read_region_bounded":
                lr = item.get("line_range")
                region_text, impl_flag = expand_region_bounded(path, lr)
                ck = ck_in or "region"
                fs_row = {
                    "file": path,
                    "snippet": region_text,
                    "symbol": symbol,
                    "candidate_kind": ck,
                }
                sr = {"file": path, "symbol": symbol, "snippet": region_text[:500], "candidate_kind": ck}
                if lr is not None:
                    fs_row["line_range"] = lr
                    sr["line_range"] = lr
                if line is not None:
                    sr["line"] = line
                if region_text.strip():
                    fs_row["retrieval_result_type"] = RETRIEVAL_RESULT_TYPE_REGION_BODY
                    sr["retrieval_result_type"] = RETRIEVAL_RESULT_TYPE_REGION_BODY
                if impl_flag is True:
                    fs_row["implementation_body_present"] = True
                    sr["implementation_body_present"] = True
                file_snippets.append(fs_row)
                symbol_results.append(sr)
            elif action_type == "read_file_header":
                header_text = expand_file_header(path)
                ck = ck_in or "file"
                fs_row = {
                    "file": path,
                    "snippet": header_text,
                    "symbol": symbol,
                    "candidate_kind": ck,
                    "retrieval_result_type": RETRIEVAL_RESULT_TYPE_FILE_HEADER,
                }
                file_snippets.append(fs_row)
                symbol_results.append({
                    "file": path,
                    "symbol": symbol,
                    "snippet": header_text[:500],
                    "candidate_kind": ck,
                    "retrieval_result_type": RETRIEVAL_RESULT_TYPE_FILE_HEADER,
                })
            else:
                content = read_file(path)
                snip = (content or "")[:2000]
                ck = ck_in or "file"
                file_snippets.append({
                    "file": path,
                    "snippet": snip,
                    "symbol": "",
                    "candidate_kind": ck,
                })
            refs = find_referencing_symbols(symbol or path, path, project_root=project_root)
            if isinstance(refs, dict):
                for key in ("callers", "callees", "imports", "referenced_by"):
                    reference_results.extend(refs.get(key) or [])
            else:
                reference_results.extend(refs if isinstance(refs, list) else [])
        except Exception as e:
            logger.warning("[retrieval_pipeline] expand %s: %s", path, e)
    pipe_metrics.end("symbol_expand")

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
    candidates = _attach_relationship_links(candidates, str(project_root), graph_stage_skipped)
    intent_label = classify_query_intent(query or "")
    state.context["retrieval_intent"] = intent_label
    candidates = apply_intent_bias(candidates, query or "")
    candidates = sorted(
        candidates,
        key=lambda c: float(c.get("selection_score") or 0.0) if isinstance(c, dict) else 0.0,
        reverse=True,
    )
    candidates = candidates[:MAX_RETRIEVAL_RESULTS]
    for _c in candidates:
        if isinstance(_c, dict):
            _c["snippet"] = coerce_snippet_text(_c.get("snippet"))
    state.context["context_candidates"] = candidates

    had_impl_body_pre_pipeline = _rows_have_implementation_body(candidates)
    had_symbol_kind_pre_dedupe = any(
        isinstance(c, dict) and c.get("candidate_kind") == "symbol" for c in candidates
    )

    # Step 5: Unconditional deduplication before reranker
    pre_dedupe_count = len(candidates)
    candidates = deduplicate_candidates(candidates)
    if had_impl_body_pre_pipeline and not _rows_have_implementation_body(candidates):
        logger.error(
            "[retrieval_pipeline] deduplicate_candidates removed all implementation_body_present rows"
        )
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
            pipe_metrics.start("rerank")
            t0 = time.monotonic()
            deduped = candidates  # already deduped above
            snippets = [coerce_snippet_text(c.get("snippet")) for c in deduped]
            scored = _reranker.rerank(rank_query, snippets)
            rerank_ms = int((time.monotonic() - t0) * 1000)
            pipe_metrics.end("rerank")
            total_tokens = sum(len(s.split()) for s in snippets)

            from agent.retrieval.reranker.hardware import detect_hardware  # noqa: PLC0415
            device = detect_hardware()

            reranked = _apply_reranker_scores(deduped, scored, RERANKER_TOP_K)
            impact = _compute_rerank_impact(deduped, reranked)
            build_selector_candidate_pool(
                state, reranked, intent_label,
                max_size=MAX_SELECTOR_CANDIDATE_POOL,
                min_size=MIN_SELECTOR_CANDIDATE_POOL,
            )
            pipe_metrics.start("context_prune")
            final_context = prune_context(
                reranked, max_snippets=MAX_CONTEXT_SNIPPETS, max_chars=DEFAULT_MAX_CHARS
            )
            pipe_metrics.end("context_prune")
            _log_rerank_telemetry(
                state, rerank_ms, device,
                candidates_in, len(deduped), len(final_context),
                total_tokens, skipped_reason=None, impact=impact,
            )
        except Exception as exc:
            logger.warning(
                "[retrieval_pipeline] reranker inference failed — using retriever-score ordering: %s: %s",
                type(exc).__name__,
                exc,
            )
            state.context["reranker_failed"] = True
            state.context["reranker_failed_fallback_used"] = True
            _skipped_reason = f"inference_error:{type(exc).__name__}"
            _reranker = None  # trigger fallback below
    elif _reranker is None and RERANKER_ENABLED:
        _skipped_reason = "disabled"
    elif _bypass:
        _skipped_reason = f"symbol_query:{_bypass_reason}"
    elif candidates and len(candidates) < RERANK_MIN_CANDIDATES:
        _skipped_reason = "below_min_candidates"

    # Fallback: retriever-score ordering when reranker was skipped or failed (no LLM fallback)
    if not final_context:
        if candidates:
            ranked = sorted(
                candidates,
                key=lambda c: float(
                    c.get("selection_score")
                    if isinstance(c, dict) and c.get("selection_score") is not None
                    else (c.get("retriever_score") or 0.0)
                ),
                reverse=True,
            )
            build_selector_candidate_pool(
                state, ranked, intent_label,
                max_size=MAX_SELECTOR_CANDIDATE_POOL,
                min_size=MIN_SELECTOR_CANDIDATE_POOL,
            )
            pipe_metrics.start("context_prune")
            final_context = prune_context(
                ranked, max_snippets=MAX_CONTEXT_SNIPPETS, max_chars=DEFAULT_MAX_CHARS
            )
            pipe_metrics.end("context_prune")
        if _skipped_reason:
            _log_rerank_telemetry(
                state, 0, "none",
                len(candidates), len(candidates), len(final_context),
                0, skipped_reason=_skipped_reason,
            )
            if candidates:
                logger.info(
                    "[retrieval] Reranker skipped (%s); using retriever-score ordering. "
                    "Ensure reranker loads for faster ranking: pip install onnxruntime, run download_reranker.py",
                    _skipped_reason,
                )

    # Ensure selector pool is set when no candidates (edge case)
    if "retrieval_candidate_pool" not in state.context:
        state.context["retrieval_candidate_pool"] = []
        state.context["retrieval_candidate_pool_count"] = 0
        state.context["retrieval_candidate_pool_has_impl"] = False
        state.context["retrieval_candidate_pool_linked_count"] = 0

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

    final_context, inject_n = _inject_instruction_path_snippets(
        final_context, state, project_root, query
    )
    if inject_n:
        rm = state.context.get("retrieval_metrics") or {}
        rm["instruction_path_injects"] = inject_n
        state.context["retrieval_metrics"] = rm

    state.context["ranked_context"] = final_context
    state.context["ranking_scores"] = []
    pipe_metrics.log()

    if had_impl_body_pre_pipeline and not _rows_have_implementation_body(final_context):
        logger.error(
            "[retrieval_pipeline] invariant: had implementation_body_present in candidates "
            "but none in ranked_context (check rerank/prune/compression)"
        )

    if had_symbol_kind_pre_dedupe and not any(
        isinstance(r, dict) and r.get("candidate_kind") == "symbol" for r in (final_context or [])
    ):
        logger.warning(
            "[retrieval_pipeline] typed symbol candidates existed pre-dedupe but none in ranked_context"
        )

    _ri = state.context.get("retrieval_intent")
    if _ri == INTENT_ARCHITECTURE and final_context:
        rows = [r for r in final_context if isinstance(r, dict)]
        if rows and all((r.get("candidate_kind") == "file") for r in rows):
            _has_sym_reg = any(
                r.get("retrieval_result_type")
                in (RETRIEVAL_RESULT_TYPE_SYMBOL_BODY, RETRIEVAL_RESULT_TYPE_REGION_BODY)
                for r in rows
            )
            if not _has_sym_reg:
                logger.warning(
                    "[retrieval_pipeline] architecture intent: final context has only file rows "
                    "and no symbol/region body"
                )

    search_memory = state.context.get("search_memory") or {}
    if isinstance(search_memory, dict):
        search_memory = dict(search_memory)
        existing = list(search_memory.get("results") or [])
        seen_mem: set[str] = set()
        for sym in (built.get("symbols") or [])[:16]:
            if not isinstance(sym, dict):
                continue
            row: dict = {
                "file": normalize_file_path(sym.get("file") or ""),
                "snippet": (sym.get("snippet") or "")[:500],
            }
            if sym.get("candidate_kind"):
                row["candidate_kind"] = sym["candidate_kind"]
            if sym.get("retrieval_result_type"):
                row["retrieval_result_type"] = sym["retrieval_result_type"]
            if "implementation_body_present" in sym:
                row["implementation_body_present"] = sym["implementation_body_present"]
            if sym.get("line") is not None:
                try:
                    row["line"] = int(sym["line"])
                except (TypeError, ValueError):
                    row["line"] = sym["line"]
            if sym.get("line_range") is not None:
                row["line_range"] = sym["line_range"]
            if sym.get("relations"):
                row["relations"] = sym["relations"]
            if sym.get("enclosing_class"):
                row["enclosing_class"] = sym["enclosing_class"]
            mk = retrieval_row_identity_key(row)
            if mk in seen_mem:
                continue
            seen_mem.add(mk)
            existing.append(row)
        for s in built.get("snippets", [])[:5]:
            snip = coerce_snippet_text(s.get("snippet", "") if isinstance(s, dict) else s)
            stub = {"file": "", "snippet": snip[:500]}
            mk = retrieval_row_identity_key(stub)
            if mk not in seen_mem:
                seen_mem.add(mk)
                existing.append(stub)
        search_memory["results"] = existing
        state.context["search_memory"] = search_memory

    candidate_pool = state.context.get("retrieval_candidate_pool") or []
    final_rows = state.context.get("ranked_context") or []
    _record = _build_search_debug_record(state, query, raw_results, candidate_pool, final_rows)
    state.context.setdefault("search_debug_records", []).append(_record)

    return {
        "results": results,
        "query": query or "",
        "anchors": len(anchors),
        "expanded": len(expanded),
        "symbols": len(built.get("symbols", [])),
        "references": len(built.get("references", [])),
    }
