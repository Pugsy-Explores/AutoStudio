# Prompt System (`agent/prompt_system/`)

Prompt infrastructure: templates, registry, versioning, context engineering, guardrails, observability, and retry strategies.

## Responsibilities

- **Prompt registry**: structured access to prompts by name/version.
- **Context engineering**: rank/prune/compress/summarize context within budgets.
- **Guardrails**: injection checks, output schema validation, constraint enforcement, safety policy.
- **Retry strategies**: standardized ways to alter prompts/models/context after failures.
- **Versioning & experiments**: store versions, diff, and A/B test prompts.

## Public API

Exports from `agent/prompt_system/__init__.py`:

- `PromptTemplate`
- `PromptRegistry`, `get_registry`

## Subpackages

- `context/`: token counting, budgeting, ranking, pruning, compression, summarization
- `guardrails/`: injection + schema + safety checks
- `observability/`: prompt usage metrics/logging
- `retry_strategies/`: bounded, deterministic retry approaches
- `versioning/`: prompt history and A/B testing

## Invariants

- Guardrails must be cheap enough to run frequently and strict enough to prevent unsafe execution paths.
- Context operations must preserve budgets deterministically (same inputs → same pruned output).

