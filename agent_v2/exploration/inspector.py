from __future__ import annotations

import logging

from agent_v2.schemas.exploration import InspectionSignals, LineRangeSignal, ReadPacket

_LOG = logging.getLogger(__name__)


class Inspector:
    """Extraction-only inspector that proposes useful line ranges and signals."""

    def inspect(self, packets: list[ReadPacket], *, max_ranges: int = 6) -> InspectionSignals:
        _LOG.debug("[Inspector.inspect]")
        if not packets:
            return InspectionSignals()
        ranges: list[LineRangeSignal] = []
        symbols: list[str] = []
        relationships: list[str] = []
        for packet in packets:
            if packet.symbol:
                symbols.append(packet.symbol)
            reason = "core logic" if packet.read_source == "symbol" else "dependency"
            ranges.append(
                LineRangeSignal(
                    start=max(1, packet.line_start),
                    end=max(packet.line_start, packet.line_end),
                    reason=reason,
                )
            )
        merged = self._merge_and_filter_ranges(ranges)
        if len(merged) > max_ranges:
            merged = merged[:max_ranges]
        unique_symbols = list(dict.fromkeys(s for s in symbols if s.strip()))
        if len(unique_symbols) > 1:
            relationships.append("multiple symbols co-occur in grouped snippets")
        return InspectionSignals(line_ranges=merged, symbols=unique_symbols, relationships=relationships)

    @staticmethod
    def _merge_and_filter_ranges(ranges: list[LineRangeSignal]) -> list[LineRangeSignal]:
        if not ranges:
            return []
        ordered = sorted(ranges, key=lambda r: (r.start, r.end))
        merged: list[LineRangeSignal] = []
        for r in ordered:
            if not merged:
                merged.append(r)
                continue
            last = merged[-1]
            if r.start <= last.end + 1:
                merged[-1] = LineRangeSignal(
                    start=last.start,
                    end=max(last.end, r.end),
                    reason=last.reason or r.reason or "core logic",
                )
            else:
                merged.append(r)
        # Guard against accidental full-file sweeps from noisy input.
        compact: list[LineRangeSignal] = []
        for r in merged:
            if (r.end - r.start + 1) > 400:
                compact.append(LineRangeSignal(start=r.start, end=r.start + 399, reason=r.reason))
            else:
                compact.append(r)
        return compact
