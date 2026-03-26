from __future__ import annotations

from collections import defaultdict

from agent_v2.schemas.exploration import ReadPacket


class SliceGrouper:
    """Group and prioritize read packets before inspection."""

    def group(self, packets: list[ReadPacket]) -> list[list[ReadPacket]]:
        if not packets:
            return []
        buckets: dict[tuple[str, str, str], list[ReadPacket]] = defaultdict(list)
        for packet in packets:
            key = (
                packet.file_path,
                (packet.symbol or "").strip(),
                (packet.call_chain_id or "").strip(),
            )
            buckets[key].append(packet)
        groups = list(buckets.values())
        for group in groups:
            group.sort(key=lambda p: (p.line_start, p.line_end))
        groups.sort(key=self._group_priority)
        return groups

    @staticmethod
    def _group_priority(group: list[ReadPacket]) -> tuple[int, int, int, int]:
        first = group[0]
        same_file_score = 0 if first.file_path else 1
        same_symbol_score = 0 if (first.symbol or "").strip() else 1
        call_chain_score = 0 if (first.call_chain_id or "").strip() else 1
        return (same_file_score, same_symbol_score, call_chain_score, len(group))
