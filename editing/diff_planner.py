"""Plan safe code edits before execution. Identify affected symbols and impacted files."""

import logging
import os
from pathlib import Path

from agent.retrieval.target_resolution import resolve_edit_targets_for_plan
from agent.retrieval.task_semantics import (
    instruction_path_hints as _instruction_path_hints,
    instruction_suggests_docs_consistency as _instruction_suggests_docs_consistency,
    instruction_edit_target_paths as _instruction_edit_target_paths,
)
from config.editing_config import MAX_FILES_EDITED

logger = logging.getLogger(__name__)

ENABLE_DIFF_PLANNER = os.environ.get("ENABLE_DIFF_PLANNER", "1").lower() in ("1", "true", "yes")


def _ranked_context_file_order(context: dict) -> list[str]:
    order: list[str] = []
    for key in (
        "ranked_context",
        "prior_phase_ranked_context",
        "context_snippets",
        "retrieved_symbols",
    ):
        for item in context.get(key) or []:
            if isinstance(item, dict):
                f = (item.get("file") or "").strip()
                if f and f not in order:
                    order.append(f)
    return order


def _file_matches_instruction_hint(file_path: str, hints: list[str]) -> bool:
    if not hints:
        return True
    fn = file_path.replace("\\", "/")
    for h in hints:
        hnorm = h.strip().replace("\\", "/")
        if hnorm in fn or fn.endswith(hnorm) or fn.endswith("/" + hnorm.lstrip("./")):
            return True
    return False


def _is_valid_edit_target(file_path: str, project_root: str, instruction: str = "") -> bool:
    """Valid edit target: .py/.pyi always; .md when instruction suggests docs-consistency."""
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
    suf = p.suffix.lower()
    if suf in (".py", ".pyi"):
        return not _is_blocked_edit_path(p)
    if suf == ".md" and _instruction_suggests_docs_consistency(instruction):
        return not _is_blocked_edit_path(p)
    return False


def _is_valid_py_edit_target(file_path: str, project_root: str, instruction: str = "") -> bool:
    """Python-only when instruction empty; allows .md when instruction suggests docs-consistency."""
    return _is_valid_edit_target(file_path, project_root, instruction)


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


def _path_relative_to_root(file_path: str, project_root: str) -> str:
    """Normalize path to project_root-relative for consistent patch resolution."""
    root = Path(project_root).resolve()
    p = Path(file_path)
    if not p.is_absolute():
        p = (root / file_path).resolve()
    else:
        p = p.resolve()
    try:
        return str(p.relative_to(root)).replace("\\", "/")
    except ValueError:
        return file_path.replace("\\", "/")


def _instruction_hint_file_targets(instruction: str, project_root: str) -> list[tuple[str, str]]:
    """
    Resolve path literals in the instruction (e.g. src/calc/ops.py, README.md) to project_root-relative paths.
    Stage 13.1: retrieval may rank tests above the real fix location; hints anchor the edit plan.
    Stage 17: include .md when instruction suggests docs-consistency.
    """
    root = Path(project_root).resolve()
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    allowed_suffixes = (".py", ".pyi")
    if _instruction_suggests_docs_consistency(instruction):
        allowed_suffixes = (".py", ".pyi", ".md")
    hints = _instruction_path_hints(instruction)
    # docs-consistency: prefer edit target by task semantics
    # - version alignment: edit constants.py (APP_VERSION) to match README
    # - stability/httpbin: edit .md to match .py
    if _instruction_suggests_docs_consistency(instruction):
        low = instruction.lower()
        if "version" in low and ("constants" in low or "app_version" in low):
            py_hints = [h for h in hints if h.strip().endswith((".py", ".pyi"))]
            md_hints = [h for h in hints if h.strip().endswith(".md")]
            hints = py_hints + md_hints
        else:
            md_hints = [h for h in hints if h.strip().endswith(".md")]
            py_hints = [h for h in hints if h.strip().endswith((".py", ".pyi"))]
            hints = md_hints + py_hints
    for hint in hints:
        h = hint.strip()
        if not h.endswith(allowed_suffixes):
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
        rel = str(p.relative_to(root)).replace("\\", "/")
        if rel not in seen:
            seen.add(rel)
            out.append((rel, ""))
    return out


