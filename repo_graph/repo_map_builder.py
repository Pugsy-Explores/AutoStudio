"""Build high-level architectural map of the repository from symbol graph."""

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from config.repo_graph_config import (
    INDEX_SQLITE,
    REPO_MAP_JSON,
    SYMBOL_GRAPH_DIR,
    SYMBOLS_JSON,
)

if TYPE_CHECKING:
    from repo_graph.graph_storage import GraphStorage

logger = logging.getLogger(__name__)


def _file_to_module_name(file_path: str, project_root: str | Path) -> str:
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


def _normalize_file_path(file_path: str, project_root: Path) -> str:
    """Convert absolute path to relative path from project root."""
    try:
        p = Path(file_path).resolve()
        if p.is_relative_to(project_root):
            return str(p.relative_to(project_root))
    except (ValueError, TypeError):
        pass
    return file_path


def build_repo_map_from_storage(
    graph_storage: "GraphStorage",
    output_path: str | Path | None = None,
    project_root: str | Path | None = None,
) -> dict:
    """
    Build spec-format repo map from GraphStorage.
    Returns {modules: {}, symbols: {}, calls: {}}.
    """
    root = project_root
    if root is None:
        # Infer from db_path: .symbol_graph/index.sqlite -> parent of .symbol_graph
        db_path = Path(graph_storage.db_path)
        if SYMBOL_GRAPH_DIR in db_path.parts:
            root = db_path.parent.parent
        else:
            root = db_path.parent
    root = Path(root).resolve()

    conn = graph_storage._connect()
    nodes_rows = conn.execute("SELECT id, name, type, file, start_line FROM nodes").fetchall()
    edges_rows = conn.execute("SELECT source_id, target_id, edge_type FROM edges").fetchall()

    id_to_node: dict[int, dict] = {}
    modules: dict[str, dict] = {}
    symbols: dict[str, dict] = {}
    calls: dict[str, list[str]] = {}

    for row in nodes_rows:
        nid = row[0]
        name = row[1] or ""
        sym_type = row[2] or ""
        file_path = row[3] or ""
        start_line = row[4] or 0
        if not name:
            continue
        mod_name = _file_to_module_name(file_path, root)
        rel_file = _normalize_file_path(file_path, root)
        id_to_node[nid] = {"id": nid, "name": name, "type": sym_type, "file": file_path, "start_line": start_line}
        symbols[name] = {"file": rel_file, "type": sym_type, "line": start_line, "module": mod_name}
        if mod_name not in modules:
            modules[mod_name] = {"files": [], "symbols": []}
        if rel_file not in modules[mod_name]["files"]:
            modules[mod_name]["files"].append(rel_file)
        if name not in modules[mod_name]["symbols"]:
            modules[mod_name]["symbols"].append(name)

    for row in edges_rows:
        src_id, tgt_id, edge_type = row[0], row[1], row[2]
        src = id_to_node.get(src_id)
        tgt = id_to_node.get(tgt_id)
        if src and tgt and src["name"] and tgt["name"]:
            src_name = src["name"]
            tgt_name = tgt["name"]
            if src_name not in calls:
                calls[src_name] = []
            if tgt_name not in calls[src_name]:
                calls[src_name].append(tgt_name)

    result = {"modules": modules, "symbols": symbols, "calls": calls}
    logger.info("[repo_map] modules=%d symbols=%d", len(modules), len(symbols))

    out_path = Path(output_path) if output_path else root / SYMBOL_GRAPH_DIR / REPO_MAP_JSON
    if not out_path.is_absolute():
        out_path = root / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    return result


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


def _spec_to_legacy_format(spec_result: dict, root: Path) -> dict:
    """Convert spec format {modules: {}, symbols: {}, calls: {}} to legacy list format."""
    modules_spec = spec_result.get("modules") or {}
    symbols_spec = spec_result.get("symbols") or {}
    calls_spec = spec_result.get("calls") or {}

    # Build module deps from calls: if A in M1 calls B in M2, M1 depends on M2
    mod_deps: dict[str, set[str]] = {}
    for src, targets in calls_spec.items():
        src_info = symbols_spec.get(src, {})
        src_mod = src_info.get("module", "")
        if not src_mod:
            continue
        if src_mod not in mod_deps:
            mod_deps[src_mod] = set()
        for tgt in targets:
            tgt_info = symbols_spec.get(tgt, {})
            tgt_mod = tgt_info.get("module", "")
            if tgt_mod and tgt_mod != src_mod:
                mod_deps[src_mod].add(tgt_mod)

    modules_list: list[dict] = []
    for mod_name, mod_data in modules_spec.items():
        key_symbols = mod_data.get("symbols", [])[:20]
        deps = sorted(mod_deps.get(mod_name, set()))
        modules_list.append({
            "name": mod_name,
            "files": mod_data.get("files", []),
            "key_symbols": key_symbols,
            "dependencies": deps,
        })
    modules_list.sort(key=lambda m: m["name"])
    return {"modules": modules_list}


def build_repo_map(project_root: str, output_path: str | None = None) -> dict:
    """
    Generate high-level architectural map from symbol graph.
    When index.sqlite exists, uses build_repo_map_from_storage (spec format).
    Returns {modules: [{name, files, key_symbols, dependencies}, ...]} for backward compat.
    """
    root = Path(project_root).resolve()
    out_dir = root / SYMBOL_GRAPH_DIR
    symbols_path = out_dir / SYMBOLS_JSON
    index_path = out_dir / INDEX_SQLITE

    if index_path.exists():
        try:
            from repo_graph.graph_storage import GraphStorage

            storage = GraphStorage(str(index_path))
            try:
                out_path = Path(output_path) if output_path else out_dir / REPO_MAP_JSON
                spec_result = build_repo_map_from_storage(storage, out_path, root)
                return _spec_to_legacy_format(spec_result, root)
            finally:
                storage.close()
        except ImportError:
            pass

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
