"""
Retrieval result contract: normalized result shape and type constants.
Used by step_dispatcher and retrieval pipeline for consistent result handling.
"""

RETRIEVAL_RESULT_TYPE_SYMBOL_BODY = "symbol_body"


def normalize_result(item: dict, *, source_hint: str | None = None) -> dict:
    """
    Normalize a retrieval result to the standard shape.
    Ensures file, symbol, line, snippet keys; adds source_hint if provided.
    """
    out = {
        "file": str(item.get("file") or item.get("path") or ""),
        "symbol": str(item.get("symbol") or ""),
        "line": int(item.get("line") or 0),
        "snippet": str(item.get("snippet") or ""),
    }
    if source_hint:
        out["source_hint"] = source_hint
    # Preserve retrieval_result_type if present (for impl-body filtering)
    if "retrieval_result_type" in item:
        out["retrieval_result_type"] = item["retrieval_result_type"]
    if "implementation_body_present" in item:
        out["implementation_body_present"] = item["implementation_body_present"]
    return out
