# Regression Tests

When you fix a bug, add a test here to ensure it never returns.

Tests live in `tests/` and are named `test_bug_XXX.py`:

- `tests/test_bug_002.py` — BUG-002: Planner invalid step (normalize_actions, validate_plan)
- `tests/test_bug_006.py` — BUG-006: symbol_graph get_symbol_dependencies stub

Run: `python -m pytest tests/test_bug_002.py tests/test_bug_006.py -v`
