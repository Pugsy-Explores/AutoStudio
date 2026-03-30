"""
Controlled exploration tools: bounded graph-anchored expansion after bundle selection.
Exploration adds context from the pool only — never global search. Max 3 rows per tool.
"""

from __future__ import annotations


def _path_match(a: str, b: str) -> bool:
    """Normalize and compare paths (lowercase, forward slashes)."""
    if not a or not b:
        return False
    na = str(a).replace("\\", "/").strip().lower()
    nb = str(b).replace("\\", "/").strip().lower()
    return na == nb


def follow_relation(candidate_id: str, pool: list[dict]) -> list[dict]:
    """
    Return candidates connected via relations from given candidate.
    HARD LIMIT: 3 results max.
    """
    source = next((r for r in pool if str(r.get("candidate_id", "")) == str(candidate_id)), None)
    if not source:
        return []

    relations = source.get("relations") or []
    results: list[dict] = []
    seen_ids: set[str] = set()

    for rel in relations:
        if not isinstance(rel, dict):
            continue
        target_file = rel.get("target_file")
        if not target_file:
            continue
        for r in pool:
            cid = str(r.get("candidate_id", ""))
            if cid in seen_ids:
                continue
            if _path_match(r.get("file", ""), target_file):
                results.append(r)
                seen_ids.add(cid)
                if len(results) >= 3:
                    return results
    return results[:3]


def expand_symbol(candidate_id: str, pool: list[dict]) -> list[dict]:
    """
    Expand symbol body or nearby context for selected candidate.
    If already has impl, return as-is. Otherwise return same row (no-op fallback).
    """
    r = next((x for x in pool if str(x.get("candidate_id", "")) == str(candidate_id)), None)
    if not r:
        return []
    if r.get("implementation_body_present"):
        return [r]
    return [r]


def read_file_region(candidate_id: str, pool: list[dict]) -> list[dict]:
    """
    Return same-file candidates (cheap local expansion).
    HARD LIMIT: 3 results max.
    """
    r = next((x for x in pool if str(x.get("candidate_id", "")) == str(candidate_id)), None)
    if not r:
        return []
    file_path = r.get("file", "")
    if not file_path:
        return [r]
    results = [x for x in pool if _path_match(x.get("file", ""), file_path)]
    return results[:3]


def expand_from_node(candidate_id: str, pool: list[dict], seed_row: dict | None = None) -> list[dict]:
    """
    Deterministic tool choice: relations > impl > file region.
    If seed_row not provided, looks up by candidate_id in pool.
    """
    row = seed_row or next(
        (x for x in pool if str(x.get("candidate_id", "")) == str(candidate_id)), None
    )
    if not row:
        return []
    if row.get("relations"):
        return follow_relation(candidate_id, pool)
    if row.get("implementation_body_present"):
        return expand_symbol(candidate_id, pool)
    return read_file_region(candidate_id, pool)
