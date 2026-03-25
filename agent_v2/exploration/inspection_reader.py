from __future__ import annotations

from agent_v2.schemas.execution import ExecutionResult
from agent_v2.schemas.exploration import ExplorationCandidate


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
        return snippet[:8000], result

    @staticmethod
    def _extract_snippet(data: dict) -> str:
        if not isinstance(data, dict):
            return ""
        # read_snippet returns payload under "content"; open_file historically returned string or file_content.
        content = data.get("content") or data.get("file_content") or data.get("output") or ""
        if isinstance(content, str):
            return content
        return ""
