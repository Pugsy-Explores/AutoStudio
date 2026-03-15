# Prompt Templates (Legacy + Compatibility)

**Phase 13:** Prompts are now managed by the **Prompt Infrastructure** in `agent/prompt_system/`. This package provides a compatibility shim.

## Current Architecture

- **Versioned prompts**: `agent/prompt_versions/{name}/v1.yaml` — canonical source
- **PromptRegistry**: `agent.prompt_system.get_registry()` — primary API
- **Legacy YAML**: `agent/prompts/*.yaml` — kept for fallback during migration

## Compatibility Shim

`get_prompt(name, key)` in this package redirects to the registry:

```python
from agent.prompts import get_prompt

# Legacy usage (still works)
system = get_prompt("planner_system", "system_prompt")
ctx = get_prompt("query_rewrite_with_context")  # returns {"main": ..., "end": ...}
```

## Preferred Usage (Phase 13)

```python
from agent.prompt_system import get_registry

registry = get_registry()
instructions = registry.get_instructions("planner")
instructions = registry.get_instructions("router", variables={"task_description": "..."})
template = registry.get("query_rewrite_with_context")
main, end = template.extra["main"], template.extra["end"]

# Guardrails (Phase 13 Hardening): enforced at LLM call boundary in model_client
# Pre-call: injection check on user content (always)
# Post-call: constraint validation when prompt_name passed to call_small_model/call_reasoning_model
# For programmatic use: registry.get_guarded(...), registry.validate_response(...)
```

## Format String Rules

- **Placeholders**: Use `{name}` for substitution. Pass matching kwargs to `format()` or `variables`.
- **Literal braces**: Escape as `{{` and `}}` so they render as `{` and `}`.
- **User input**: Values passed to `format()` are inserted as-is; braces in values are safe.

## Structured Output (JSON) Best Practices

For prompts that require JSON-only output (e.g. query_rewrite_with_context):

1. **Schema-first**: Put the exact JSON schema at the top of the prompt.
2. **Role-based system message**: "You are a [API] role. Return ONLY valid JSON." Never ask for explanations alongside JSON.
3. **Few-shot examples**: 2–4 input→output examples covering different cases.
4. **Explicit output format**: End with "Return JSON only:" or similar.
5. **Avoid**: "No thinking, no explanation" — reasoning models often ignore this. Use positive framing.

## Versioned Prompts (Phase 13)

All prompts live in `agent/prompt_versions/{name}/v1.yaml`. Key registry names:

| Registry Name | Purpose |
|---------------|---------|
| planner | Planner system prompt |
| replanner | Replanner system prompt |
| replanner_user | Replanner user-turn template (variables: instruction, steps_json, failed_desc, error_msg) |
| critic | Critic system prompt |
| retry_planner | Retry planner system prompt |
| query_rewrite | Simple query rewrite |
| query_rewrite_with_context | Context-aware rewrite |
| query_rewrite_system | JSON-only system for rewrite |
| validate_step | Step validation |
| router | Model routing fallback |
| router_logit | Router logit |
| instruction_router | Instruction classification (CODE_SEARCH, CODE_EDIT, etc.) |
| explain_system | EXPLAIN context-gate system prompt |
| action_selector | Autonomous action selection (SEARCH, EDIT, EXPLAIN, INFRA) |
| context_ranker_single | Single-snippet relevance (variables: query, snippet) |
| context_ranker_batch | Batch snippet relevance (variables: query, snippets) |

## Legacy Files (Fallback)

| File | Registry Name |
|------|---------------|
| planner_system.yaml | planner |
| replanner_system.yaml | replanner |
| critic_system.yaml | critic |
| retry_planner_system.yaml | retry_planner |
| query_rewrite.yaml | query_rewrite |
| query_rewrite_with_context.yaml | query_rewrite_with_context |
| query_rewrite_system.yaml | query_rewrite_system |
| validate_step.yaml | validate_step |
| model_router.yaml | router |
| router_logit_system.yaml | router_logit |

**Full architecture**: [Docs/PROMPT_ARCHITECTURE.md](../../Docs/PROMPT_ARCHITECTURE.md) — purpose, pipeline position, design philosophy, safety risks, testing, guardrails, A/B testing.
