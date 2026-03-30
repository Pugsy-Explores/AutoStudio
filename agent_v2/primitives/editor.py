"""Editor primitive wrapping filesystem and patch execution."""

import ast
from pathlib import Path

from agent.tools.filesystem_adapter import (
    read_file,
    read_file_head,
    read_file_line_range,
    read_file_line_window,
    write_file,
)
from editing.patch_executor import execute_patch


class Editor:
    """Editing primitive for file and patch operations."""

    def read(self, path: str) -> str:
        return read_file(path)

    def read_head(self, path: str, *, max_lines: int = 200, max_chars: int = 12000) -> str:
        return read_file_head(path, max_lines=max_lines, max_chars=max_chars)

    def read_line_window(
        self,
        path: str,
        *,
        center_line: int,
        window: int = 80,
        max_chars: int = 12000,
    ) -> tuple[str, int, int]:
        return read_file_line_window(
            path,
            center_line=center_line,
            window=window,
            max_chars=max_chars,
        )

    def read_line_range(
        self,
        path: str,
        *,
        start_line: int,
        end_line: int,
        max_chars: int = 12000,
    ) -> tuple[str, int, int]:
        return read_file_line_range(
            path,
            start_line=start_line,
            end_line=end_line,
            max_chars=max_chars,
        )

    def read_python_symbol_body(
        self,
        path: str,
        *,
        symbol: str,
        padding_lines: int = 5,
        max_chars: int = 12000,
    ) -> tuple[str, int, int] | None:
        """
        Deterministic symbol-body extraction for .py files using AST line spans.
        Returns (content, start_line, end_line) or None if not found/unsupported.
        """
        symbol = (symbol or "").strip()
        if not symbol:
            return None
        if not str(path).endswith(".py"):
            return None
        p = Path(path)
        try:
            text = read_file_head(str(p), max_lines=4000, max_chars=400000)
            tree = ast.parse(text)
        except Exception:
            return None

        target = None
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == symbol:
                target = node
                break
        if target is None:
            return None
        lineno = getattr(target, "lineno", None)
        end_lineno = getattr(target, "end_lineno", None)
        if not isinstance(lineno, int) or not isinstance(end_lineno, int):
            return None

        start = max(1, lineno - max(0, int(padding_lines or 0)))
        end = end_lineno + max(0, int(padding_lines or 0))
        return self.read_line_range(str(p), start_line=start, end_line=end, max_chars=max_chars)

    def write(self, path: str, content: str) -> dict:
        write_file(path, content)
        return {"success": True, "path": path}

    def apply_patch(self, patch, project_root: str | None = None) -> dict:
        return execute_patch(patch, project_root)
