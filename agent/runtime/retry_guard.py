"""
Retry strategy guard: determines whether to allow another retry based on failure_type and attempt.
"""


def should_retry_strategy(failure_type: str, attempt: int, max_attempts: int) -> bool:
    """
    Whether to allow another retry for this failure type.
    failure_type: retrieval_miss | bad_patch | test_failure | syntax_error | timeout | unknown
    attempt: 1-based attempt number.
    """
    if attempt >= max_attempts:
        return False
    if failure_type == "unknown":
        return False
    if failure_type in ("syntax_error", "timeout"):
        return attempt <= 1
    if failure_type in ("retrieval_miss", "bad_patch", "test_failure", "patch_rejected", "patch_failed", "no_changes"):
        return True
    # Default for unrecognized but known-ish types: allow (subject to max_attempts)
    return True
