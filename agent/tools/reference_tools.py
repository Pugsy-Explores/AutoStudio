"""Reference and symbol-body tools. find_referencing_symbols is a stub; wire to Serena MCP when available."""

import logging

from agent.tools.filesystem_adapter import read_file

logger = logging.getLogger(__name__)


def find_referencing_symbols(symbol: str, file_path: str) -> list[dict]:
    """
    Find references to the given symbol (e.g. usages). Stub returns [].
    Connect to Serena MCP when a reference-lookup tool is available.
    Returns list of {"file", "symbol", "line", "snippet"}.
    """
    if not symbol and not file_path:
        return []
    logger.debug("[find_referencing_symbols] symbol=%r file=%r (stub)", symbol, file_path)
    return []


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
