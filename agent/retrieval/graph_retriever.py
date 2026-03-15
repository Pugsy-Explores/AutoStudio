"""Graph-based symbol retrieval: lookup + expansion for SEARCH pipeline."""

import logging
import os
import re
from pathlib import Path

from agent.retrieval.retrieval_expander import normalize_file_path
from config.repo_graph_config import INDEX_SQLITE, SYMBOL_GRAPH_DIR
from config.retrieval_config import GRAPH_EXPANSION_DEPTH, MAX_RETRIEVED_SYMBOLS

logger = logging.getLogger(__name__)

# Filler words to strip when extracting symbol candidates from natural language queries
_FILLER_WORDS = frozenset(
    {"find", "where", "the", "is", "a", "an", "to", "of", "for", "in", "on", "at", "do", "we"}
)


def _extract_symbol_candidates(query: str) -> list[str]:
    """
    Extract likely symbol names from natural language query.
    Returns candidates in priority order: CamelCase, snake_case, then longest token.
    """
    if not query or not query.strip():
        return []
    text = query.strip()
    candidates: list[str] = []
    seen: set[str] = set()

    # CamelCase: StepExecutor, DiffPlanner
    for m in re.finditer(r"\b([A-Z][a-z]+(?:[A-Z][a-z0-9]+)*)\b", text):
        s = m.group(1)
        if s not in seen:
            seen.add(s)
            candidates.append(s)

    # snake_case: diff_planner, plan_diff, validate_patch
    for m in re.finditer(r"\b([a-z][a-z0-9_]*(?:_[a-z0-9_]+)+)\b", text):
        s = m.group(1)
        if s not in seen:
            seen.add(s)
            candidates.append(s)

    # Multi-word phrases as snake_case: "diff planner" -> diff_planner
    tokens = [t for t in re.split(r"[\s,?.!]+", text) if t and t.lower() not in _FILLER_WORDS]
    if len(tokens) >= 2:
        combined = "_".join(t.lower() for t in tokens[:3])
        if combined not in seen:
            seen.add(combined)
            candidates.append(combined)

    # Remaining tokens that look like identifiers (longest first)
    for t in sorted(tokens, key=len, reverse=True):
        if len(t) >= 2 and t not in seen and re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", t):
            seen.add(t)
            candidates.append(t)

    return candidates


def retrieve_symbol_context(query: str, project_root: str | None = None) -> dict | None:
    """
    Retrieve symbol context from precomputed graph.
    Pipeline: symbol lookup -> graph expansion (2 hops) -> return results.
    Returns {results: [{file, symbol, line, snippet}], query} or None when no index.
    """
    if not query or not query.strip():
        return {"results": [], "query": query}

    root = Path(project_root or os.environ.get("SERENA_PROJECT_DIR") or os.getcwd()).resolve()
    index_path = root / SYMBOL_GRAPH_DIR / INDEX_SQLITE
    if not index_path.is_file():
        logger.debug("[graph_retriever] no index at %s", index_path)
        return None

    try:
        from repo_graph.graph_query import expand_neighbors, find_symbol
        from repo_graph.graph_storage import GraphStorage
    except ImportError:
        logger.debug("[graph_retriever] repo_graph not available")
        return None

    storage = GraphStorage(str(index_path))
    try:
        node = find_symbol(query.strip(), storage)
        if not node:
            # Try extracted symbol candidates from natural language query
            for candidate in _extract_symbol_candidates(query):
                node = find_symbol(candidate, storage)
                if node:
                    logger.debug("[graph_retriever] matched via candidate %r", candidate)
                    break
        if not node:
            return None

        symbol_id = node.get("id")
        if symbol_id is None:
            return None

        expanded = expand_neighbors(
            symbol_id, depth=GRAPH_EXPANSION_DEPTH, storage=storage
        )
        expanded = expanded[:MAX_RETRIEVED_SYMBOLS]

        results = []
        for n in expanded:
            file_path = normalize_file_path(n.get("file", ""))
            if not file_path:
                continue
            name = n.get("name", "")
            line = n.get("start_line") or 0
            snippet = (n.get("docstring") or name or "")[:300]
            results.append({
                "file": file_path,
                "symbol": name,
                "line": line,
                "snippet": snippet,
            })

        logger.info(
            "[graph_retriever] expansion depth=%d nodes=%d",
            GRAPH_EXPANSION_DEPTH,
            len(results),
        )
        return {"results": results, "query": query}
    finally:
        storage.close()
