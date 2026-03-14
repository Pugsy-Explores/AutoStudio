"""Merge strategies for conflicting patches."""


def merge_sequential(changes: list[dict], dependency_order: list[tuple[str, str]] | None = None) -> list[dict]:
    """
    Apply changes in dependency order. If A imports B, apply B before A.
    dependency_order: optional [(file, symbol), ...] in desired apply order.
    """
    if not changes:
        return []
    if dependency_order:
        order_map = {k: i for i, k in enumerate(dependency_order)}
        key_fn = lambda c: order_map.get((c.get("file", ""), c.get("symbol", "")), 999)
        return sorted(changes, key=key_fn)
    return changes


def merge_three_way(base: str, ours: str, theirs: str) -> str | None:
    """
    Attempt automatic merge when ours and theirs edit non-overlapping regions.
    Returns merged content or None if merge failed.
    """
    if not base or not ours or not theirs:
        return None
    if ours == theirs:
        return ours
    if base == ours:
        return theirs
    if base == theirs:
        return ours
    return None
