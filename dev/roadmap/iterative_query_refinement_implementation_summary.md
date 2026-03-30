Implemented end-to-end per the attached plan (without editing the plan file), and completed all todos.

### What I changed

- Extended parser contract in `QueryIntentParser`:
  - `parse(instruction, previous_queries=None, failure_reason=None, ...)`
  - Accepts previous parser output + structured failure reason.
  - Enforces strict no-repeat filtering against `previous_queries`.
  - Keeps runtime output schema unchanged (`symbols`, `keywords`, `regex_patterns`, `intents` only).

- Added failure signal contract in `agent_v2/schemas/exploration.py`:
  - `no_results`, `low_relevance`, `too_broad`, `too_narrow`, `wrong_abstraction`, `ambiguous_intent`, `missing_symbol_signal`.

- Added retry-gated refinement in `ExplorationEngineV2`:
  - Retry trigger only at initial retrieval gate:
    - no candidates, or
    - top score below threshold.
  - Single retry cap (`EXPLORATION_MAX_QUERY_RETRIES`, default 1).
  - Refined query set replaces prior set for retry evaluation (no merge).
  - Added telemetry event with:
    - original/refined queries,
    - failure_reason,
    - improvement delta (candidate count + top score).

- Added prompt role-separation infrastructure:
  - `PromptTemplate` now supports `system_prompt` and `user_prompt_template`.
  - Loader supports new fields + legacy fallback.
  - Registry adds `render_prompt_parts(...)` with compiled render cache.

- Added message-native LLM call path:
  - `call_reasoning_model_messages(messages, ...)`.
  - Centralized fallback for non-role backends:
    - `[SYSTEM] ... \n\n---\n\n [USER] ...`
  - Existing string-based APIs remain compatible.

- Migrated exploration components to role-separated prompt usage:
  - `query_intent_parser`
  - `candidate_selector`
  - `understanding_analyzer`
  - via structured prompt parts + message call path (with legacy fallback).

- Replaced query intent prompts (default + qwen2.5-coder-7b):
  - `agent/prompt_versions/exploration.query_intent_parser/v1.yaml`
  - `agent/prompt_versions/exploration.query_intent_parser/models/qwen2.5-coder-7b/v1.yaml`
  - Includes iterative failure-aware instructions and strict JSON-only output constraints.

### Tests and validation

Ran focused tests:

- `AUTOSTUDIO_SKIP_IMPORT_CHECK=1 pytest -q tests/test_query_intent_parser.py tests/test_exploration_prompt_registry_equivalence.py tests/test_exploration_engine_v2_control_flow.py`
- Result: **26 passed**

Also checked lints on touched files: **no linter errors**.

### Workflow/bug tracking updates

- Added backlog row in `dev/tasks/backlog.md` covering the full scope you requested.
- Added roadmap file:
  - `dev/roadmap/iterative_query_refinement_prompt_role_separation.md`
- Added completed task entry in `dev/tasks/completed.md`.
- Logged bug via repo bug workflow:
  - `dev/bugs/backlog/BUG-012_exploration_query_intent_parser_lacked_failure_awa.md`
  - Updated `dev/bugs/bug_index.md`.
