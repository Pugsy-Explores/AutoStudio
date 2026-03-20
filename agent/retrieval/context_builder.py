"""Context builder: turn search results into files/snippets context. Symbol-aware."""

import logging
from pathlib import Path

from config.context_limits import MAX_CONTEXT_FILES, MAX_CONTEXT_SNIPPETS, MAX_CONTEXT_TOKENS
from config.repo_graph_config import INDEX_SQLITE, SYMBOL_GRAPH_DIR
from config.retrieval_config import DEFAULT_MAX_CONTEXT_CHARS

logger = logging.getLogger(__name__)

# Approximate chars per token
_CHARS_PER_TOKEN = 4


def build_call_chain_context(symbol: str, project_root: str) -> dict:
    """
    Build execution path context for a symbol.
    Returns {symbol, call_chain, dependencies, references}.
    call_chain: formatted strings like "retry_request()\n  calls http_client.send()\n  calls timeout_handler.wait()"
    dependencies: list from get_callees
    references: list from get_callers
    Returns empty dict when graph index absent or symbol not found.
    """
    root = Path(project_root).resolve()
    index_path = root / SYMBOL_GRAPH_DIR / INDEX_SQLITE
    if not index_path.is_file():
        return {"symbol": symbol, "call_chain": [], "dependencies": [], "references": []}

    try:
        from agent.retrieval.localization.execution_path_analyzer import build_execution_paths
        from repo_graph.graph_query import get_callees, get_callers
        from repo_graph.graph_storage import GraphStorage

        storage = GraphStorage(str(index_path))
        try:
            node = storage.get_symbol_by_name(symbol.strip()) if symbol else None
            if not node:
                return {"symbol": symbol, "call_chain": [], "dependencies": [], "references": []}
            symbol_id = node.get("id")
            if symbol_id is None:
                return {"symbol": symbol, "call_chain": [], "dependencies": [], "references": []}

            paths = build_execution_paths(symbol, str(root))
            call_chain: list[str] = []
            for p in paths:
                path_items = p.get("path") or []
                if len(path_items) < 2:
                    continue
                lines = [f"{path_items[0].get('name', '')}()"]
                for i in range(1, len(path_items)):
                    lines.append(f"  calls {path_items[i].get('name', '')}()")
                call_chain.append("\n".join(lines))

            callees = get_callees(symbol_id, storage)
            callers = get_callers(symbol_id, storage)
            dependencies = [{"name": n.get("name", ""), "file": n.get("file", ""), "line": n.get("start_line")} for n in callees]
            references = [{"name": n.get("name", ""), "file": n.get("file", ""), "line": n.get("start_line")} for n in callers]

            return {
                "symbol": symbol,
                "call_chain": call_chain,
                "dependencies": dependencies,
                "references": references,
            }
        finally:
            storage.close()
    except (ImportError, RecursionError):
        logger.debug("[context_builder] build_call_chain_context: repo_graph not available")
        return {"symbol": symbol, "call_chain": [], "dependencies": [], "references": []}


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
    project_root: str | None = None,
) -> dict:
    """
    Build context from symbol search, reference search, and file snippets.
    Returns {"symbols": [], "references": [], "files": [], "snippets": [], "call_chain": {}}.
    Deduplicates by (file, symbol or line), preserves file paths, limits total size.
    When project_root is set and symbols exist, attaches call_chain from build_call_chain_context.
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

    call_chain_ctx: dict = {}
    if project_root and symbols:
        first_symbol = (symbols[0].get("symbol") or "").strip()
        if first_symbol:
            call_chain_ctx = build_call_chain_context(first_symbol, project_root)

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
    max_chars_from_tokens = MAX_CONTEXT_TOKENS * _CHARS_PER_TOKEN
    effective_max_chars = min(max_context_chars, max_chars_from_tokens)
    for s in (file_snippets or [])[:MAX_CONTEXT_SNIPPETS * 2]:  # allow some overflow for pruning
        if not isinstance(s, dict):
            continue
        if len(files) >= MAX_CONTEXT_FILES or len(snippets) >= MAX_CONTEXT_SNIPPETS:
            break
        path = s.get("file") or s.get("path") or ""
        if path in seen_files:
            continue
        seen_files.add(path)
        files.append(path)
        snip = (s.get("snippet") or s.get("content") or "")[:2000]
        symbol = s.get("symbol") or ""
        if snip and total_chars + len(snip) <= effective_max_chars:
            snippets.append({"file": path, "symbol": symbol, "snippet": snip})
            total_chars += len(snip)
        else:
            snippets.append({"file": path, "symbol": symbol, "snippet": ""})
    snippets = snippets[:MAX_CONTEXT_SNIPPETS]
    files = files[:MAX_CONTEXT_FILES]

    logger.info("[context_builder] %d symbols, %d references, %d files, %d snippets (limits: %d files, %d snippets)", len(symbols), len(references), len(files), len(snippets), MAX_CONTEXT_FILES, MAX_CONTEXT_SNIPPETS)
    out = {
        "symbols": symbols,
        "references": references,
        "files": files,
        "snippets": snippets,
    }
    if call_chain_ctx:
        out["call_chain"] = call_chain_ctx
    return out
