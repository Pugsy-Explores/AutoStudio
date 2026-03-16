"""Tool latency budgets (seconds). Enforce per-tool timeout limits."""

TOOL_BUDGETS = {
    "SEARCH_CANDIDATES": 1.0,
    "BUILD_CONTEXT": 5.0,
    "EDIT": 10.0,
    "EXPLAIN": 5.0,
    "SEARCH": 5.0,
    "INFRA": 10.0,
}
