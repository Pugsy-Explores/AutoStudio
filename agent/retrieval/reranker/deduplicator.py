"""Candidate deduplication before reranking.

Rows are keyed by ``retrieval_row_identity_key`` (file, symbol, line, line_range,
candidate_kind, snippet hash) so distinct region/reference hits are not collapsed.
"""

from __future__ import annotations

import hashlib


def _snippet_hash(snippet: str) -> str:
    return hashlib.sha256(snippet.encode("utf-8", errors="replace")).hexdigest()


def retrieval_row_identity_key(c: dict) -> str:
    """
    Stable identity for a retrieval row across dedupe / prune.
    Includes file, symbol, line, line_range, candidate_kind, and snippet hash so region
    rows and multi-hit references do not collapse incorrectly.
    """
    file_path = (c.get("file") or "")[:512]
    sym = (c.get("symbol") or "")[:256]
    kind = (c.get("candidate_kind") or "")[:64]
    line = c.get("line")
    if isinstance(line, (int, float)):
        line_key = str(int(line))
    elif line is not None:
        line_key = str(line)
    else:
        line_key = "0"
    lr = c.get("line_range")
    lr_key = repr(lr) if lr is not None else ""
    snip_h = _snippet_hash(c.get("snippet") or "")
    return f"{file_path}|{sym}|{line_key}|{lr_key}|{kind}|{snip_h}"


def _dedupe_key(c: dict) -> str:
    return retrieval_row_identity_key(c)


def deduplicate_candidates(candidates: list[dict]) -> list[dict]:
    """Return a deduplicated copy of candidates, keyed by composite row identity.

    First occurrence of each unique (file, symbol, kind, line hints, snippet) is kept.
    Original order is preserved so retriever rank signals remain intact for score fusion.
    """
    seen: set[str] = set()
    result: list[dict] = []
    for c in candidates:
        h = _dedupe_key(c)
        if h not in seen:
            seen.add(h)
            result.append(c)
    return result
