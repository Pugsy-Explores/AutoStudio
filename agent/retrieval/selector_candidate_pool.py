"""
LLM bundle selector candidate pool: pre-prune pool with stable IDs and deterministic guardrails.
Data-path only; no LLM calls. Used to prepare a bounded, useful pool for future selector steps.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent.retrieval.result_contract import (
    RETRIEVAL_RESULT_TYPE_REGION_BODY,
    RETRIEVAL_RESULT_TYPE_SYMBOL_BODY,
)
from agent.retrieval.retrieval_intent import INTENT_ARCHITECTURE
from agent.retrieval.reranker.deduplicator import retrieval_row_identity_key

if TYPE_CHECKING:
    from agent.memory.state import AgentState


def assign_stable_candidate_ids(pool: list[dict]) -> list[dict]:
    """
    Assign deterministic stable IDs to each pool row (rc_0001, rc_0002, ...).
    IDs are stable for the same ordered pool within a run.
    """
    out: list[dict] = []
    for i, row in enumerate(pool):
        r = dict(row)
        r["candidate_id"] = f"rc_{i + 1:04d}"
        out.append(r)
    return out


def _is_implementation_backed(row: dict) -> bool:
    """True if row has real implementation content (symbol/region body)."""
    if row.get("implementation_body_present") is True:
        return True
    rrt = row.get("retrieval_result_type") or ""
    return rrt in (RETRIEVAL_RESULT_TYPE_SYMBOL_BODY, RETRIEVAL_RESULT_TYPE_REGION_BODY)


def _is_placeholder_only(row: dict) -> bool:
    """True if row has no implementation body (graph stub, file header only)."""
    return not _is_implementation_backed(row)


def _has_relations(row: dict) -> bool:
    """True if row has relationship links (import, call, etc.)."""
    rels = row.get("relations")
    return isinstance(rels, list) and len(rels) > 0


def _to_pool_row(c: dict) -> dict:
    """Build a structured pool row from a candidate with required metadata fields."""
    row: dict = {
        "candidate_id": "",  # assigned later
        "file": str(c.get("file") or ""),
        "symbol": str(c.get("symbol") or ""),
        "snippet": str(c.get("snippet") or ""),
        "candidate_kind": str(c.get("candidate_kind") or "file"),
        "retrieval_result_type": c.get("retrieval_result_type"),
        "implementation_body_present": c.get("implementation_body_present"),
        "line": c.get("line"),
        "line_range": c.get("line_range"),
        "source": c.get("source"),
        "source_kind": c.get("source") if c.get("source") else None,
        "relations": c.get("relations"),
        "enclosing_class": c.get("enclosing_class"),
    }
    # Score fields
    for k in ("selection_score", "retriever_score", "final_score", "score"):
        if k in c and c[k] is not None:
            row[k] = c[k]
            break
    else:
        row["selection_score"] = 0.0
    return row


def apply_selector_pool_guardrails(
    candidates: list[dict],
    *,
    max_size: int,
    min_size: int,
    intent: str,
) -> list[dict]:
    """
    Apply deterministic guardrails to pre-prune candidates:
    - Remove exact duplicates
    - Remove placeholder-only graph rows when real impl rows exist
    - Preserve at least one implementation-backed row if any exist
    - Preserve linked rows for architecture intent when available
    - Cap pool to max_size after ranking/order
    """
    if not candidates:
        return []

    # 1. Deduplicate by row identity
    seen: set[str] = set()
    deduped: list[dict] = []
    for c in candidates:
        if not isinstance(c, dict):
            continue
        key = retrieval_row_identity_key(c)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(c)

    impl_rows = [c for c in deduped if _is_implementation_backed(c)]

    # 2. Remove placeholder-only rows when real impl rows exist
    if impl_rows:
        pool = [c for c in deduped if _is_implementation_backed(c)]
    else:
        pool = list(deduped)

    # 3. For architecture intent: prefer linked rows so they survive capping
    if intent == INTENT_ARCHITECTURE:
        order_map = {retrieval_row_identity_key(c): i for i, c in enumerate(pool)}
        pool.sort(key=lambda c: (0 if _has_relations(c) else 1, order_map.get(retrieval_row_identity_key(c), 999)))

    # 4. Cap to max_size (impl-backed already preserved in step 2; linked preferred in step 3)
    result = pool[:max_size]

    # 5. Convert to pool row shape
    pool_rows = [_to_pool_row(c) for c in result]
    return assign_stable_candidate_ids(pool_rows)


def build_selector_candidate_pool(
    state: "AgentState",
    pre_prune_candidates: list[dict],
    intent_label: str,
    *,
    max_size: int,
    min_size: int,
) -> None:
    """
    Build the selector candidate pool, apply guardrails, and store in state.context.
    Sets retrieval_candidate_pool, retrieval_candidate_pool_count, has_impl, linked_count.
    """
    pool = apply_selector_pool_guardrails(
        pre_prune_candidates,
        max_size=max_size,
        min_size=min_size,
        intent=intent_label,
    )
    state.context["retrieval_candidate_pool"] = pool
    state.context["retrieval_candidate_pool_count"] = len(pool)
    state.context["retrieval_candidate_pool_has_impl"] = any(
        r.get("implementation_body_present") for r in pool
    )
    state.context["retrieval_candidate_pool_linked_count"] = sum(
        1 for r in pool if _has_relations(r)
    )
