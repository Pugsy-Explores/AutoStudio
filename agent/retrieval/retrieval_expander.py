"""Retrieval expansion: turn search results into follow-up actions (read_file / read_symbol_body). Capped for large repos."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from config.retrieval_config import (
    ENABLE_KIND_AWARE_EXPANSION,
    MAX_FILE_HEADER_LINES,
    MAX_LINES_PER_EXPANDED_UNIT,
    MAX_SEARCH_RESULTS,
    MAX_SYMBOL_EXPANSION,
)

logger = logging.getLogger(__name__)

# Re-export for backward compatibility
__all__ = [
    "expand_search_results",
    "normalize_file_path",
    "MAX_SEARCH_RESULTS",
    "MAX_SYMBOL_EXPANSION",
    "expansion_action_for_result",
    "expand_region_bounded",
    "expand_file_header",
    "extract_enclosing_class_name",
    "count_expanded_lines",
    "_extract_file_pieces",
]

# Strip JSON/formatting artifacts that can appear when search results are mis-parsed
_PATH_ARTIFACT_PATTERN = re.compile(r'^[\s{"\']+|[\s}"\']+$')

_TOP_LEVEL_DEF = re.compile(r"^(\s*)(def |class )")
_IMPORT_LINE = re.compile(r"^(\s*)(import |from )")
_ENTRYPOINT_LINE = re.compile(
    r"if __name__|^\s*def (main|run|cli|app)\s*\(",
    re.MULTILINE,
)


def normalize_file_path(path: str) -> str:
    """
    Normalize file path by stripping JSON/formatting artifacts.
    Handles malformed paths like '{"tests/test_agent_e2e.py' or '"path/to/file.py"'.
    """
    if not path or not isinstance(path, str):
        return ""
    s = path.strip()
    s = _PATH_ARTIFACT_PATTERN.sub("", s)
    return s.strip() if s else ""


def parse_line_range(lr: Any) -> tuple[int, int] | None:
    """Normalize line_range to 1-based (start, end) inclusive."""
    if lr is None:
        return None
    if isinstance(lr, (list, tuple)) and len(lr) >= 2:
        try:
            a, b = int(lr[0]), int(lr[1])
            return (min(a, b), max(a, b))
        except (TypeError, ValueError):
            return None
    if isinstance(lr, (int, float)):
        n = int(lr)
        return (n, n) if n > 0 else None
    return None


def count_expanded_lines(text: str) -> int:
    """Line count for budget tests."""
    if not text:
        return 0
    return len(text.splitlines())


def extract_enclosing_class_name(lines: list[str], line_1based: int) -> str:
    """
    Walk backward from line_1based (1-based) to the nearest enclosing `class Name`.
    Returns "" if none.
    """
    if line_1based < 1 or line_1based > len(lines):
        return ""
    idx = line_1based - 1
    class_re = re.compile(r"^(\s*)class\s+([A-Za-z_][A-Za-z0-9_]*)\s*[\(:]")
    for j in range(idx, -1, -1):
        m = class_re.match(lines[j])
        if m:
            return m.group(2)
    return ""


def expand_region_bounded(
    path: str,
    line_range: Any,
    *,
    max_lines: int | None = None,
) -> tuple[str, bool | None]:
    """
    Extract bounded code around line_range; extend backward to enclosing def/class when cheap.
    Returns (snippet_text, implementation_body_present_or_omit).
    Second value: True -> set implementation_body_present on the row; None -> omit the field.
    Do not return False (that would poison downstream gates).
    """
    max_lines = max_lines or MAX_LINES_PER_EXPANDED_UNIT
    span = parse_line_range(line_range)
    if not path or not span:
        return "", None
    try:
        p = Path(path)
        raw = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "", None
    lines = raw.splitlines()
    if not lines:
        return "", None
    start_line, end_line = span
    start_line = max(1, min(start_line, len(lines)))
    end_line = max(start_line, min(end_line, len(lines)))
    pad = 18
    lo = max(1, start_line - pad)
    # Walk back to enclosing def/class
    anchor = start_line
    for j in range(start_line - 1, -1, -1):
        line = lines[j]
        stripped = line.lstrip()
        if stripped.startswith("def ") or stripped.startswith("class "):
            anchor = j + 1
            break
        if j < start_line - 80:
            break
    lo = max(1, min(lo, anchor))
    hi = min(len(lines), max(end_line + pad, anchor + max_lines - 1))
    chunk = lines[lo - 1 : hi]
    if len(chunk) > max_lines:
        chunk = chunk[:max_lines]
    text = "\n".join(chunk).strip()
    if not text:
        return "", None
    # Heuristic: full enclosing body only when we anchored at def/class and have enough lines
    first = chunk[0].lstrip() if chunk else ""
    impl_body: bool | None = None
    if first.startswith("def ") or first.startswith("class "):
        if len(chunk) >= 5:
            impl_body = True
    return text, impl_body


def _extract_module_docstring(lines: list[str]) -> tuple[str, int]:
    """Return (docstring block or '', index after docstring)."""
    if not lines:
        return "", 0
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines):
        return "", i
    first = lines[i].lstrip()
    if not (first.startswith('"""') or first.startswith("'''")):
        return "", i
    quote = '"""' if first.startswith('"""') else "'''"
    block: list[str] = [lines[i]]
    if lines[i].count(quote) >= 2 and len(lines[i].strip()) > 6:
        return "\n".join(block), i + 1
    j = i + 1
    while j < len(lines):
        block.append(lines[j])
        if quote in lines[j]:
            return "\n".join(block), j + 1
        j += 1
    return "\n".join(block), j


