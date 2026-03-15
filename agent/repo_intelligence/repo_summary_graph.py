"""Repository summary graph: high-level map of modules, entrypoints, key classes, dependencies."""

import logging
from pathlib import Path

from config.repo_intelligence_config import MAX_REPO_SCAN_FILES
from repo_graph.repo_map_builder import build_repo_map

logger = logging.getLogger(__name__)

_ENTRYPOINT_PATTERNS = ("__main__", "main", "app", "cli", "entrypoint", "run")


def build_repo_summary_graph(root: str) -> dict:
    """
    Build high-level repo map: modules, entrypoints, key classes, dependency edges.
    Capped at MAX_REPO_SCAN_FILES to prevent runaway scans.
    """
    root_path = Path(root).resolve()
    repo_map = build_repo_map(str(root_path))

    modules_raw = repo_map.get("modules") or []
    total_files = sum(len(m.get("files") or []) for m in modules_raw)

    if total_files > MAX_REPO_SCAN_FILES:
        logger.warning(
            "[repo_summary_graph] capping scan: %d files > MAX_REPO_SCAN_FILES=%d",
            total_files,
            MAX_REPO_SCAN_FILES,
        )
        modules: list[dict] = []
        file_count = 0
        for m in modules_raw:
            if file_count >= MAX_REPO_SCAN_FILES:
                break
            files = m.get("files") or []
            take = min(len(files), MAX_REPO_SCAN_FILES - file_count)
            if take > 0:
                modules.append({
                    "name": m.get("name", ""),
                    "files": files[:take],
                    "key_symbols": m.get("key_symbols", [])[:20],
                    "dependencies": m.get("dependencies", []),
                })
                file_count += take
    else:
        modules = [
            {
                "name": m.get("name", ""),
                "files": m.get("files", []),
                "key_symbols": m.get("key_symbols", [])[:20],
                "dependencies": m.get("dependencies", []),
            }
            for m in modules_raw
        ]

    entrypoints: list[str] = []
    for m in modules:
        name = (m.get("name") or "").lower()
        for pat in _ENTRYPOINT_PATTERNS:
            if pat in name:
                entrypoints.append(m.get("name", ""))
                break

    key_classes: list[dict] = []
    for m in modules:
        for sym in m.get("key_symbols", []):
            if isinstance(sym, str) and sym[0].isupper():
                key_classes.append({"module": m.get("name", ""), "class": sym})
            elif isinstance(sym, dict) and sym.get("type") == "class":
                key_classes.append({
                    "module": m.get("name", ""),
                    "class": sym.get("name", sym.get("class", "")),
                })
    key_classes = key_classes[:100]

    dependency_edges: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for m in modules:
        src = m.get("name", "")
        for dep in m.get("dependencies", []):
            if dep and (src, dep) not in seen:
                seen.add((src, dep))
                dependency_edges.append({"from": src, "to": dep})

    result = {
        "modules": modules,
        "entrypoints": entrypoints,
        "key_classes": key_classes,
        "dependency_edges": dependency_edges,
    }
    logger.info(
        "[repo_summary_graph] modules=%d entrypoints=%d key_classes=%d edges=%d",
        len(modules),
        len(entrypoints),
        len(key_classes),
        len(dependency_edges),
    )
    return result
