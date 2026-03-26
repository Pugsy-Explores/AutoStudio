from __future__ import annotations

from agent_v2.schemas.exploration import ContextBlock, InspectionSignals, ReadPacket


class Fetcher:
    """Resolve inspector ranges into bounded context blocks."""

    def fetch(
        self,
        packets: list[ReadPacket],
        signals: InspectionSignals,
        *,
        top_k_ranges: int = 6,
        max_total_lines: int = 300,
    ) -> list[ContextBlock]:
        if not packets:
            return []
        base = packets[0]
        ranges = list(signals.line_ranges)[: max(1, top_k_ranges)]
        blocks: list[ContextBlock] = []
        used_lines = 0
        for r in ranges:
            if used_lines >= max_total_lines:
                break
            clipped_start = max(base.line_start, r.start)
            clipped_end = min(base.line_end, r.end)
            if clipped_end < clipped_start:
                continue
            segment = self._extract_segment(base.content, base.line_start, clipped_start, clipped_end)
            line_count = max(1, clipped_end - clipped_start + 1)
            if used_lines + line_count > max_total_lines:
                remaining = max_total_lines - used_lines
                clipped_end = clipped_start + max(0, remaining - 1)
                segment = self._extract_segment(base.content, base.line_start, clipped_start, clipped_end)
                line_count = max(0, clipped_end - clipped_start + 1)
            if line_count <= 0:
                continue
            blocks.append(
                ContextBlock(
                    file_path=base.file_path,
                    start=clipped_start,
                    end=clipped_end,
                    content=segment,
                    origin_reason=r.reason,
                    symbol=base.symbol,
                    relationship_refs=list(signals.relationships),
                )
            )
            used_lines += line_count
        return self._dedupe_blocks(blocks)

    @staticmethod
    def _extract_segment(content: str, base_start: int, start: int, end: int) -> str:
        if not content:
            return ""
        lines = content.splitlines()
        a = max(0, start - base_start)
        b = max(a, end - base_start + 1)
        return "\n".join(lines[a:b])

    @staticmethod
    def _dedupe_blocks(blocks: list[ContextBlock]) -> list[ContextBlock]:
        out: list[ContextBlock] = []
        seen: set[tuple[str, int, int]] = set()
        for block in blocks:
            key = (block.file_path, block.start, block.end)
            if key in seen:
                continue
            seen.add(key)
            out.append(block)
        return out
