"""AST-aware semantic overlap detection for patches."""


def detect_semantic_overlaps(changes: list[dict]) -> list[dict]:
    """
    Detect when patches target overlapping AST regions.
    changes: [{file, symbol, action, patch}, ...]
    Returns list of {type: "semantic_overlap", file, symbol, change_indices}.
    """
    overlaps: list[dict] = []

    for i, a in enumerate(changes):
        file_a = a.get("file", "")
        sym_a = a.get("symbol", "")
        if not file_a:
            continue
        for j, b in enumerate(changes):
            if i >= j:
                continue
            file_b = b.get("file", "")
            sym_b = b.get("symbol", "")
            if file_b != file_a:
                continue
            if _symbols_overlap(sym_a, sym_b):
                overlaps.append({
                    "type": "semantic_overlap",
                    "file": file_a,
                    "symbol": f"{sym_a} / {sym_b}",
                    "change_indices": [i, j],
                })

    return overlaps


def _symbols_overlap(a: str, b: str) -> bool:
    """
    True if two symbols refer to overlapping AST regions.
    E.g. "Foo" and "Foo.bar" overlap (bar is inside Foo).
    """
    if not a or not b:
        return False
    if a == b:
        return True
    if a.startswith(b + "."):
        return True
    if b.startswith(a + "."):
        return True
    return False
