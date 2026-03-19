"""Retrieval expansion: turn search results into follow-up actions (read_file / read_symbol_body). Capped for large repos."""

import logging
import re
from pathlib import Path

from config.retrieval_config import MAX_SEARCH_RESULTS, MAX_SYMBOL_EXPANSION

logger = logging.getLogger(__name__)

# Re-export for backward compatibility
__all__ = ["expand_search_results", "normalize_file_path", "MAX_SEARCH_RESULTS", "MAX_SYMBOL_EXPANSION"]

# Strip JSON/formatting artifacts that can appear when search results are mis-parsed
# (e.g. Serena text fallback, cached results with embedded JSON)
_PATH_ARTIFACT_PATTERN = re.compile(r'^[\s{"\']+|[\s}"\']+$')


def normalize_file_path(path: str) -> str:
    """
    Normalize file path by stripping JSON/formatting artifacts.
    Handles malformed paths like '{"tests/test_agent_e2e.py' or '"path/to/file.py"'.
    """
    if not path or not isinstance(path, str):
        return ""
    s = path.strip()
    # Strip common JSON/quote artifacts
    s = _PATH_ARTIFACT_PATTERN.sub("", s)
    return s.strip() if s else ""


def expand_search_results(results: list[dict]) -> list[dict]:
    """
    Expand search results into a list of actions (read_file or read_symbol_body).
    Input: list of items with at least "file", optionally "symbol".
    Output: list of {"file", "symbol", "action"} capped at MAX_SYMBOL_EXPANSION.
    When symbol is present, action is "read_symbol_body"; else "read_file".
    """
    if not results or not isinstance(results, list):
        return []
    expanded = []
    for r in results[:MAX_SYMBOL_EXPANSION]:
        if not isinstance(r, dict):
            continue
        raw_path = r.get("file") or r.get("path") or ""
        file_path = normalize_file_path(raw_path)
        if not file_path:
            continue
        try:
            p = Path(file_path)
            if p.exists() and p.is_dir():
                logger.debug("[retrieval_expand] skip directory: %s", file_path)
                continue
        except OSError:
            continue
        symbol = r.get("symbol") or ""
        action = "read_symbol_body" if symbol else "read_file"
        line = r.get("line") if isinstance(r.get("line"), (int, float)) else None
        if line is not None:
            line = int(line)
        entry = {"file": file_path, "symbol": symbol, "action": action, "line": line}
        expanded.append(entry)
        logger.info("[retrieval_expand] %s %s", action, file_path)
    return expanded
