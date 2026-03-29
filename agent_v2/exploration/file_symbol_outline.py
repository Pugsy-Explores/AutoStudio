"""Deterministic Python symbol outlines for exploration selector (Tree-sitter, no LLM)."""

from __future__ import annotations

import logging
import re
from pathlib import Path

_LOG = logging.getLogger(__name__)


def load_python_file_outline(file_path: str) -> list[dict[str, str]]:
    """
    Return [{name, type}, ...] for top-level and nested function/class/method symbols.
    Empty if file missing, non-.py, or parse fails.
    """
    path = Path(file_path)
    if not path.suffix.lower() == ".py" or not path.is_file():
        return []

    try:
        from repo_index.parser import parse_file
        from repo_index.symbol_extractor import extract_symbols
    except ImportError as e:
        _LOG.debug("[file_symbol_outline] import skip: %s", e)
        return []

    tree = parse_file(str(path))
    if tree is None:
        return []
    try:
        source_bytes = path.read_bytes()
    except OSError:
        source_bytes = b""

    try:
        resolved = str(path.resolve())
    except OSError:
        resolved = str(path)

    raw = extract_symbols(tree, resolved, source_bytes)
    out: list[dict[str, str]] = []
    for row in raw:
        st = str(row.get("symbol_type") or "")
        if st not in ("function", "class", "method"):
            continue
        name = str(row.get("symbol_name") or "").strip()
        if name:
            out.append({"name": name, "type": st})
    return out


def rank_outline_for_selector_query(
    outline: list[dict[str, str]],
    query_text: str,
    top_k: int,
) -> list[dict[str, str]]:
    """Deterministic relevance: instruction + intent text vs symbol names."""
    if not outline or top_k <= 0:
        return []
    q = (query_text or "").lower()
    tokens = set(re.findall(r"[a-z][a-z0-9_]{2,}", q, flags=re.I))

    def score(entry: dict[str, str]) -> tuple[int, str]:
        name = entry.get("name") or ""
        nl = name.lower()
        s = 0
        if nl and nl in q:
            s += 12
        for t in tokens:
            if t in nl:
                s += 3
        for part in nl.replace(".", " ").split():
            if len(part) >= 3 and part in tokens:
                s += 4
        return (-s, nl)

    ranked = sorted(outline, key=score)
    return ranked[:top_k]
