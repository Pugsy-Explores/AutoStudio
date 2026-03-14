"""Plan safe code edits before execution. Identify affected symbols and impacted files."""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

ENABLE_DIFF_PLANNER = os.environ.get("ENABLE_DIFF_PLANNER", "1").lower() in ("1", "true", "yes")


def plan_diff(instruction: str, context: dict) -> dict:
    """
    Plan code edits from instruction and context.
    Returns {changes: [{file, symbol, action, patch, reason}, ...]}.
    action: "modify" | "add" | "delete"
    """
    changes: list[dict] = []
    ranked_context = context.get("ranked_context") or []
    retrieved_symbols = context.get("retrieved_symbols") or []
    retrieved_files = context.get("retrieved_files") or []

    # Collect affected symbols from context
    affected_symbols: set[tuple[str, str]] = set()
    for s in retrieved_symbols:
        if isinstance(s, dict):
            f = s.get("file") or ""
            sym = s.get("symbol") or ""
            if f or sym:
                affected_symbols.add((f, sym))
    for c in ranked_context:
        if isinstance(c, dict):
            f = c.get("file") or ""
            sym = c.get("symbol") or ""
            if f or sym:
                affected_symbols.add((f, sym))

    # Query graph for callers when index exists
    project_root = context.get("project_root") or os.environ.get("SERENA_PROJECT_DIR") or os.getcwd()
    index_path = Path(project_root) / ".symbol_graph" / "index.sqlite"
    impacted_files: set[str] = set(retrieved_files)

    if index_path.is_file():
        try:
            from repo_graph.graph_query import expand_neighbors, find_symbol
            from repo_graph.graph_storage import GraphStorage

            storage = GraphStorage(str(index_path))
            try:
                for file_path, symbol in affected_symbols:
                    if not symbol:
                        impacted_files.add(file_path)
                        continue
                    node = find_symbol(symbol, storage)
                    if node:
                        symbol_id = node.get("id")
                        if symbol_id is not None:
                            # Get callers (incoming edges)
                            neighbors = storage.get_neighbors(symbol_id, direction="in")
                            for n in neighbors:
                                f = n.get("file", "")
                                if f:
                                    impacted_files.add(f)
            finally:
                storage.close()
        except ImportError:
            pass

    # Build changes from instruction and impacted context
    for file_path, symbol in affected_symbols:
        changes.append({
            "file": file_path,
            "symbol": symbol,
            "action": "modify",
            "patch": f"Apply changes from: {instruction[:200]}",
            "reason": "Primary symbol from context",
        })

    for f in impacted_files:
        if not any(c.get("file") == f for c in changes):
            changes.append({
                "file": f,
                "symbol": "",
                "action": "modify",
                "patch": f"Review for impact: {instruction[:200]}",
                "reason": "Caller or dependent file",
            })

    # Deduplicate: prefer (file, symbol) entries
    seen: set[tuple[str, str]] = set()
    deduped: list[dict] = []
    for c in changes:
        key = (c.get("file", ""), c.get("symbol", ""))
        if key not in seen:
            seen.add(key)
            deduped.append(c)

    logger.info("[diff_planner] planned changes=%d", len(deduped))
    return {"changes": deduped}