def plan_diff(instruction: str, context: dict) -> dict:
    """
    Plan code edits from instruction and context.
    Returns {changes: [{file, symbol, action, patch, reason}, ...]}.
    action: "modify" | "add" | "delete"
    """
    changes: list[dict] = []
    ranked_context = list(context.get("ranked_context") or [])
    prior_rc = context.get("prior_phase_ranked_context") or []
    if isinstance(prior_rc, list) and prior_rc:
        ranked_context = ranked_context + [x for x in prior_rc if isinstance(x, dict)]
    retrieved_symbols = context.get("retrieved_symbols") or []
    retrieved_files = context.get("retrieved_files") or []

    project_root = context.get("project_root") or os.environ.get("SERENA_PROJECT_DIR") or os.getcwd()

    # Stage 25: target resolution — prefer source files over validation scripts
    resolution = resolve_edit_targets_for_plan(instruction, project_root, context)
    ranked = resolution.get("edit_targets_ranked", [])
    target_penalty_map: dict[str, int] = {fp: pen for fp, pen, _ in ranked}
    context["target_resolution"] = resolution

    # Collect affected symbols: use resolved targets with penalty < 80 first
    affected_symbols: set[tuple[str, str]] = set()
    edit_level = context.get("edit_target_level")
    symbol_short = context.get("edit_target_symbol_short")
    good_targets = [(fp, "") for fp, pen, _ in ranked if pen < 80]
    if edit_level == "file":
        # Retry hint: file-level only, no symbol anchoring (good_targets already has (fp, ""))
        pass
    elif symbol_short and good_targets:
        # Retry hint: use short symbol for primary target
        primary_fp = good_targets[0][0]
        affected_symbols.add((primary_fp, symbol_short))
    if good_targets:
        for fp, sym in good_targets:
            affected_symbols.add((fp, sym))
    # Fallback: explicit paths from instruction (benchmark + repair tasks name files)
    for fp, sym in _instruction_hint_file_targets(instruction, project_root):
        if target_penalty_map.get(fp, 50) < 80:
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
            file_norm = str(rp.relative_to(Path(project_root).resolve())).replace("\\", "/")
        except (OSError, ValueError):
            continue
        changes.append({
            "file": file_norm,
            "symbol": symbol,
            "action": "modify",
            "patch": f"Apply changes from: {instruction[:200]}",
            "reason": "Primary symbol from context",
        })

    changes_files_norm = {
        _path_relative_to_root(c.get("file", ""), project_root) for c in changes
    }
    for f in impacted_files:
        try:
            f_norm = _path_relative_to_root(f, project_root)
        except Exception:
            continue
        if f_norm not in changes_files_norm:
            try:
                pf = Path(f if Path(f).is_absolute() else str(Path(project_root) / f))
                pf = pf.resolve()
                if _is_blocked_edit_path(pf):
                    continue
            except OSError:
                continue
            changes.append({
                "file": f_norm,
                "symbol": "",
                "action": "modify",
                "patch": f"Review for impact: {instruction[:200]}",
                "reason": "Caller or dependent file",
            })
            changes_files_norm.add(f_norm)

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
    root = Path(project_root).resolve()
    pref_index: dict[str, int] = {}
    for i, path in enumerate(preferred):
        norm = _path_relative_to_root(path, project_root)
        if norm not in pref_index:
            pref_index[norm] = i
    path_hints = _instruction_path_hints(instruction)

    edit_targets = _instruction_edit_target_paths(instruction)

    def _sort_key(c: dict) -> tuple[int, int, int, int, int, str]:
        fp = c.get("file", "") or ""
        fp_norm = _path_relative_to_root(fp, project_root)
        has_sym = 0 if c.get("symbol") else 1
        pri = pref_index.get(fp_norm, 9999)
        hint_miss = 0 if _file_matches_instruction_hint(fp_norm, path_hints) else 1
        edit_target_miss = 0 if _file_matches_instruction_hint(fp_norm, edit_targets) else 1
        # Stage 25: prefer source over validation script (lower penalty = better)
        resolution_penalty = target_penalty_map.get(fp_norm, 50)
        return (resolution_penalty, edit_target_miss, hint_miss, pri, has_sym, fp_norm)

    filtered = [
        c
        for c in deduped
        if _is_valid_edit_target(c.get("file", "") or "", project_root, instruction)
    ]
    filtered.sort(key=_sort_key)
    if not filtered and deduped:
        logger.info("[diff_planner] no on-disk .py targets validated; using planned targets (tests/offline)")
        filtered = deduped[:MAX_FILES_EDITED]
    else:
        filtered = filtered[:MAX_FILES_EDITED]
    deduped = filtered

    logger.info("[diff_planner] planned changes=%d", len(deduped))
    return {"changes": deduped}
