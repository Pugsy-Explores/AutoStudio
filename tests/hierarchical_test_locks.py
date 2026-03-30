"""Shared contract locks for hierarchical vs compatibility `run_hierarchical` loop_output (Stage 3–5).

Compatibility mode must return the same dict object as `run_deterministic` with no extra keys.
Hierarchical mode adds the keys listed in `HIERARCHICAL_LOOP_OUTPUT_KEYS` at the top level of
`loop_output`, and may add `errors_encountered_merged` and `attempt_history` on each entry in
`phase_results` (never on the compat `loop_output` dict itself).
"""

from __future__ import annotations

# Keys that hierarchical `run_hierarchical` adds to loop_output (compat must not add any of these).
HIERARCHICAL_LOOP_OUTPUT_KEYS = frozenset(
    (
        "phase_validation",
        "parent_retry",
        "parent_plan_id",
        "phase_count",
        "parent_goal_met",
        "parent_goal_reason",
        "phase_results",
        "parent_retry_eligible",
        "parent_retry_reason",
        "max_parent_retries",
        "attempts_total",
        "retries_used",
    )
)

# Hierarchical-only field that appears on per-phase dicts; must not appear on compat loop_output.
_PHASE_RESULT_FIELD_NAMES = frozenset(("errors_encountered_merged", "attempt_history"))


def assert_compat_loop_output_has_no_hierarchical_keys(loop_output: dict) -> None:
    """Assert compatibility path returned `run_deterministic`'s dict with no hierarchical-only keys."""
    for k in HIERARCHICAL_LOOP_OUTPUT_KEYS:
        assert k not in loop_output
    for k in _PHASE_RESULT_FIELD_NAMES:
        assert k not in loop_output
