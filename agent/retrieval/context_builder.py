"""Context builder: turn search results into files/snippets context. Symbol-aware."""

import logging

from config.retrieval_config import DEFAULT_MAX_CONTEXT_CHARS

logger = logging.getLogger(__name__)


def build_context(search_results) -> dict:
    """
    Build context dict from search results. Returns {"files": [], "snippets": []}.
    If search_results has "results" list, populate from it; else return empty.
    """
    out = {"files": [], "snippets": []}
    if not search_results or not isinstance(search_results, dict):
        return out
    results = search_results.get("results")
    if not isinstance(results, list):
        return out
    for r in results:
        if isinstance(r, dict):
            if r.get("file"):
                out["files"].append(r.get("file"))
            if "snippet" in r:
                out["snippets"].append(r.get("snippet", ""))
    return out


def build_context_from_symbols(
    symbol_results: list,
    reference_results: list,
    file_snippets: list,
    max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
) -> dict:
    """
    Build context from symbol search, reference search, and file snippets.
    Returns {"symbols": [], "references": [], "files": [], "snippets": []}.
    Deduplicates by (file, symbol or line), preserves file paths, limits total size.
    """
    symbols = []
    seen_symbols = set()
    for r in (symbol_results or []):
        if not isinstance(r, dict):
            continue
        key = (r.get("file") or "", r.get("symbol") or "")
        if key in seen_symbols:
            continue
        seen_symbols.add(key)
        symbols.append(r)

    references = []
    seen_refs = set()
    for r in (reference_results or []):
        if not isinstance(r, dict):
            continue
        key = (r.get("file") or "", r.get("symbol") or "", r.get("line"))
        if key in seen_refs:
            continue
        seen_refs.add(key)
        references.append(r)

    files = []
    snippets = []  # each item: {"file": str, "symbol": str, "snippet": str}
    seen_files = set()
    total_chars = 0
    for s in (file_snippets or []):
        if not isinstance(s, dict):
            continue
        path = s.get("file") or s.get("path") or ""
        if path in seen_files:
            continue
        seen_files.add(path)
        files.append(path)
        snip = (s.get("snippet") or s.get("content") or "")[:2000]
        symbol = s.get("symbol") or ""
        if snip and total_chars + len(snip) <= max_context_chars:
            snippets.append({"file": path, "symbol": symbol, "snippet": snip})
            total_chars += len(snip)
        else:
            snippets.append({"file": path, "symbol": symbol, "snippet": ""})

    logger.info("[context_builder] %d symbols, %d references, %d files, %d snippets", len(symbols), len(references), len(files), len(snippets))
    return {
        "symbols": symbols,
        "references": references,
        "files": files,
        "snippets": snippets,
    }
