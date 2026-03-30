from __future__ import annotations

import logging

from agent_v2.schemas.exploration import ContextBlock, ReadPacket

_LOG = logging.getLogger(__name__)


class ContextBlockBuilder:
    """Canonical context block formatter for analyzer input stability."""

    def from_packets(
        self,
        packets: list[ReadPacket],
        *,
        max_total_lines: int = 300,
    ) -> list[ContextBlock]:
        _LOG.debug("[ContextBlockBuilder.from_packets]")
        blocks: list[ContextBlock] = []
        used_lines = 0
        for packet in self._ordered_packets(packets):
            if used_lines >= max_total_lines:
                break
            line_budget = max_total_lines - used_lines
            start = packet.line_start
            end = min(packet.line_end, start + line_budget - 1)
            if end < start:
                continue
            content = self._truncate_lines(packet.content, line_budget)
            blocks.append(
                ContextBlock(
                    file_path=packet.file_path,
                    start=start,
                    end=end,
                    content=content,
                    origin_reason="direct_read",
                    symbol=packet.symbol,
                    relationship_refs=[],
                )
            )
            used_lines += max(1, end - start + 1)
        return blocks

    def finalize(
        self,
        blocks: list[ContextBlock],
        *,
        max_total_lines: int = 300,
    ) -> list[ContextBlock]:
        _LOG.debug("[ContextBlockBuilder.finalize]")
        ordered = sorted(blocks, key=lambda b: (b.file_path, b.start, b.end))
        out: list[ContextBlock] = []
        used_lines = 0
        for block in ordered:
            if used_lines >= max_total_lines:
                break
            remaining = max_total_lines - used_lines
            line_count = max(1, block.end - block.start + 1)
            if line_count > remaining:
                new_end = block.start + remaining - 1
                content = self._truncate_lines(block.content, remaining)
                out.append(block.model_copy(update={"end": new_end, "content": content}))
                used_lines = max_total_lines
            else:
                out.append(block)
                used_lines += line_count
        return out

    @staticmethod
    def _ordered_packets(packets: list[ReadPacket]) -> list[ReadPacket]:
        return sorted(
            packets,
            key=lambda p: (p.file_path, 0 if (p.symbol or "").strip() else 1, p.line_start, p.line_end),
        )

    @staticmethod
    def _truncate_lines(content: str, max_lines: int) -> str:
        if max_lines <= 0 or not content:
            return ""
        lines = content.splitlines()
        return "\n".join(lines[:max_lines])