def _extract_file_pieces(path: str) -> dict[str, Any]:
    """
    Structured file header pieces (internal; join in expand_file_header).
    Keys: module_docstring, imports, top_level_defs, entrypoint_lines.
    """
    out: dict[str, Any] = {
        "module_docstring": "",
        "imports": [],
        "top_level_defs": [],
        "entrypoint_lines": [],
    }
    try:
        p = Path(path)
        raw = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    lines = raw.splitlines()
    if not lines:
        return out
    doc, start_after = _extract_module_docstring(lines)
    out["module_docstring"] = doc
    i = start_after
    # Imports and top-level defs until we hit a non-import, non-def at col0 after imports block
    collecting_imports = True
    while i < len(lines) and len(out["imports"]) + len(out["top_level_defs"]) < MAX_FILE_HEADER_LINES:
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        if collecting_imports and _IMPORT_LINE.match(line):
            out["imports"].append(line.rstrip())
            i += 1
            continue
        collecting_imports = False
        m = _TOP_LEVEL_DEF.match(line)
        if m and not m.group(1):
            out["top_level_defs"].append(line.rstrip())
            i += 1
            continue
        i += 1
        if len(out["top_level_defs"]) >= 40:
            break
    # Second pass: entrypoint markers may appear after class bodies
    for line in lines:
        if _ENTRYPOINT_LINE.search(line):
            s = line.rstrip()
            if s not in out["entrypoint_lines"]:
                out["entrypoint_lines"].append(s)
        if len(out["entrypoint_lines"]) >= 20:
            break
    return out


def expand_file_header(path: str, *, max_lines: int | None = None) -> str:
    """Join structured header pieces into one bounded snippet string."""
    max_lines = max_lines or MAX_FILE_HEADER_LINES
    pieces = _extract_file_pieces(path)
    parts: list[str] = []
    if pieces.get("module_docstring"):
        parts.append(pieces["module_docstring"])
    for im in pieces.get("imports") or []:
        parts.append(im)
    for sig in pieces.get("top_level_defs") or []:
        parts.append(sig)
    for ep in pieces.get("entrypoint_lines") or []:
        parts.append(ep)
    text = "\n".join(parts).strip()
    ls = text.splitlines()
    if len(ls) > max_lines:
        text = "\n".join(ls[:max_lines]) + "\n..."
    return text


def expansion_action_for_result(r: dict, *, kind_aware: bool) -> str:
    """
    Decide expansion action for one search hit.
    When kind_aware is False, preserve legacy behavior: symbol present -> read_symbol_body else read_file.
    """
    if not kind_aware:
        symbol = (r.get("symbol") or "").strip()
        return "read_symbol_body" if symbol else "read_file"
    kind = (r.get("candidate_kind") or "").strip().lower()
    if kind == "file":
        return "read_file_header"
    if kind == "region":
        if parse_line_range(r.get("line_range")) is not None:
            return "read_region_bounded"
        return "read_file"
    if kind == "symbol":
        return "read_symbol_body"
    if kind in ("reference", "localization"):
        return "read_symbol_body" if (r.get("symbol") or "").strip() else "read_file"
    symbol = (r.get("symbol") or "").strip()
    return "read_symbol_body" if symbol else "read_file"


def expand_search_results(results: list[dict]) -> list[dict]:
    """
    Expand search results into a list of actions (read_file or read_symbol_body).
    Input: list of items with at least "file", optionally "symbol".
    Output: list of {"file", "symbol", "action"} capped at MAX_SYMBOL_EXPANSION.
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
        kind_aware = ENABLE_KIND_AWARE_EXPANSION
        action = expansion_action_for_result(r, kind_aware=kind_aware)
        line = r.get("line") if isinstance(r.get("line"), (int, float)) else None
        if line is not None:
            line = int(line)
        entry: dict = {"file": file_path, "symbol": symbol, "action": action, "line": line}
        lr = r.get("line_range")
        if lr is not None:
            entry["line_range"] = lr
        ck = r.get("candidate_kind")
        if ck:
            entry["candidate_kind"] = ck
        expanded.append(entry)
        logger.info("[retrieval_expand] %s %s", action, file_path)
    return expanded
