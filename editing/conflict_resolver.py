"""Detect and resolve conflicting edits before patch execution."""

import logging
from collections import defaultdict

from editing.semantic_diff import detect_semantic_overlaps

logger = logging.getLogger(__name__)


def resolve_conflicts(patch_plan: dict) -> dict:
    """
    Check patch_plan for conflicts and optionally split into sequential groups.
    patch_plan: {changes: [{file, symbol, action, patch, reason}, ...]}
    Returns {valid, conflicts?, sequential_groups?}.
    """
    changes = patch_plan.get("changes", [])
    if not changes:
        return {"valid": True}

    conflicts: list[dict] = []

    # 1. Same symbol: multiple changes targeting (file, symbol)
    symbol_counts: dict[tuple[str, str], list[int]] = defaultdict(list)
    for i, c in enumerate(changes):
        key = (c.get("file", ""), c.get("symbol", ""))
        if key != ("", ""):
            symbol_counts[key].append(i)

    for key, indices in symbol_counts.items():
        if len(indices) > 1:
            conflicts.append({
                "type": "same_symbol",
                "file": key[0],
                "symbol": key[1],
                "change_indices": indices,
            })

    # 2. Same file: multiple edits to same file (potential range overlap)
    file_counts: dict[str, list[int]] = defaultdict(list)
    for i, c in enumerate(changes):
        f = c.get("file", "")
        if f:
            file_counts[f].append(i)

    for f, indices in file_counts.items():
        if len(indices) > 1:
            conflicts.append({
                "type": "same_file",
                "file": f,
                "change_indices": indices,
            })

    # 3. Semantic overlap: patches target same/overlapping AST region
    semantic = detect_semantic_overlaps(changes)
    for ov in semantic:
        indices = ov.get("change_indices", [])
        if indices and not any(
            c.get("type") == "semantic_overlap" and set(c.get("change_indices", [])) == set(indices)
            for c in conflicts
        ):
            conflicts.append(ov)

    if not conflicts:
        logger.info("[conflict_resolver] conflicts detected=0")
        return {"valid": True}

    logger.info("[conflict_resolver] conflicts detected=%d", len(conflicts))

    # Build sequential groups: split so no group has internal conflicts
    sequential_groups = _build_sequential_groups(changes, conflicts)

    return {
        "valid": False,
        "conflicts": conflicts,
        "sequential_groups": sequential_groups,
        "merge_strategy": "sequential",
    }


def _build_sequential_groups(changes: list[dict], conflicts: list[dict]) -> list[list[dict]]:
    """
    Split changes into groups so each group has no internal conflicts.
    Assign each change a group index; changes in same group are applied together.
    """
    # Assign group index per change: 0 = non-conflicted, 1+ = sequential for conflicted
    change_to_group: dict[int, int] = {i: 0 for i in range(len(changes))}

    for c in conflicts:
        indices = c.get("change_indices", [])
        for order, idx in enumerate(indices):
            if idx < len(changes):
                # Conflicted changes get group 1, 2, 3, ... (order of application)
                change_to_group[idx] = max(change_to_group[idx], order + 1)

    # Non-conflicted stay in group 0
    conflicted_indices = {i for i, g in change_to_group.items() if g > 0}
    for i in range(len(changes)):
        if i not in conflicted_indices:
            change_to_group[i] = 0

    # Build result: group 0 first (non-conflicted), then 1, 2, 3...
    max_group = max(change_to_group.values())
    result: list[list[dict]] = [[] for _ in range(max_group + 1)]
    for i, ch in enumerate(changes):
        g = change_to_group[i]
        result[g].append(ch)

    return [grp for grp in result if grp]
