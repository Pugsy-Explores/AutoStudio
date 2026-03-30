"""Deterministic context pruner for retrieval_pipeline_v2.

Rules (from migration plan §Issue 3 fix):
  Dedup key:  (path_normalized, symbol_normalized, snippet_hash[:16])
  Ordering:   strictly preserve input order (= RRF/reranker order).
              NO _KIND_RANK, NO re-sort of any kind.
  Tie-break:  first occurrence in input wins — duplicates dropped.
  Budget:     stop at max_snippets OR when cumulative chars >= max_chars.
              The last included snippet may be truncated to fit exactly.

Guarantee: given the same input list, this function always produces
           identical output (order, content, dedup decisions).
"""

from __future__ import annotations

import hashlib
import logging

logger = logging.getLogger(__name__)


def _dedup_key(row: dict) -> str:
    path_norm = (row.get("file") or row.get("path") or "").strip().lower()
    sym_norm = (row.get("symbol") or "").strip().lower()
    snip_norm = " ".join((row.get("snippet") or "").split())
    snip_hash = hashlib.sha256(
        snip_norm.encode("utf-8", errors="replace")
    ).hexdigest()[:16]
    return f"{path_norm}|{sym_norm}|{snip_hash}"


def prune_deterministic(
    rows: list[dict],
    max_snippets: int = 20,
    max_chars: int = 20_000,
) -> list[dict]:
    """Prune row list deterministically.

    Args:
        rows: ordered list of result dicts (RRF order must be preserved).
        max_snippets: hard cap on number of candidates returned.
        max_chars: hard cap on total snippet characters.

    Returns:
        Deduplicated, budget-limited list. Order is never changed.
    """
    seen: set[str] = set()
    result: list[dict] = []
    total_chars = 0

    for row in rows:
        if len(result) >= max_snippets:
            break

        key = _dedup_key(row)
        if key in seen:
            continue
        seen.add(key)

        snippet = row.get("snippet") or ""
        remaining = max_chars - total_chars
        if remaining <= 0:
            break

        out = dict(row)
        if len(snippet) > remaining:
            out["snippet"] = snippet[:remaining]
        result.append(out)
        total_chars += len(out["snippet"])

    logger.debug(
        "[prune_deterministic] input=%d deduped=%d output=%d total_chars=%d",
        len(rows),
        len(rows) - len(result),
        len(result),
        total_chars,
    )
    return result
