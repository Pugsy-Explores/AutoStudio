# Iterative Query Refinement + Prompt Role Separation

## Goal

Improve exploration recall after failed retrieval by feeding structured failure signals and prior query output back into `QueryIntentParser`, while migrating exploration prompts to explicit system/user roles without changing exploration control flow.

## Scope

- Keep exploration stage order and loop behavior unchanged.
- Add data-flow retry inputs only (`previous_queries`, `failure_reason`).
- Keep parser runtime JSON schema stable: `symbols`, `keywords`, `regex_patterns`, `intents`.

## Implementation Items

1. Add `FailureReason` contract (including `ambiguous_intent`, `missing_symbol_signal`).
2. Extend `QueryIntentParser.parse(...)` with optional retry context.
3. Add single retry at initial retrieval gate only:
   - trigger on no candidates OR low top-k relevance
   - one retry max per exploration cycle
   - retry query set replaces previous query set (no merge)
4. Enforce no-repeat behavior versus `previous_queries`.
5. Refactor prompt infra to support:
   - `system_prompt`
   - `user_prompt_template`
   - compiled prompt cache
6. Add message-native reasoning calls and strict fallback format:
   - `[SYSTEM] ... --- ... [USER] ...`
7. Migrate query parser/analyzer/selector to role-separated prompt usage.
8. Add telemetry for refinement:
   - original/refined queries
   - failure reason
   - improvement delta

## Validation

- Focused tests:
  - `tests/test_query_intent_parser.py`
  - `tests/test_exploration_prompt_registry_equivalence.py`
  - `tests/test_exploration_engine_v2_control_flow.py`
- Expect no downstream parsing regressions and no planner/pipeline flow changes.
