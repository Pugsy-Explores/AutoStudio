"""Batched callers/callees from SQLite symbol graph (single GraphStorage session)."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from agent_v2.config import (
    EXPLORATION_REPO_GRAPH_INDEX_SQLITE,
    EXPLORATION_REPO_SYMBOL_GRAPH_DIR,
    EXPLORATION_SYMBOL_AWARE_LOG_PROGRESS,
)
from repo_graph.graph_query import get_callers, get_callees

_LOG = logging.getLogger(__name__)


def _resolve_project_root(project_root: str | None) -> Path:
    raw = (project_root or os.environ.get("SERENA_PROJECT_DIR") or os.getcwd()).strip()
    return Path(raw).resolve()


def _canon_file(p: str, root: Path) -> str:
    raw = str(p or "").strip()
    if not raw:
        return ""
    path = Path(raw)
    if not path.is_absolute():
        path = root / raw
    try:
        return str(path.resolve())
    except OSError:
        return str(path)


def _pick_node_for_symbol(
    storage: Any,
    symbol_name: str,
    candidate_file: str,
    project_root: Path,
) -> dict | None:
    """Disambiguate: exact file > same parent dir > highest call-graph degree."""
    nodes = storage.list_nodes_by_exact_name(symbol_name, limit=200)
    if not nodes:
        return None
    want = _canon_file(candidate_file, project_root)
    want_parent = str(Path(want).parent) if want else ""

    def parent_of(fp: str) -> str:
        return str(Path(fp).parent) if fp else ""

    exact = [n for n in nodes if _canon_file(str(n.get("file") or ""), project_root) == want]
    if len(exact) == 1:
        return exact[0]
    pool = exact if exact else nodes

    same_dir = [
        n
        for n in pool
        if want_parent and parent_of(str(n.get("file") or "")) == want_parent
    ]
    pool2 = same_dir if same_dir else pool

    best: dict | None = None
    best_deg = -1
    for n in pool2:
        nid = n.get("id")
        if nid is None:
            continue
        try:
            deg = storage.count_call_graph_degree(int(nid))
        except (TypeError, ValueError):
            deg = 0
        if deg > best_deg:
            best_deg = deg
            best = n
    return best


def _node_row_to_ref(n: dict) -> str:
    fp = str(n.get("file") or "").strip()
    name = str(n.get("name") or "").strip()
    line = n.get("start_line")
    if fp and name:
        if isinstance(line, int) and line > 0:
            return f"{fp}:{line}::{name}"
        return f"{fp}::{name}"
    return name or fp or ""


def fetch_callers_callees_batch(
    items: list[tuple[str, str]],
    project_root: str | None,
    *,
    k_each: int = 5,
) -> dict[str, dict[str, list[str]]]:
    """
    Single DB session. items: (candidate_file_path, symbol_name).

    Returns:
        symbol_name -> {"callers": [...], "callees": [...]} string refs
    """
    out: dict[str, dict[str, list[str]]] = {}
    if not items or k_each <= 0:
        return out

    root = _resolve_project_root(project_root)
    # Dedupe (canonical_file, symbol) preserving order — avoids redundant SQLite work.
    _deduped: list[tuple[str, str]] = []
    _seen_cf_sym: set[tuple[str, str]] = set()
    for cand_file, sym in items:
        s = str(sym or "").strip()
        if not s:
            continue
        cf = _canon_file(cand_file, root)
        key = (cf, s)
        if key in _seen_cf_sym:
            continue
        _seen_cf_sym.add(key)
        _deduped.append((cand_file, s))
    items = _deduped
    if not items:
        return out
    index_path = root / EXPLORATION_REPO_SYMBOL_GRAPH_DIR / EXPLORATION_REPO_GRAPH_INDEX_SQLITE
    if EXPLORATION_SYMBOL_AWARE_LOG_PROGRESS:
        _LOG.info(
            "exploration.symbol_aware graph_sqlite batch pairs=%s k_each=%s index=%s exists=%s",
            len(items),
            k_each,
            index_path.name,
            index_path.is_file(),
        )
    if not index_path.is_file():
        return out

    try:
        from repo_graph.graph_storage import GraphStorage
    except ImportError as e:
        _LOG.debug("[graph_symbol_edges_batch] import: %s", e)
        return out

    storage: Any = None
    try:
        storage = GraphStorage(str(index_path))
        for cand_file, sym in items:
            s = str(sym or "").strip()
            if not s:
                continue
            node = _pick_node_for_symbol(storage, s, cand_file, root)
            if not node:
                out[s] = {"callers": [], "callees": []}
                continue
            nid = node.get("id")
            if nid is None:
                out[s] = {"callers": [], "callees": []}
                continue
            try:
                nid_i = int(nid)
            except (TypeError, ValueError):
                out[s] = {"callers": [], "callees": []}
                continue
            cr = get_callers(nid_i, storage)
            ce = get_callees(nid_i, storage)
            out[s] = {
                "callers": [_node_row_to_ref(x) for x in cr[:k_each]],
                "callees": [_node_row_to_ref(x) for x in ce[:k_each]],
            }
        n_edges = sum(
            1
            for v in out.values()
            if (v.get("callers") or v.get("callees"))
        )
        if EXPLORATION_SYMBOL_AWARE_LOG_PROGRESS:
            _LOG.info(
                "exploration.symbol_aware graph_sqlite batch_done symbols=%s with_nonempty_edges=%s",
                list(out.keys()),
                n_edges,
            )
        return out
    except Exception as exc:
        _LOG.warning("[graph_symbol_edges_batch] error: %s", exc)
        return out
    finally:
        if storage is not None:
            storage.close()


def format_symbol_relationships_block(
    edges_by_symbol: dict[str, dict[str, list[str]]],
    *,
    max_chars: int,
) -> str:
    if not edges_by_symbol or max_chars <= 0:
        return ""
    lines: list[str] = [
        "--------------------------------",
        "SYMBOL RELATIONSHIPS",
        "--------------------------------",
    ]
    for sym in sorted(edges_by_symbol.keys()):
        bucket = edges_by_symbol[sym]
        lines.append(f"{sym}:")
        lines.append(f"  callers: {bucket.get('callers') or []}")
        lines.append(f"  callees: {bucket.get('callees') or []}")
    text = "\n".join(lines)
    if len(text) > max_chars:
        return text[: max_chars - 20] + "\n... [truncated]"
    return text
