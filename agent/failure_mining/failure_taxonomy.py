"""Standard failure categories for trajectory-scoped failure mining."""

FAILURE_TYPES = [
    "retrieval_miss",
    "wrong_file_localization",
    "incorrect_patch",
    "syntax_error_patch",
    "test_failure",
    "tool_error",
    "timeout",
    "hallucinated_api",
    "premature_completion",
    "hallucinated_symbol",  # patch/reasoning references symbol not in repo graph
    "loop_failure",  # identical step repeated >= 3 times consecutively
]
