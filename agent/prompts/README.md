# Prompt Templates

YAML prompts in this package are loaded and formatted with Python's `str.format()`.

For full architecture: [Docs/PROMPT_ARCHITECTURE.md](../../Docs/PROMPT_ARCHITECTURE.md) — purpose, pipeline position, design philosophy, safety risks, testing.

## Format string rules

- **Placeholders**: Use `{name}` for substitution. Pass matching kwargs to `format()`.
- **Literal braces**: Escape as `{{` and `}}` so they render as `{` and `}`.
  - Example: `Start with {{ and end with }}` → "Start with { and end with }"
- **User input**: Values passed to `format()` are inserted as-is; braces in values are safe.

## Structured output (JSON) best practices

For prompts that require JSON-only output (e.g. query_rewrite_with_context):

1. **Schema-first**: Put the exact JSON schema at the top of the prompt.
2. **Role-based system message**: "You are a [API] role. Return ONLY valid JSON." Never ask for explanations alongside JSON.
3. **Few-shot examples**: 2–4 input→output examples covering different cases.
4. **Explicit output format**: End with "Return JSON only:" or similar.
5. **Avoid**: "No thinking, no explanation" — reasoning models often ignore this. Use positive framing.

## Files

- `query_rewrite.yaml` — `{text}`
- `query_rewrite_with_context.yaml` — `{user_request}`, `{previous_attempts}`, `{planner_step}` (main); `end` is appended, not formatted. Output schema: `{tool, query, reason}`; optional `queries` (array) for variants; includes SEARCH STRATEGY RULES.
- `validate_step.yaml` — `{step}`, `{success}`, `{output_summary}`
- `model_router.yaml` — `{task_description}`
- `planner_system.yaml` — system prompt; includes MULTI-STEP EXAMPLES (Phase 5): bug fix, multi-file feature, refactoring few-shot examples.
- `replanner_system.yaml` — system prompt, not formatted
