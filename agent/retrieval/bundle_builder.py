"""
Bundle builder: construct connected-component bundles from candidate pool using relations.
Deterministic, local, cheap. Used by bundle_selector when ENABLE_BUNDLE_SELECTION is on.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Any


def _path_key(p: str) -> str:
    """Normalize path for matching (lowercase, forward slashes). Deterministic across runs."""
    if not p:
        return ""
    return str(p).replace("\\", "/").strip().lower()


def _get_row_score(row: dict) -> float:
    """Extract score for ranking; lower = weaker for pruning."""
    for k in ("final_score", "selection_score", "retriever_score", "score"):
        v = row.get(k)
        if isinstance(v, (int, float)):
            return float(v)
    return 0.0


def _is_linked(row: dict) -> bool:
    return bool(isinstance(row.get("relations"), list) and row.get("relations"))


def _has_impl(row: dict) -> bool:
    return bool(row.get("implementation_body_present"))


def build_candidate_bundles(pool: list[dict], *, max_bundle_size: int = 6) -> list[dict]:
    """
    Build bundles (connected components) from candidate pool using relations.
    Returns list of bundles with stable bundle_id, candidate_ids, files, linked_count,
    impl_count, relation_edges. Skips trivial bundles (size < 2).
    When bundle exceeds max_bundle_size: keep impl+linked rows, trim low-score rows.
    """
    if not pool:
        return []

    id_to_row = {str(r.get("candidate_id", "")): r for r in pool if r.get("candidate_id")}
    if not id_to_row:
        return []

    # file_path -> [candidate_ids]
    file_to_ids: dict[str, list[str]] = defaultdict(list)
    for cid, row in id_to_row.items():
        fp = _path_key(row.get("file") or "")
        if fp:
            file_to_ids[fp].append(cid)

    # Build adjacency: for each row with relations, resolve target_file to candidate_ids
    adj: dict[str, set[str]] = defaultdict(set)
    for cid, row in id_to_row.items():
        rels = row.get("relations")
        if not isinstance(rels, list):
            continue
        for r in rels:
            if not isinstance(r, dict):
                continue
            tfile = _path_key(r.get("target_file") or "")
            if not tfile:
                continue
            tsym = (r.get("target_symbol") or "").strip().lower()
            targets = file_to_ids.get(tfile, [])
            for tid in targets:
                if tid == cid:
                    continue
                if tsym:
                    trow = id_to_row.get(tid)
                    if trow and (trow.get("symbol") or "").strip().lower() == tsym:
                        adj[cid].add(tid)
                        adj[tid].add(cid)
                    # fallback: link by file if no symbol match
                    elif not any(
                        (id_to_row.get(t) or {}).get("symbol", "").strip().lower() == tsym
                        for t in targets
                    ):
                        adj[cid].add(tid)
                        adj[tid].add(cid)
                else:
                    adj[cid].add(tid)
                    adj[tid].add(cid)

    # Connected components (BFS)
    visited: set[str] = set()
    components: list[list[str]] = []

    for start in id_to_row:
        if start in visited:
            continue
        comp: list[str] = []
        stack = [start]
        while stack:
            n = stack.pop()
            if n in visited:
                continue
            visited.add(n)
            comp.append(n)
            for neighbor in adj.get(n, []):
                if neighbor not in visited:
                    stack.append(neighbor)
        if comp:
            components.append(comp)

    # Build bundle dicts; skip trivial (size < 2)
    bundles: list[dict] = []
    for comp in components:
        if len(comp) < 2:
            continue

        # Smart pruning when over max: prefer impl+linked, trim low-score
        candidate_ids = list(comp)
        if len(candidate_ids) > max_bundle_size:
            rows_with_meta = [(cid, id_to_row.get(cid, {})) for cid in candidate_ids]
            prioritized = [cid for cid, r in rows_with_meta if _has_impl(r) or _is_linked(r)]
            trim_only = [cid for cid in candidate_ids if cid not in prioritized]
            trim_only.sort(
                key=lambda c: _get_row_score(id_to_row.get(c, {})),
                reverse=False,
            )
            prioritized.sort(
                key=lambda c: (
                    0 if _has_impl(id_to_row.get(c, {})) else 1,
                    0 if _is_linked(id_to_row.get(c, {})) else 1,
                    -_get_row_score(id_to_row.get(c, {})),
                ),
            )
            keep_ids: set[str] = set()
            for cid in prioritized:
                if len(keep_ids) >= max_bundle_size:
                    break
                keep_ids.add(cid)
            for cid in trim_only:
                if len(keep_ids) >= max_bundle_size:
                    break
                keep_ids.add(cid)
            candidate_ids = [c for c in candidate_ids if c in keep_ids]
            if len(candidate_ids) < 2:
                continue

        # Stable bundle_id
        canon = "|".join(sorted(candidate_ids))
        bundle_id = f"b_{hashlib.sha256(canon.encode()).hexdigest()[:8]}"

        files: set[str] = set()
        linked_count = 0
        impl_count = 0
        relation_edges = 0
        for cid in candidate_ids:
            r = id_to_row.get(cid, {})
            if r.get("file"):
                files.add(str(r.get("file")))
            if _is_linked(r):
                linked_count += 1
            if _has_impl(r):
                impl_count += 1
            rels = r.get("relations")
            if isinstance(rels, list):
                relation_edges += len(rels)

        bundles.append({
            "bundle_id": bundle_id,
            "candidate_ids": candidate_ids,
            "files": files,
            "linked_count": linked_count,
            "impl_count": impl_count,
            "relation_edges": relation_edges,
        })

    return bundles


def detect_bridge_candidates(pool: list[dict], bundles: list[dict]) -> None:
    """
    Identify candidates that link multiple bundles. Mutates pool rows in-place:
    row["is_bridge"] = True when candidate connects 2+ bundles.
    """
    cid_to_bundle: dict[str, str] = {}
    for b in bundles:
        bid = b.get("bundle_id", "")
        for cid in b.get("candidate_ids", []):
            cid_to_bundle[cid] = bid

    id_to_row = {str(r.get("candidate_id", "")): r for r in pool if r.get("candidate_id")}
    file_to_ids: dict[str, list[str]] = defaultdict(list)
    for cid, row in id_to_row.items():
        fp = _path_key(row.get("file") or "")
        if fp:
            file_to_ids[fp].append(cid)

    for row in pool:
        cid = str(row.get("candidate_id", ""))
        if not cid:
            continue
        row["is_bridge"] = False
        rels = row.get("relations")
        if not isinstance(rels, list) or not rels:
            continue
        touched_bundles: set[str] = set()
        for r in rels:
            if not isinstance(r, dict):
                continue
            tfile = _path_key(r.get("target_file") or "")
            for tid in file_to_ids.get(tfile, []):
                tb = cid_to_bundle.get(tid)
                if tb:
                    touched_bundles.add(tb)
        my_bundle = cid_to_bundle.get(cid)
        if my_bundle:
            touched_bundles.add(my_bundle)
        if len(touched_bundles) >= 2:
            row["is_bridge"] = True


def score_bundle(bundle: dict) -> float:
    """Score bundles for selection. Normalized by size to avoid large bundles dominating."""
    raw = (
        2.0 * bundle.get("linked_count", 0)
        + 1.5 * bundle.get("impl_count", 0)
        + 1.0 * len(bundle.get("files") or set())
        + 0.5 * bundle.get("relation_edges", 0)
    )
    size = len(bundle.get("candidate_ids") or []) or 1
    return raw / size


def top_bundles_by_score(bundles: list[dict], top_n: int = 3) -> list[dict]:
    """Return top N bundles by normalized score (descending)."""
    return sorted(bundles, key=score_bundle, reverse=True)[:top_n]
