"""Localization engine: orchestrate dependency traversal, execution paths, symbol ranking."""

import logging
from pathlib import Path

from config.retrieval_config import MAX_DEPENDENCY_NODES
from agent.retrieval.localization.dependency_traversal import traverse_dependencies
from agent.retrieval.localization.execution_path_analyzer import build_execution_paths
from agent.retrieval.localization.symbol_ranker import rank_localization_candidates

logger = logging.getLogger(__name__)


def _extract_best_anchor(anchors: list[dict]) -> str | None:
    """Pick best anchor symbol name from anchors (search result dicts)."""
    if not anchors:
        return None
    for a in anchors:
        sym = a.get("symbol") or a.get("name_path")
        if sym:
            return str(sym)
    # Fallback: use file stem from first anchor
    first = anchors[0]
    if isinstance(first, dict) and first.get("file"):
        return Path(first["file"]).stem
    return None


def _merge(dep_result: dict, exec_paths: list[dict]) -> list[dict]:
    """Merge dependency nodes and execution path items; deduplicate by (file, symbol)."""
    seen: set[tuple[str, str]] = set()
    merged: list[dict] = []

    path_symbols: set[tuple[str, str]] = set()
    for ep in exec_paths:
        for item in ep.get("path", []):
            f = item.get("file", "")
            n = item.get("name", "")
            if f or n:
                path_symbols.add((f, n))

    for node in dep_result.get("candidate_symbols", []):
        f = node.get("file", "")
        n = node.get("name", "")
        key = (f, n)
        if key in seen:
            continue
        seen.add(key)
        merged.append({
            "file": f,
            "symbol": n,
            "name": n,
            "snippet": node.get("docstring", "") or "",
            "hop_distance": node.get("hop_distance", 999),
            "in_execution_path": key in path_symbols,
            "in_dependency_nodes": True,
        })

    for ep in exec_paths:
        for item in ep.get("path", []):
            f = item.get("file", "")
            n = item.get("name", "")
            key = (f, n)
            if key in seen:
                continue
            seen.add(key)
            merged.append({
                "file": f,
                "symbol": n,
                "name": n,
                "snippet": "",
                "hop_distance": 999,
                "in_execution_path": True,
                "in_dependency_nodes": False,
            })

    return merged


def localize_issue(
    query: str,
    anchors: list[dict],
    project_root: str,
    trace_id: str = "",
) -> list[dict]:
    """
    Combine all localization stages. Returns ranked candidates.
    Each item: {file, symbol, snippet, localization_score, source: "localization", type: "localization"}.
    """
    anchor_symbol = _extract_best_anchor(anchors)
    if not anchor_symbol:
        logger.debug("[localization_engine] no anchor symbol")
        return []

    if trace_id:
        try:
            from agent.observability.trace_logger import log_event
            log_event(trace_id, "localization_anchor_detected", {"anchor": anchor_symbol})
        except Exception:
            pass

    dep_result = traverse_dependencies(anchor_symbol, project_root)
    if trace_id:
        try:
            from agent.observability.trace_logger import log_event
            log_event(
                trace_id,
                "dependency_traversal_complete",
                {
                    "node_count": dep_result.get("node_count", 0),
                    "file_count": len(dep_result.get("candidate_files", [])),
                },
            )
        except Exception:
            pass

    exec_paths = build_execution_paths(anchor_symbol, project_root)
    if trace_id:
        try:
            from agent.observability.trace_logger import log_event
            log_event(trace_id, "execution_paths_built", {"path_count": len(exec_paths)})
        except Exception:
            pass

    candidates = _merge(dep_result, exec_paths)
    if not candidates:
        return []

    ranked = rank_localization_candidates(candidates, anchor_symbol, query)  # type: ignore[arg-type]
    top_k = ranked[:MAX_DEPENDENCY_NODES]

    if trace_id:
        try:
            from agent.observability.trace_logger import log_event
            top = top_k[0] if top_k else {}
            log_event(
                trace_id,
                "localization_ranked",
                {
                    "ranked_count": len(top_k),
                    "top_file": top.get("file", ""),
                    "top_symbol": top.get("symbol", ""),
                },
            )
        except Exception:
            pass

    # Format for retrieval pipeline: {file, symbol, snippet, type, localization_score}
    out: list[dict] = []
    for r in top_k:
        snippet = r.get("snippet", "") or f"Symbol from graph: {r.get('name', r.get('symbol', ''))}"
        out.append({
            "file": r.get("file", ""),
            "symbol": r.get("symbol", ""),
            "snippet": snippet,
            "type": "localization",
            "localization_score": r.get("localization_score", 0.0),
            "source": "localization",
        })
    return out
