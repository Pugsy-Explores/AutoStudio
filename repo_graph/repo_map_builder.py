"""Build high-level architectural map of the repository from symbol graph."""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

SYMBOL_GRAPH_DIR = ".symbol_graph"
SYMBOLS_JSON = "symbols.json"
INDEX_SQLITE = "index.sqlite"
REPO_MAP_JSON = "repo_map.json"


def _file_to_module_name(file_path: str, project_root: str) -> str:
    """Convert file path to module name (e.g. agent/execution/executor.py -> agent.execution.executor)."""
    root = Path(project_root).resolve()
    try:
        p = Path(file_path).resolve()
        if p.is_relative_to(root):
            rel = p.relative_to(root)
        else:
            rel = Path(file_path)
    except (ValueError, TypeError):
        rel = Path(file_path)
    parts = list(rel.parts)
    if parts and parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else ""


def _build_dependency_clusters(
    symbols: list[dict],
    storage,
    project_root: str,
) -> dict[str, set[str]]:
    """Build module -> set of dependency module names from graph edges."""
    from repo_graph.graph_query import find_symbol

    file_to_module: dict[str, str] = {}
    for s in symbols:
        f = s.get("file", "")
        if f:
            file_to_module[f] = _file_to_module_name(f, project_root)

    deps: dict[str, set[str]] = {}
    for s in symbols:
        f = s.get("file", "")
        if not f:
            continue
        mod = file_to_module.get(f, "")
        if mod not in deps:
            deps[mod] = set()

        name = s.get("symbol_name", "")
        if not name:
            continue
        node = find_symbol(name, storage)
        if not node:
            continue
        node_id = node.get("id")
        if node_id is None:
            continue

        for direction in ("out", "in"):
            neighbors = storage.get_neighbors(node_id, direction=direction)
            for n in neighbors:
                nfile = n.get("file", "")
                nname = n.get("name", "")
                if nfile:
                    dep_mod = file_to_module.get(nfile, _file_to_module_name(nfile, project_root))
                    if dep_mod and dep_mod != mod:
                        deps[mod].add(dep_mod)

    return deps


def build_repo_map(project_root: str, output_path: str | None = None) -> dict:
    """
    Generate high-level architectural map from symbol graph.
    Returns {modules: [{name, files, key_symbols, dependencies}, ...]}.
    """
    root = Path(project_root).resolve()
    out_dir = root / SYMBOL_GRAPH_DIR
    symbols_path = out_dir / SYMBOLS_JSON
    index_path = out_dir / INDEX_SQLITE

    symbols: list[dict] = []
    if symbols_path.exists():
        try:
            with open(symbols_path, encoding="utf-8") as f:
                symbols = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("[repo_map] failed to load symbols.json: %s", e)

    if not symbols:
        logger.info("[repo_map] no symbols found, returning empty map")
        return {"modules": []}

    # Group symbols by file (module)
    file_to_symbols: dict[str, list[dict]] = {}
    for s in symbols:
        f = s.get("file", "")
        if f:
            file_to_symbols.setdefault(f, []).append(s)

    # Build dependency clusters from graph
    deps: dict[str, set[str]] = {}
    if index_path.exists():
        try:
            from repo_graph.graph_storage import GraphStorage

            storage = GraphStorage(str(index_path))
            try:
                deps = _build_dependency_clusters(symbols, storage, str(root))
            finally:
                storage.close()
        except ImportError:
            pass

    # Build module entries
    modules: list[dict] = []
    for file_path, syms in file_to_symbols.items():
        mod_name = _file_to_module_name(file_path, str(root))
        key_symbols = [
            s.get("symbol_name", "").split(".")[-1] or s.get("symbol_name", "")
            for s in syms
            if s.get("symbol_type") in ("class", "function", "method") and s.get("symbol_name")
        ]
        key_symbols = list(dict.fromkeys(key_symbols))[:20]  # dedup, limit

        try:
            rel_path = str(Path(file_path).resolve().relative_to(root))
        except (ValueError, TypeError):
            rel_path = file_path
        modules.append({
            "name": mod_name or Path(file_path).stem,
            "files": [rel_path],
            "key_symbols": key_symbols,
            "dependencies": sorted(deps.get(mod_name, set())),
        })

    # Sort by module name
    modules.sort(key=lambda m: m["name"])

    result = {"modules": modules}
    total_deps = sum(len(m["dependencies"]) for m in modules)
    logger.info("[repo_map] modules=%d dependencies=%d", len(modules), total_deps)

    # Write output
    out_path = Path(output_path) if output_path else out_dir / REPO_MAP_JSON
    out_path = out_path if out_path.is_absolute() else root / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    return result
