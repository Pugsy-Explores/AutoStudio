"""Reference and symbol-body tools. find_referencing_symbols uses symbol graph when available."""

import logging
import os
from pathlib import Path

from agent.tools.filesystem_adapter import read_file

logger = logging.getLogger(__name__)

MAX_REFERENCES_PER_SYMBOL = 10


def _find_project_root(file_path: str) -> Path | None:
    """Walk up from file_path to find project root (directory containing .symbol_graph)."""
    if not file_path:
        return None
    p = Path(file_path).resolve()
    if p.is_file():
        p = p.parent
    for _ in range(20):
        if (p / ".symbol_graph" / "index.sqlite").exists():
            return p
        parent = p.parent
        if parent == p:
            break
        p = parent
    return None


def find_referencing_symbols(
    symbol: str,
    file_path: str,
    project_root: str | None = None,
) -> dict[str, list[dict]]:
    """
    Find references to the given symbol via symbol graph.
    Returns structured dict: {callers, callees, imports, referenced_by}, each list capped at 10.
    Each item is {file, symbol, line?, name} (node dict from graph).
    Falls back to empty dict when no graph index exists.
    """
    if not symbol and not file_path:
        return {"callers": [], "callees": [], "imports": [], "referenced_by": []}

    root = Path(project_root) if project_root else _find_project_root(file_path)
    if not root:
        root = Path(os.getcwd())
    index_path = root / ".symbol_graph" / "index.sqlite"
    if not index_path.is_file():
        logger.debug("[find_referencing_symbols] no index at %s", index_path)
        return {"callers": [], "callees": [], "imports": [], "referenced_by": []}

    try:
        from repo_graph.graph_query import (
            get_callees,
            get_callers,
            get_imports,
            get_referenced_by,
        )
        from repo_graph.graph_storage import GraphStorage

        storage = GraphStorage(str(index_path))
        try:
            node = storage.get_symbol_by_name(symbol.strip()) if symbol else None
            if not node and file_path:
                matches = storage.get_symbols_like(Path(file_path).stem, limit=1)
                node = matches[0] if matches else None
            if not node:
                return {"callers": [], "callees": [], "imports": [], "referenced_by": []}

            symbol_id = node.get("id")
            if symbol_id is None:
                return {"callers": [], "callees": [], "imports": [], "referenced_by": []}

            def to_ref(n: dict) -> dict:
                sym = n.get("name", "")
                line = n.get("start_line")
                return {
                    "file": n.get("file", ""),
                    "symbol": sym,
                    "line": line,
                    "snippet": f"{sym} at line {line}" if line else sym or "",
                }

            callers = [to_ref(n) for n in get_callers(symbol_id, storage)][:MAX_REFERENCES_PER_SYMBOL]
            callees = [to_ref(n) for n in get_callees(symbol_id, storage)][:MAX_REFERENCES_PER_SYMBOL]
            imports = [to_ref(n) for n in get_imports(symbol_id, storage)][:MAX_REFERENCES_PER_SYMBOL]
            ref_by = [to_ref(n) for n in get_referenced_by(symbol_id, storage)][:MAX_REFERENCES_PER_SYMBOL]

            return {"callers": callers, "callees": callees, "imports": imports, "referenced_by": ref_by}
        finally:
            storage.close()
    except ImportError:
        logger.debug("[find_referencing_symbols] repo_graph not available")
        return {"callers": [], "callees": [], "imports": [], "referenced_by": []}


def read_symbol_body(
    symbol: str,
    file_path: str,
    max_chars: int = 2000,
    line: int | None = None,
    window_lines: int = 50,
) -> str:
    """
    Get the body of a symbol (e.g. function/class) in a file. Prefer Serena MCP if available.
    When line is set (e.g. from find_symbol), read only a window around that line to avoid
    loading the whole file. Otherwise returns first max_chars of the file.
    """
    if not file_path:
        return ""
    try:
        content = read_file(file_path)
        if not content:
            return ""
        lines = content.splitlines()
        if line is not None and line > 0 and lines:
            # Surgical: only the window around the symbol location (1-based index from search)
            zero_indexed = max(0, line - 1)
            start = max(0, zero_indexed - window_lines)
            end = min(len(lines), zero_indexed + window_lines + 1)
            window = "\n".join(lines[start:end])
            return window[:max_chars]
        return (content or "")[:max_chars]
    except Exception as e:
        logger.warning("[read_symbol_body] %s: %s", file_path, e)
        return ""
