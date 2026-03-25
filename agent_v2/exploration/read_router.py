from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent_v2.primitives import get_editor


@dataclass(frozen=True)
class ReadRequest:
    """
    Internal bounded-read request.

    Not exposed to LLM; system constructs this from ExplorationTarget.
    """

    path: str
    symbol: str | None = None
    line: int | None = None
    window: int = 80


def read(request: ReadRequest, *, state) -> dict:
    """
    System-owned bounded read router.

    Returns a normalized dict payload for tool output:
      { file_path, start_line, end_line, content, mode }
    """
    path = str(request.path or "").strip()
    if not path:
        return {"file_path": "", "start_line": 0, "end_line": 0, "content": "", "mode": "empty"}

    project_root = (getattr(state, "context", None) or {}).get("project_root")
    if not project_root:
        project_root = str(Path.cwd())
    full_path = Path(path) if Path(path).is_absolute() else Path(project_root) / path
    resolved = str(full_path.resolve())

    editor = get_editor(state)

    symbol = (request.symbol or "").strip() or None
    line = int(request.line) if request.line is not None else None
    window = int(request.window or 80)

    if symbol:
        py = editor.read_python_symbol_body(
            resolved,
            symbol=symbol,
            padding_lines=5,
            max_chars=12000,
        )
        if py is not None:
            content, start_line, end_line = py
            return {
                "file_path": resolved,
                "start_line": start_line,
                "end_line": end_line,
                "content": content,
                "mode": "symbol_body",
                "symbol": symbol,
            }

    if line is not None and line > 0:
        content, start_line, end_line = editor.read_line_window(
            resolved,
            center_line=line,
            window=window,
            max_chars=12000,
        )
        return {
            "file_path": resolved,
            "start_line": start_line,
            "end_line": end_line,
            "content": content,
            "mode": "line_window",
        }

    content = editor.read_head(resolved, max_lines=200, max_chars=12000)
    return {
        "file_path": resolved,
        "start_line": 1,
        "end_line": max(1, len(content.splitlines())),
        "content": content,
        "mode": "file_head",
    }

