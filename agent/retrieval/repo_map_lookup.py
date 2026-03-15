"""Repo map lookup: match query tokens against precomputed symbol map."""

import json
import logging
import os
import re
from pathlib import Path

from config.repo_graph_config import REPO_MAP_JSON, SYMBOL_GRAPH_DIR

logger = logging.getLogger(__name__)


def load_repo_map(project_root: str | None = None) -> dict | None:
    """Load repo_map.json; return dict or None."""
    root = Path(project_root or os.environ.get("SERENA_PROJECT_DIR", os.getcwd())).resolve()
    map_path = root / SYMBOL_GRAPH_DIR / REPO_MAP_JSON
    if not map_path.is_file():
        return None
    try:
        with open(map_path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _query_terms(query: str | None) -> set[str]:
    """Normalize query into tokens for matching (alphanumeric, underscores)."""
    if not query or not query.strip():
        return set()
    tokens = re.findall(r"[a-zA-Z0-9_]+", query.strip())
    return {t for t in tokens if len(t) > 1}


def lookup_repo_map(query: str, project_root: str | None = None) -> list[dict]:
    """
    Match query tokens against repo_map symbols; return anchor candidates.
    Returns [{"anchor": "StepExecutor", "file": "agent/execution/executor.py"}, ...].
    Returns [] if no repo_map or no matches.
    """
    root = Path(project_root or os.environ.get("SERENA_PROJECT_DIR", os.getcwd())).resolve()
    map_path = root / SYMBOL_GRAPH_DIR / REPO_MAP_JSON
    if not map_path.is_file():
        return []

    try:
        with open(map_path, encoding="utf-8") as f:
            repo_map = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.debug("[repo_map] lookup failed to load: %s", e)
        return []

    symbols = repo_map.get("symbols") or {}
    if not symbols:
        return []

    terms = _query_terms(query)
    if not terms:
        return []

    candidates: list[dict] = []
    seen: set[str] = set()

    # Exact match first
    for term in terms:
        if term in symbols and term not in seen:
            seen.add(term)
            info = symbols[term]
            candidates.append({
                "anchor": term,
                "file": info.get("file", ""),
            })
            logger.info("[repo_map] anchor=%s", term)

    # Substring match (case-insensitive)
    term_lower = {t: t.lower() for t in terms}
    for sym_name, info in symbols.items():
        if sym_name in seen:
            continue
        sym_lower = sym_name.lower()
        for term, t_lower in term_lower.items():
            if t_lower in sym_lower or sym_lower in t_lower:
                seen.add(sym_name)
                candidates.append({
                    "anchor": sym_name,
                    "file": info.get("file", ""),
                })
                logger.info("[repo_map] anchor=%s", sym_name)
                break

    return candidates
