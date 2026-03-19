"""Plan safe code edits before execution. Identify affected symbols and impacted files."""

import logging
import os
import re
from pathlib import Path

from config.editing_config import MAX_FILES_EDITED

logger = logging.getLogger(__name__)

ENABLE_DIFF_PLANNER = os.environ.get("ENABLE_DIFF_PLANNER", "1").lower() in ("1", "true", "yes")


def _ranked_context_file_order(context: dict) -> list[str]:
    order: list[str] = []
    for key in ("ranked_context", "context_snippets", "retrieved_symbols"):
        for item in context.get(key) or []:
            if isinstance(item, dict):
                f = (item.get("file") or "").strip()
                if f and f not in order:
                    order.append(f)
    return order


def _instruction_path_hints(instruction: str) -> list[str]:
    if not instruction:
        return []
    return re.findall(r"[\w./\\]+\.py\b", instruction)


def _file_matches_instruction_hint(file_path: str, hints: list[str]) -> bool:
    if not hints:
        return True
    fn = file_path.replace("\\", "/")
    for h in hints:
        hnorm = h.strip().replace("\\", "/")
        if hnorm in fn or fn.endswith(hnorm) or fn.endswith("/" + hnorm.lstrip("./")):
            return True
    return False


def _is_valid_py_edit_target(file_path: str, project_root: str) -> bool:
    if not file_path or not isinstance(file_path, str):
        return False
    p = Path(file_path)
    if not p.is_absolute():
        p = Path(project_root) / file_path
    try:
        p = p.resolve()
    except OSError:
        return False
    if not p.is_file():
        return False
    if p.suffix.lower() not in (".py", ".pyi"):
        return False
    return not _is_blocked_edit_path(p)


def _is_blocked_edit_path(resolved: Path) -> bool:
    """Match patch_executor: no index artifacts, DBs, or .symbol_graph."""
    try:
        parts_lower = {x.lower() for x in resolved.parts}
    except (ValueError, OSError):
        return True
    if ".symbol_graph" in parts_lower:
        return True
    name = resolved.name.lower()
    if name in ("index.sqlite", "repo_map.json", "symbols.json"):
        return True
    if name.endswith((".sqlite", ".db")):
        return True
    return False


def _instruction_hint_file_targets(instruction: str, project_root: str) -> list[tuple[str, str]]:
    """
    Resolve path literals in the instruction (e.g. src/calc/ops.py) to absolute files.
    Stage 13.1: retrieval may rank tests above the real fix location; hints anchor the edit plan.
    """
    root = Path(project_root).resolve()
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for hint in _instruction_path_hints(instruction):
        h = hint.strip()
        if not h.endswith((".py", ".pyi")):
            continue
        p = Path(h)
        if not p.is_absolute():
            p = (root / h).resolve()
        else:
            p = p.resolve()
        try:
            p.relative_to(root)
        except ValueError:
            continue
        if not p.is_file() or _is_blocked_edit_path(p):
            continue
        sp = str(p)
        if sp not in seen:
            seen.add(sp)
            out.append((sp, ""))
    return out


