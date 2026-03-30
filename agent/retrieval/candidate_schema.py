"""Retrieval contract for retrieval_pipeline_v2.

Strict schema — no heuristics, no implicit ranking.
All ranking authority lives in RRF (Option A) or optionally a single
cross-encoder reranker (Option B, not yet wired).

Candidate.dedup_key() is the canonical identity for prune_deterministic.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Source(str, Enum):
    GRAPH = "graph"
    BM25 = "bm25"
    VECTOR = "vector"
    SERENA = "serena"


@dataclass
class Candidate:
    """Single retrieval candidate. score fields are never blended heuristics.

    retrieval_score: source-native only (BM25 raw, vector distance if surfaced).
                     May be None when the underlying API doesn't expose it.
    rerank_score:    Set only if cross-encoder reranker ran (Option B). Always
                     None in default (Option A / RRF-only) pipeline.
    metadata:        Lossless passthrough — raw_score, rank_in_source,
                     source_specific dict.
    """

    path: str
    snippet: str
    symbol: str | None = None
    line: int | None = None
    source: Source = Source.GRAPH
    retrieval_score: float | None = None
    rerank_score: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def dedup_key(self) -> str:
        """Deterministic dedup key: (path_norm, symbol_norm, snippet_hash).

        Rules from migration plan §Issue 3:
          - path and symbol lowercased and stripped.
          - snippet whitespace-normalized before hashing.
          - first occurrence in RRF order wins.
        """
        path_norm = (self.path or "").strip().lower()
        sym_norm = (self.symbol or "").strip().lower()
        snip_norm = " ".join((self.snippet or "").split())
        snip_hash = hashlib.sha256(
            snip_norm.encode("utf-8", errors="replace")
        ).hexdigest()[:16]
        return f"{path_norm}|{sym_norm}|{snip_hash}"

    def to_legacy_dict(self) -> dict:
        """Convert to {file, symbol, line, snippet} for existing pipeline consumers."""
        return {
            "file": self.path,
            "symbol": self.symbol or "",
            "line": self.line or 0,
            "snippet": self.snippet,
        }


@dataclass
class RetrievalInput:
    """v2 pipeline input. All fields explicit — no hidden globals."""

    query: str
    project_root: str | None = None
    # Additional indexed repo roots (each with its own .symbol_graph). Merged in graph/bm25/vector.
    extra_project_roots: tuple[str, ...] | None = None
    top_k_per_source: int = 15
    rrf_top_n: int = 50
    rrf_k: int = 60
    max_snippets: int = 20
    max_chars: int = 20_000


@dataclass
class RetrievalOutput:
    """v2 pipeline output with full trace."""

    candidates: list[Candidate]
    query: str
    warnings: list[str] = field(default_factory=list)
    stages: dict[str, Any] = field(default_factory=dict)
    # stages keys: pre_rrf, post_rrf, post_validate, post_prune
