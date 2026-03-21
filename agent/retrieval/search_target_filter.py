"""
Stage 13: filter and rank raw search hits so EDIT sees files, not index roots or directories.

Applied inside ``run_retrieval_pipeline`` before anchor detection.

Stage 18: when the query (plus optional parent instruction) suggests docs/code alignment,
keep markdown and Python hits — Stage 13 previously dropped all ``.md`` whenever any ``.py`` existed.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from agent.retrieval.task_semantics import instruction_suggests_docs_consistency

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


def _score_result(r: dict, query_lower: str, *, docs_alignment: bool = False) -> float:
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
    if docs_alignment:
        if f.endswith(".md"):
            s += 22.0
            if "readme" in f or "readme" in query_lower:
                s += 10.0
        elif f.endswith((".toml", ".json", ".txt", ".yml", ".yaml")):
            s -= 6.0
    else:
        if f.endswith((".md", ".toml", ".json", ".txt", ".yml", ".yaml")):
            s -= 25.0
    return s


def _merged_semantic_text(query: str | None, parent_instruction: str | None) -> str:
    parts = []
    if query and query.strip():
        parts.append(query.strip())
    if parent_instruction and str(parent_instruction).strip():
        pi = str(parent_instruction).strip()
        if pi not in " ".join(parts):
            parts.append(pi)
    return " ".join(parts).strip()


def filter_and_rank_search_results(
    results: list[dict],
    query: str | None,
    project_root: str,
    *,
    parent_instruction: str | None = None,
    extra_path_roots: tuple[str, ...] | None = None,
) -> list[dict]:
    """
    Drop directories, index metadata paths, and paths outside project_root.
    Prefer concrete source files; sort by heuristic score + retriever score.

    When the graph index lives under a temp dir but hits reference the real checkout,
    pass ``extra_path_roots`` (e.g. state.context[\"source_root\"]) so those files are kept.
    """
    if not results or not isinstance(results, list):
        return []

    root = Path(project_root).resolve()
    extra_roots: list[Path] = []
    if extra_path_roots:
        for x in extra_path_roots:
            if x and str(x).strip():
                try:
                    extra_roots.append(Path(x).resolve())
                except OSError:
                    pass

    def _relative_under_any(abs_path: Path) -> tuple[Path, Path] | None:
        """Return (chosen_root, rel) if abs_path lies under root or any extra root."""
        for base in (root, *extra_roots):
            try:
                rel = abs_path.relative_to(base)
                return base, rel
            except ValueError:
                continue
        return None
    merged = _merged_semantic_text(query, parent_instruction)
    docs_alignment = instruction_suggests_docs_consistency(merged)
    query_lower = (query or "").lower()
    if docs_alignment and merged:
        query_lower = merged.lower()
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
        rel_info = _relative_under_any(p)
        if rel_info is None:
            continue
        _base_root, rel = rel_info
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
        _need_snip = str(p).lower().endswith((".py", ".pyi"))
        if docs_alignment:
            _need_snip = _need_snip or str(p).lower().endswith((".md", ".mdx"))
        if not (nr.get("snippet") or "").strip() and _need_snip:
            # SEARCH policy requires a non-empty snippet; graph/BM25 sometimes omit it.
            nr["snippet"] = "(no snippet)"
        nr["_target_filter_score"] = _score_result(nr, query_lower, docs_alignment=docs_alignment)
        normalized.append(nr)

    normalized.sort(key=lambda x: float(x.get("_target_filter_score", 0)), reverse=True)

    # Strip internal key before downstream use
    for nr in normalized:
        nr.pop("_target_filter_score", None)

    has_py = any(str(x.get("file") or "").lower().endswith(".py") for x in normalized)
    if normalized and not has_py:
        if docs_alignment:
            has_doc = any(
                str(x.get("file") or "").lower().endswith((".md", ".mdx"))
                for x in normalized
            )
            if not has_doc:
                logger.debug("[search_target_filter] docs_alignment: no .py or .md; clearing")
                normalized = []
        else:
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
            ri = _relative_under_any(p)
            if ri is None:
                continue
            _br, rel_salv = ri
            if p.is_file() and not _is_blocked_path(str(rel_salv).lower()):
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
            ri = _relative_under_any(p)
            if ri is None:
                continue
            _br, rel_dir = ri
            if not p.is_dir() or _is_blocked_path(str(rel_dir).lower()):
                continue
            try:
                for sub in sorted(p.rglob("*.py"))[:12]:
                    if not sub.is_file():
                        continue
                    ri_sub = _relative_under_any(sub.resolve())
                    if ri_sub is None:
                        continue
                    rel = str(ri_sub[1]).lower()
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
            nr["_target_filter_score"] = _score_result(nr, query_lower, docs_alignment=docs_alignment)
        normalized.sort(key=lambda x: float(x.get("_target_filter_score", 0)), reverse=True)
        for nr in normalized:
            nr.pop("_target_filter_score", None)

    if docs_alignment:
        keep_ext = (".py", ".pyi", ".md", ".mdx")
        paired = [
            x
            for x in normalized
            if str(x.get("file") or "").lower().endswith(keep_ext)
        ]
        if paired:
            normalized = paired
    else:
        py_only = [
            x
            for x in normalized
            if str(x.get("file") or "").lower().endswith((".py", ".pyi"))
        ]
        if py_only:
            normalized = py_only

    return normalized
