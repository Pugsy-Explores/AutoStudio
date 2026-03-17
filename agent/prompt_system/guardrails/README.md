# Prompt Guardrails (`agent/prompt_system/guardrails/`)

Safety and correctness checks applied at prompt boundaries. These guards reduce the risk of prompt injection, invalid structured outputs, and unsafe actions.

## Responsibilities

- **Prompt injection checks**: detect and block malicious instructions.
- **Output schema validation**: ensure structured outputs match expected JSON/schema.
- **Constraint checking**: enforce system invariants (e.g., allowed actions only).
- **Safety policy**: centralized safety rules and enforcement hooks.

## Public API

Exports from `agent/prompt_system/guardrails/__init__.py`:

- `check_prompt_injection`, `PromptInjectionError`
- `validate_output_schema`
- `check_constraints`
- `SafetyPolicy`, `check_safety`

