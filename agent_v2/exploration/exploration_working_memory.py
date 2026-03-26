"""
Exploration-stage working memory — per-run, ephemeral, in-memory only.

Schema + behavior: Docs/architecture_freeze/EXPLORATION_WORKING_MEMORY_DESIGN.md
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from agent_v2.schemas.exploration import ExplorationCandidate

EvidenceTier = Literal[0, 1, 2]
# 0 = inspection/analyzer (grounded read), 1 = expansion summary row, 2 = discovery

_GENERIC_GAP_MARKERS: tuple[str, ...] = (
    "more context",
    "need more context",
    "insufficient context",
    "missing details",
    "unclear",
    "unknown",
    "more code",
)


def _norm_gap_desc(s: str) -> str:
    t = (s or "").strip().lower()
    return t[:500]


def _is_generic_gap(normalized_lower: str) -> bool:
    if len(normalized_lower) < 8:
        return True
    return any(m in normalized_lower for m in _GENERIC_GAP_MARKERS)


def file_symbol_key(file_path: str, symbol: str | None) -> str:
    sym = (symbol or "").strip()
    return f"{file_path}::{sym if sym else '__file__'}"


@dataclass
class ExplorationWorkingMemory:
    """
    Single source of truth for Schema 4 exploration output (evidence, gaps, relationships).
    """

    min_confidence: float = 0.35
    max_evidence: int = 6
    max_gaps: int = 6
    max_relationships: int = 48

    _evidence: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    _evidence_order: list[tuple[str, str]] = field(default_factory=list)
    _relationships: dict[tuple[str, str, str], dict[str, Any]] = field(default_factory=dict)
    _rel_order: list[tuple[str, str, str]] = field(default_factory=list)
    _gaps: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    _gap_order: list[tuple[str, str]] = field(default_factory=list)
    _seq: int = 0

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    @staticmethod
    def _better_summary_row(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
        """Prefer lower tier (0 best); tie-break higher confidence."""
        ta = int(a.get("tier", 99))
        tb = int(b.get("tier", 99))
        if ta != tb:
            return a if ta < tb else b
        ca = float(a.get("confidence") or 0.0)
        cb = float(b.get("confidence") or 0.0)
        return a if ca >= cb else b

    def add_evidence(
        self,
        symbol: str | None,
        file: str,
        line_range: tuple[int, int],
        summary: str,
        *,
        snippet: str | None = None,
        read_source: str | None = None,
        confidence: float,
        source: Literal["inspection", "analyzer", "discovery", "expansion"],
        tier: EvidenceTier,
        tool_name: str = "read_snippet",
    ) -> None:
        if float(confidence) < self.min_confidence:
            return
        sym_key = (symbol or "").strip() or "__file__"
        key = (file, sym_key)
        start, end = int(line_range[0]), int(line_range[1])
        if end < start:
            start, end = end, start
        start = max(1, start)
        end = max(start, end)

        new_row: dict[str, Any] = {
            "symbol": symbol,
            "file": file,
            "line_range": {"start": start, "end": end},
            "summary": (summary or "").strip()[:600] or "evidence recorded",
            "snippet": (snippet or "")[:600] if snippet else "",
            "read_source": read_source,
            "confidence": float(confidence),
            "source": source,
            "tier": int(tier),
            "tool_name": tool_name,
            "order": self._next_seq(),
        }

        if key not in self._evidence:
            self._evidence[key] = new_row
            self._evidence_order.append(key)
            return

        old = self._evidence[key]
        merged_start = min(int(old["line_range"]["start"]), start)
        merged_end = max(int(old["line_range"]["end"]), end)
        winner = self._better_summary_row(old, new_row)
        merged = {
            **winner,
            "line_range": {"start": merged_start, "end": merged_end},
            "summary": winner["summary"],
            "snippet": winner.get("snippet") or "",
            "read_source": winner.get("read_source"),
            "confidence": max(float(old.get("confidence") or 0.0), float(new_row.get("confidence") or 0.0)),
            "order": min(int(old.get("order", 10**9)), int(new_row.get("order", 10**9))),
        }
        self._evidence[key] = merged

    def ingest_discovery_candidates(
        self,
        candidates: list[ExplorationCandidate],
        *,
        limit: int,
    ) -> None:
        for c in (candidates or [])[:limit]:
            raw = float(getattr(c, "_discovery_max_score", 0.0) or 0.0)
            conf = max(self.min_confidence, min(1.0, raw))
            snip = (c.snippet or "").strip()
            summary = snip[:600] if snip else "discovery candidate"
            self.add_evidence(
                c.symbol,
                str(c.file_path),
                (1, 1),
                summary,
                snippet=None,
                read_source=None,
                confidence=conf,
                source="discovery",
                tier=2,
                tool_name="search",
            )

    def add_relationships_from_expand(
        self,
        anchor_file: str,
        anchor_symbol: str | None,
        expand_data: dict[str, Any],
        *,
        confidence: float = 0.85,
    ) -> None:
        if not isinstance(expand_data, dict):
            return
        from_key = file_symbol_key(anchor_file, anchor_symbol)
        buckets: tuple[tuple[str, str], ...] = (
            ("callers", "callers"),
            ("callees", "callees"),
            ("related", "related"),
        )
        for bucket, rel_type in buckets:
            raw = expand_data.get(bucket) or []
            if not isinstance(raw, list):
                continue
            for row in raw:
                if not isinstance(row, dict):
                    continue
                fp = str(row.get("file_path") or row.get("file") or "").strip()
                if not fp:
                    continue
                sym_raw = row.get("symbol")
                sym = str(sym_raw).strip() if sym_raw else None
                to_key = file_symbol_key(fp, sym)
                self.add_relationship(
                    from_key,
                    to_key,
                    rel_type,
                    confidence=confidence,
                    source="expansion",
                )

    def add_relationship(
        self,
        from_key: str,
        to_key: str,
        rel_type: Literal["callers", "callees", "related"],
        *,
        confidence: float,
        source: Literal["expansion"],
    ) -> None:
        if from_key == to_key:
            return
        k = (from_key, to_key, rel_type)
        if k in self._relationships:
            return
        if len(self._relationships) >= self.max_relationships:
            return
        self._relationships[k] = {
            "from": from_key,
            "to": to_key,
            "type": rel_type,
            "confidence": float(confidence),
            "source": source,
            "order": self._next_seq(),
        }
        self._rel_order.append(k)

    def add_gap(
        self,
        gap_type: str,
        description: str,
        *,
        confidence: float,
        source: Literal["analyzer"],
    ) -> bool:
        desc = (description or "").strip()
        if not desc:
            return False
        low = desc.lower()
        if _is_generic_gap(low):
            return False
        gk = (gap_type.strip() or "none", _norm_gap_desc(desc))
        if gk in self._gaps:
            return False
        if len(self._gaps) >= self.max_gaps:
            return False
        self._gaps[gk] = {
            "type": gap_type.strip() or "none",
            "description": desc[:500],
            "confidence": float(confidence),
            "source": source,
            "order": self._next_seq(),
        }
        self._gap_order.append(gk)
        return True

    def add_expansion_evidence_row(
        self,
        anchor_file: str,
        anchor_symbol: str | None,
        summary: str,
        *,
        success: bool,
        tool_name: str,
    ) -> None:
        """One row for expansion phase (search item) when summary should surface."""
        conf = 0.8 if success else max(self.min_confidence, 0.35)
        self.add_evidence(
            anchor_symbol,
            anchor_file,
            (1, 1),
            (summary or "").strip() or "expansion completed",
            snippet=None,
            read_source=None,
            confidence=conf,
            source="expansion",
            tier=1,
            tool_name=tool_name,
        )

    def all_evidence_rows(self) -> list[dict[str, Any]]:
        """All stored evidence rows in enqueue order (uncapped; for integrity checks)."""
        out: list[dict[str, Any]] = []
        for fk in self._evidence_order:
            row = self._evidence.get(fk)
            if row:
                out.append(dict(row))
        return out

    def get_summary(self) -> dict[str, Any]:
        ev_keys = list(self._evidence_order)
        ev_list: list[dict[str, Any]] = []
        for fk in ev_keys:
            row = self._evidence.get(fk)
            if row:
                ev_list.append(dict(row))
        ev_list.sort(key=lambda r: (int(r.get("tier", 99)), int(r.get("order", 0))))

        rel_list = [dict(self._relationships[k]) for k in self._rel_order if k in self._relationships]
        gap_list = [dict(self._gaps[k]) for k in self._gap_order if k in self._gaps]
        return {
            "evidence": ev_list[: self.max_evidence],
            "relationships": rel_list,
            "gaps": gap_list,
        }
