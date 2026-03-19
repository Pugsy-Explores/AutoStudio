"""
Stage 13: filter and rank raw search hits so EDIT sees files, not index roots or directories.

Applied inside ``run_retrieval_pipeline`` before anchor detection.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_INDEX_PATH_MARKERS = (".symbol_graph", "__pycache__", ".git/", "/.git", "\\.git")


def _is_blocked_path(rel_lower: str) -> bool:
    if not rel_lower:
        return True
    for m in _INDEX_PATH_MARKERS:
        if m in rel_lower:
            return True
    if rel_lower.endswith("/.symbol_graph") or rel_lower.endswith(".symbol_graph"):
        return True
    return False


def _score_result(r: dict, query_lower: str) -> float:
    f = (r.get("file") or r.get("path") or "").lower()
    s = float(r.get("score", 0) or 0)
    if f.endswith(".py"):
        s += 12.0
    elif f.endswith(".pyi"):
        s += 8.0
    sym = (r.get("symbol") or "").strip()
    if sym:
        s += 4.0
        short = sym.split(".")[-1].lower()
        if short and len(short) > 1 and re.search(rf"\b{re.escape(short)}\b", query_lower):
            s += 18.0
    # Path fragments from query (e.g. src/calc/ops.py)
    for token in query_lower.replace("\\", "/").replace("/", " ").split():
        if len(token) > 2 and token in f:
            s += 6.0
    if "src/" in query_lower or "/src/" in query_lower:
        if "/src/" in f or f.startswith("src/"):
            s += 14.0
    if "/tests/" in f or f.startswith("tests/") or f.endswith("_test.py") or "/test_" in f:
        s -= 6.0
    if "benchmark_local/" in f or "benchmark_local\\" in f:
        s += 2.0
    if f.endswith((".md", ".toml", ".json", ".txt", ".yml", ".yaml")):
        s -= 25.0
    return s


def filter_and_rank_search_results(
    results: list[dict],
    query: str | None,
    project_root: str,
) -> list[dict]:
    """
    Drop directories, index metadata paths, and paths outside project_root.
    Prefer concrete source files; sort by heuristic score + retriever score.
    """
    if not results or not isinstance(results, list):
        return []

    root = Path(project_root).resolve()
    query_lower = (query or "").lower()
    normalized: list[dict] = []

    for r in results:
        if not isinstance(r, dict):
            continue
        raw = (r.get("file") or r.get("path") or "").strip()
        if not raw:
            continue
        p = Path(raw)
        if not p.is_absolute():
            p = (root / raw).resolve()
        else:
            p = p.resolve()
        try:
            rel = p.relative_to(root)
        except ValueError:
            continue
        rel_s = str(rel)
        low = rel_s.lower()
        if _is_blocked_path(low):
            continue
        if not p.exists():
            continue
        if p.is_dir():
            logger.debug("[search_target_filter] skip directory: %s", p)
            continue
        nr = dict(r)
        nr["file"] = str(p)
        if "path" in nr:
            nr["path"] = str(p)
        nr["_target_filter_score"] = _score_result(nr, query_lower)
        normalized.append(nr)

    normalized.sort(key=lambda x: float(x.get("_target_filter_score", 0)), reverse=True)

    # Strip internal key before downstream use
    for nr in normalized:
        nr.pop("_target_filter_score", None)

    has_py = any(str(x.get("file") or "").lower().endswith(".py") for x in normalized)
    if normalized and not has_py:
        logger.debug("[search_target_filter] discarding non-.py hits to allow directory expansion")
        normalized = []

    if not normalized and results:
        # Salvage: any result that resolves to a .py file under root
        for r in results:
            if not isinstance(r, dict):
                continue
            raw = (r.get("file") or r.get("path") or "").strip()
            if not raw.lower().endswith(".py"):
                continue
            p = Path(raw)
            if not p.is_absolute():
                p = (root / raw).resolve()
            else:
                p = p.resolve()
            try:
                p.relative_to(root)
            except ValueError:
                continue
            if p.is_file() and not _is_blocked_path(str(p.relative_to(root)).lower()):
                normalized.append(dict(r) | {"file": str(p)})
                break

    if not normalized and results:
        # Expand directory hits (e.g. vendor package root) into concrete .py files
        seen_paths: set[str] = set()
        for r in results:
            if not isinstance(r, dict):
                continue
            raw = (r.get("file") or r.get("path") or "").strip()
            if not raw:
                continue
            p = Path(raw)
            if not p.is_absolute():
                p = (root / raw).resolve()
            else:
                p = p.resolve()
            try:
                p.relative_to(root)
            except ValueError:
                continue
            if not p.is_dir() or _is_blocked_path(str(p.relative_to(root)).lower()):
                continue
            try:
                for sub in sorted(p.rglob("*.py"))[:12]:
                    if not sub.is_file():
                        continue
                    rel = str(sub.relative_to(root)).lower()
                    if _is_blocked_path(rel):
                        continue
                    sp = str(sub.resolve())
                    if sp not in seen_paths:
                        seen_paths.add(sp)
                        normalized.append(
                            {
                                "file": sp,
                                "symbol": r.get("symbol") or "",
                                "snippet": "",
                                "score": float(r.get("score", 0) or 0) * 0.5,
                                "source": "dir_expand",
                            }
                        )
            except OSError:
                continue
        for nr in normalized:
            nr["_target_filter_score"] = _score_result(nr, query_lower)
        normalized.sort(key=lambda x: float(x.get("_target_filter_score", 0)), reverse=True)
        for nr in normalized:
            nr.pop("_target_filter_score", None)

    py_only = [
        x
        for x in normalized
        if str(x.get("file") or "").lower().endswith((".py", ".pyi"))
    ]
    if py_only:
        normalized = py_only

    return normalized
