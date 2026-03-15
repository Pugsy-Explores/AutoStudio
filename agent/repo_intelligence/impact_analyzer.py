"""Impact analyzer: predict which files and symbols are affected by editing a file."""

import logging
from collections import deque
from pathlib import Path

from config.repo_graph_config import INDEX_SQLITE, SYMBOL_GRAPH_DIR
from config.repo_intelligence_config import MAX_IMPACT_DEPTH
from repo_graph.graph_storage import GraphStorage

logger = logging.getLogger(__name__)


def analyze_impact(file_path: str, root: str, depth: int | None = None) -> dict:
    """
    BFS traversal from symbols in file_path to find affected files and symbols.
    Returns {affected_files: [...], affected_symbols: [...], confidence: float}.
    """
    depth = depth if depth is not None else MAX_IMPACT_DEPTH
    root_path = Path(root).resolve()
    index_path = root_path / SYMBOL_GRAPH_DIR / INDEX_SQLITE

    if not index_path.exists():
        logger.warning("[impact_analyzer] index.sqlite not found at %s", index_path)
        return {"affected_files": [], "affected_symbols": [], "confidence": 0.0}

    try:
        storage = GraphStorage(str(index_path))
    except Exception as e:
        logger.warning("[impact_analyzer] failed to open GraphStorage: %s", e)
        return {"affected_files": [], "affected_symbols": [], "confidence": 0.0}

    try:
        path_str = str(Path(file_path).resolve())
        if not Path(path_str).is_absolute():
            path_str = str((root_path / file_path).resolve())
        conn = storage._connect()
        rows = conn.execute("SELECT id, name, file FROM nodes WHERE file = ?", (path_str,)).fetchall()
        if not rows:
            try:
                rel = str(Path(path_str).relative_to(root_path))
                rows = conn.execute("SELECT id, name, file FROM nodes WHERE file LIKE ?", (f"%{rel}",)).fetchall()
            except (ValueError, TypeError):
                pass
        if not rows:
            logger.info("[impact_analyzer] no symbols found for file %s", file_path)
            return {"affected_files": [file_path], "affected_symbols": [], "confidence": 0.5}

        start_ids = [r[0] for r in rows]
        visited: set[int] = set()
        affected_files: set[str] = set()
        affected_symbols: list[dict] = []

        queue: deque[tuple[int, int]] = deque((nid, 0) for nid in start_ids)
        for nid in start_ids:
            visited.add(nid)

        while queue:
            node_id, d = queue.popleft()
            if d >= depth:
                continue
            neighbors = storage.get_neighbors(node_id, direction="both")
            for n in neighbors:
                nid = n.get("id")
                if nid is None or nid in visited:
                    continue
                visited.add(nid)
                f = n.get("file", "")
                name = n.get("name", "")
                if f:
                    try:
                        rel = str(Path(f).relative_to(root_path))
                    except (ValueError, TypeError):
                        rel = f
                    affected_files.add(rel)
                if name:
                    affected_symbols.append({"name": name, "file": n.get("file", "")})
                queue.append((nid, d + 1))

        affected_files_list = sorted(affected_files)
        confidence = 0.9 if len(affected_symbols) > 0 else 0.6
        result = {
            "affected_files": affected_files_list,
            "affected_symbols": affected_symbols[:100],
            "confidence": confidence,
        }
        logger.info(
            "[impact_analyzer] file=%s affected_files=%d affected_symbols=%d",
            file_path,
            len(affected_files_list),
            len(affected_symbols),
        )
        return result
    finally:
        storage.close()
