"""BM25 lexical retrieval for exact identifier and keyword queries.

Indexes symbol names, docstrings, file paths. Complements vector search
for exact-match and lexical patterns.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from config.repo_graph_config import INDEX_SQLITE, SYMBOL_GRAPH_DIR

logger = logging.getLogger(__name__)

_BM25_INDEX: object | None = None
_REPO_SYMBOLS: list[dict] = []
_PROJECT_ROOT: str | None = None


def _reset_for_testing() -> None:
    """Reset module state. Only for use in tests."""
    global _BM25_INDEX, _REPO_SYMBOLS, _PROJECT_ROOT  # noqa: PLW0603
    _BM25_INDEX = None
    _REPO_SYMBOLS = []
    _PROJECT_ROOT = None


def _tokenize(text: str) -> list[str]:
    """Simple tokenizer: split on non-alphanumeric, lowercase, filter short."""
    if not text:
        return []
    tokens = re.findall(r"[a-zA-Z0-9_]+", str(text))
    return [t.lower() for t in tokens if len(t) > 1]


def _doc_text(sym: dict) -> str:
    """Build searchable text from symbol: name, file, docstring, signature."""
    parts = [
        sym.get("name") or sym.get("symbol_name") or "",
        sym.get("file") or "",
        sym.get("docstring") or "",
        sym.get("signature") or "",
    ]
    return " ".join(p for p in parts if p)


def build_bm25_index(project_root: str | None = None) -> bool:
    """Build BM25 index from repo symbols (graph + repo_map).

    Loads symbols from GraphStorage when index.sqlite exists; falls back
    to repo_map.json. Returns True if index built successfully.
    """
    global _BM25_INDEX, _REPO_SYMBOLS, _PROJECT_ROOT  # noqa: PLW0603

    root = Path(project_root or os.environ.get("SERENA_PROJECT_DIR") or os.getcwd()).resolve()
    _PROJECT_ROOT = str(root)

    symbols: list[dict] = []

    # Prefer graph (has docstrings, signatures)
    index_path = root / SYMBOL_GRAPH_DIR / INDEX_SQLITE
    if index_path.is_file():
        try:
            from repo_graph.graph_storage import GraphStorage  # noqa: PLC0415

            storage = GraphStorage(str(index_path))
            try:
                nodes = storage.get_all_nodes()
                for n in nodes:
                    symbols.append({
                        "name": n.get("name", ""),
                        "file": n.get("file", ""),
                        "docstring": n.get("docstring", ""),
                        "signature": n.get("signature", ""),
                        "line": n.get("start_line"),
                    })
            finally:
                storage.close()
        except Exception as e:
            logger.debug("[bm25] graph load failed: %s", e)

    # Fallback: repo_map
    if not symbols:
        try:
            from agent.retrieval.repo_map_lookup import load_repo_map  # noqa: PLC0415

            repo_map = load_repo_map(str(root))
            if repo_map and isinstance(repo_map.get("symbols"), dict):
                for name, info in repo_map["symbols"].items():
                    symbols.append({
                        "name": name,
                        "file": info.get("file", ""),
                        "docstring": "",
                        "signature": "",
                        "line": None,
                    })
        except Exception as e:
            logger.debug("[bm25] repo_map load failed: %s", e)

    if not symbols:
        logger.debug("[bm25] no symbols to index")
        return False

    _REPO_SYMBOLS = symbols
    corpus = [_tokenize(_doc_text(s)) for s in symbols]

    try:
        from rank_bm25 import BM25Okapi  # noqa: PLC0415

        _BM25_INDEX = BM25Okapi(corpus)
        logger.info("[bm25] indexed %d symbols", len(symbols))
        return True
    except ImportError:
        logger.warning("[bm25] rank_bm25 not installed; pip install rank-bm25")
        return False
    except RecursionError:
        # rank_bm25->numpy can raise RecursionError in some import loader contexts (installed but unusable)
        logger.warning(
            "[bm25] rank_bm25 import failed with RecursionError (numpy/loader); bm25 unavailable. "
            "Try: pip install numpy --upgrade && pip install rank-bm25 --force-reinstall"
        )
        return False


def search_bm25(query: str, project_root: str | None = None, top_k: int = 30) -> list[dict]:
    """Search BM25 index; return top_k results as {file, symbol, line, snippet}.

    Returns [] when index is empty or rank_bm25 is unavailable.
    """
    global _BM25_INDEX, _REPO_SYMBOLS, _PROJECT_ROOT  # noqa: PLW0603

    if not query or not query.strip():
        return []

    root = project_root or _PROJECT_ROOT or os.environ.get("SERENA_PROJECT_DIR") or os.getcwd()
    if not _BM25_INDEX or not _REPO_SYMBOLS:
        build_bm25_index(root)

    if not _BM25_INDEX or not _REPO_SYMBOLS:
        return []

    try:
        from rank_bm25 import BM25Okapi  # noqa: PLC0415
    except (ImportError, RecursionError):
        return []

    tokenized_query = _tokenize(query)
    if not tokenized_query:
        return []

    scores = _BM25_INDEX.get_scores(tokenized_query)
    indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]

    results = []
    for i in indices:
        sym = _REPO_SYMBOLS[i]
        snippet = (sym.get("docstring") or sym.get("name") or "")[:300]
        results.append({
            "file": sym.get("file", ""),
            "symbol": sym.get("name", ""),
            "line": sym.get("line"),
            "snippet": snippet,
        })
    return results
