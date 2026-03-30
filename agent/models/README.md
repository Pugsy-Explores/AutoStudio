# Models Subsystem (`agent/models/`)

Model routing and client layer. All LLM calls should flow through this package to keep configuration, observability, and safety consistent.

## Responsibilities

- **Model clients**: perform HTTP calls to configured endpoints with consistent timeouts/params.
- **Task routing**: map tasks (routing/planning/reasoning/reranking-fallback) to model choices.
- **Configuration surface**: expose resolved endpoints and model names for runtime wiring.

## Public API

Exports from `agent/models/__init__.py`:

- Clients: `call_small_model`, `call_reasoning_model`
- Routing: `get_model_for_task`, `route_task`
- Types: `ModelType`
- Resolved config: `SMALL_MODEL_ENDPOINT`, `REASONING_MODEL_ENDPOINT`, `SMALL_MODEL_NAME`, `REASONING_MODEL_NAME`

## Invariants

- **No direct LLM calls in business logic**: orchestrator, planner, retrieval rankers, etc. must use this layer (or the configured model router) rather than calling vendor SDKs directly.
- Keep request parameters deterministic for evaluation where possible.

