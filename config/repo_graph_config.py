"""Repository graph and symbol index configuration."""

import os
from pathlib import Path


SYMBOL_GRAPH_DIR = os.getenv("SYMBOL_GRAPH_DIR", ".symbol_graph")
REPO_MAP_JSON = os.getenv("REPO_MAP_JSON", "repo_map.json")
SYMBOLS_JSON = os.getenv("SYMBOLS_JSON", "symbols.json")
INDEX_SQLITE = os.getenv("INDEX_SQLITE", "index.sqlite")
MAX_EXPANSION_DEPTH = int(os.getenv("GRAPH_EXPANSION_DEPTH", "2"))


def get_repo_map_path(project_root: str | Path) -> Path:
    """Return path to repo_map.json under project root."""
    root = Path(project_root).resolve()
    return root / SYMBOL_GRAPH_DIR / REPO_MAP_JSON


def get_symbol_graph_path(project_root: str | Path) -> Path:
    """Return path to .symbol_graph directory under project root."""
    root = Path(project_root).resolve()
    return root / SYMBOL_GRAPH_DIR
