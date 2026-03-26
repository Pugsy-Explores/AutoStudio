from __future__ import annotations

from agent_v2.config import EXPLORATION_SNIPPET_MAX_CHARS
from agent_v2.schemas.execution import ExecutionResult
from agent_v2.schemas.exploration import ExplorationCandidate, ReadPacket


class InspectionReader:
    """Read a bounded snippet for a selected candidate."""

    def __init__(self, dispatcher):
        self._dispatcher = dispatcher

    def inspect(
        self,
        candidate: ExplorationCandidate,
        *,
        symbol: str | None = None,
        line: int | None = None,
        window: int = 80,
        state,
    ) -> tuple[str, ExecutionResult]:
        step_count = int(getattr(state, "steps_taken", 0))
        step = {
            "id": f"inspect_{step_count + 1}",
            "action": "READ",
            "_react_action_raw": "read_snippet",
            "_react_args": {
                "path": candidate.file_path,
                "symbol": symbol or candidate.symbol,
                "line": line,
                "window": window,
            },
            "path": candidate.file_path,
            "description": candidate.file_path,
        }
        result = self._dispatcher.execute(step, state)
        data = result.output.data if result.output else {}
        snippet = self._extract_snippet(data)
        return snippet[:EXPLORATION_SNIPPET_MAX_CHARS], result

    def inspect_packet(
        self,
        candidate: ExplorationCandidate,
        *,
        symbol: str | None = None,
        line: int | None = None,
        window: int = 80,
        state,
    ) -> tuple[ReadPacket, ExecutionResult]:
        snippet, result = self.inspect(
            candidate,
            symbol=symbol,
            line=line,
            window=window,
            state=state,
        )
        data = result.output.data if result.output else {}
        read_source = self._read_source(data)
        start_line = int(data.get("start_line") or 1) if isinstance(data, dict) else 1
        end_line = int(data.get("end_line") or max(1, len(snippet.splitlines()))) if isinstance(data, dict) else max(1, len(snippet.splitlines()))
        packet = ReadPacket(
            file_path=str(data.get("file_path") or candidate.file_path) if isinstance(data, dict) else candidate.file_path,
            symbol=symbol or candidate.symbol,
            read_source=read_source,
            content=snippet,
            line_start=start_line,
            line_end=end_line,
            char_count=len(snippet),
            line_count=max(1, len(snippet.splitlines())) if snippet else 0,
        )
        return packet, result

    @staticmethod
    def _extract_snippet(data: dict) -> str:
        if not isinstance(data, dict):
            return ""
        # read_snippet returns payload under "content"; open_file historically returned string or file_content.
        content = data.get("content") or data.get("file_content") or data.get("output") or ""
        if isinstance(content, str):
            return content
        return ""

    @staticmethod
    def _read_source(data: dict) -> str | None:
        if not isinstance(data, dict):
            return None
        mode = str(data.get("mode") or "")
        if mode == "symbol_body":
            return "symbol"
        if mode == "line_window":
            return "line"
        if mode == "file_head":
            return "head"
        return None
