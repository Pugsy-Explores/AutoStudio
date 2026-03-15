"""Incremental repo map update: refresh repo_map.json when a file changes."""

import logging
import os
from pathlib import Path

from config.repo_graph_config import INDEX_SQLITE, SYMBOL_GRAPH_DIR
from repo_graph.repo_map_builder import build_repo_map_from_storage

logger = logging.getLogger(__name__)


def update_repo_map_for_file(file_path: str, project_root: str | None = None) -> None:
    """
    Update repo_map.json after a file has been modified.
    Rebuilds repo_map from the updated graph (index.sqlite).
    Call after update_index_for_file.
    """
    path = Path(file_path).resolve()
    if not path.exists() or not path.is_file():
        return

    root = Path(project_root).resolve() if project_root else path.parent
    while root != root.parent and not (root / SYMBOL_GRAPH_DIR).is_dir():
        root = root.parent
    if not (root / SYMBOL_GRAPH_DIR).is_dir():
        root = Path(os.environ.get("SERENA_PROJECT_DIR", os.getcwd())).resolve()

    index_path = root / SYMBOL_GRAPH_DIR / INDEX_SQLITE
    if not index_path.is_file():
        return

    try:
        from repo_graph.graph_storage import GraphStorage

        storage = GraphStorage(str(index_path))
        try:
            build_repo_map_from_storage(storage, None, root)
            logger.info("[repo_map] updated file %s", path.name)
        finally:
            storage.close()
    except Exception as e:
        logger.debug("[repo_map] update failed for %s: %s", file_path, e)
