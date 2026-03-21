"""
Retrieval result contract: normalized result shape and type constants.
Used by step_dispatcher and retrieval pipeline for consistent result handling.
"""

RETRIEVAL_RESULT_TYPE_SYMBOL_BODY = "symbol_body"
RETRIEVAL_RESULT_TYPE_REGION_BODY = "region_body"
RETRIEVAL_RESULT_TYPE_FILE_HEADER = "file_header"


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
    # Optional typed metadata (propagate through normalize / SEARCH fallbacks)
    if item.get("candidate_kind"):
        out["candidate_kind"] = str(item["candidate_kind"])
    if "line_range" in item and item["line_range"] is not None:
        out["line_range"] = item["line_range"]
    if item.get("source"):
        out["source"] = item["source"]
    if "localization_score" in item:
        out["localization_score"] = item["localization_score"]
    if item.get("relations"):
        out["relations"] = item["relations"]
    if item.get("enclosing_class"):
        out["enclosing_class"] = str(item["enclosing_class"])
    return out