def plan_diff(instruction: str, context: dict) -> dict:
    """
    Plan code edits from instruction and context.
    Returns {changes: [{file, symbol, action, patch, reason}, ...]}.
    action: "modify" | "add" | "delete"
    """
    changes: list[dict] = []
    ranked_context = context.get("ranked_context") or []
    retrieved_symbols = context.get("retrieved_symbols") or []
    retrieved_files = context.get("retrieved_files") or []

    project_root = context.get("project_root") or os.environ.get("SERENA_PROJECT_DIR") or os.getcwd()

    # Collect affected symbols from context
    affected_symbols: set[tuple[str, str]] = set()
    # Prefer explicit paths mentioned in the instruction (benchmark + repair tasks name files).
    for fp, sym in _instruction_hint_file_targets(instruction, project_root):
        affected_symbols.add((fp, sym))
    for s in retrieved_symbols:
        if isinstance(s, dict):
            f = s.get("file") or ""
            sym = s.get("symbol") or ""
            if f or sym:
                affected_symbols.add((f, sym))
    for c in ranked_context:
        if isinstance(c, dict):
            f = c.get("file") or ""
            sym = c.get("symbol") or ""
            if f or sym:
                affected_symbols.add((f, sym))

    # Query graph for callers when index exists
    index_path = Path(project_root) / ".symbol_graph" / "index.sqlite"
    impacted_files: set[str] = set(retrieved_files)

    if index_path.is_file():
        try:
            from repo_graph.graph_query import expand_neighbors, find_symbol
            from repo_graph.graph_storage import GraphStorage

            storage = GraphStorage(str(index_path))
            try:
                for file_path, symbol in affected_symbols:
                    if not symbol:
                        impacted_files.add(file_path)
                        continue
                    node = find_symbol(symbol, storage)
                    if node:
                        symbol_id = node.get("id")
                        if symbol_id is not None:
                            # Get callers (incoming edges)
                            neighbors = storage.get_neighbors(symbol_id, direction="in")
                            for n in neighbors:
                                f = n.get("file", "")
                                if not f:
                                    continue
                                try:
                                    pf = Path(f if Path(f).is_absolute() else str(Path(project_root) / f))
                                    pf = pf.resolve()
                                    if _is_blocked_edit_path(pf):
                                        continue
                                except OSError:
                                    continue
                                impacted_files.add(f)
            finally:
                storage.close()
        except ImportError:
            pass

    # Build changes from instruction and impacted context
    for file_path, symbol in affected_symbols:
        try:
            rp = Path(file_path)
            if not rp.is_absolute():
                rp = Path(project_root) / file_path
            rp = rp.resolve()
            if _is_blocked_edit_path(rp):
                continue
        except OSError:
            continue
        changes.append({
            "file": file_path,
            "symbol": symbol,
            "action": "modify",
            "patch": f"Apply changes from: {instruction[:200]}",
            "reason": "Primary symbol from context",
        })

    for f in impacted_files:
        if not any(c.get("file") == f for c in changes):
            try:
                pf = Path(f if Path(f).is_absolute() else str(Path(project_root) / f))
                pf = pf.resolve()
                if _is_blocked_edit_path(pf):
                    continue
            except OSError:
                continue
            changes.append({
                "file": f,
                "symbol": "",
                "action": "modify",
                "patch": f"Review for impact: {instruction[:200]}",
                "reason": "Caller or dependent file",
            })

    # Deduplicate: prefer (file, symbol) entries
    seen: set[tuple[str, str]] = set()
    deduped: list[dict] = []
    for c in changes:
        key = (c.get("file", ""), c.get("symbol", ""))
        if key not in seen:
            seen.add(key)
            deduped.append(c)

    # Stage 13: only existing Python files; prefer ranked_context order; cap breadth
    preferred = _ranked_context_file_order(context)
    pref_index = {path: i for i, path in enumerate(preferred)}
    path_hints = _instruction_path_hints(instruction)

    def _sort_key(c: dict) -> tuple[int, int, int, str]:
        fp = c.get("file", "") or ""
        has_sym = 0 if c.get("symbol") else 1
        pri = pref_index.get(fp, 9999)
        hint_miss = 0 if _file_matches_instruction_hint(fp, path_hints) else 1
        return (hint_miss, pri, has_sym, fp)

    filtered = [c for c in deduped if _is_valid_py_edit_target(c.get("file", "") or "", project_root)]
    filtered.sort(key=_sort_key)
    if not filtered and deduped:
        logger.info("[diff_planner] no on-disk .py targets validated; using planned targets (tests/offline)")
        filtered = deduped[:MAX_FILES_EDITED]
    else:
        filtered = filtered[:MAX_FILES_EDITED]
    deduped = filtered

    logger.info("[diff_planner] planned changes=%d", len(deduped))
    return {"changes": deduped}
