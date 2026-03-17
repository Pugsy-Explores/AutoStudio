# Prompt Observability (`agent/prompt_system/observability/`)

Prompt usage instrumentation: lightweight metrics and reporting to understand prompt cost/quality trade-offs.

## Responsibilities

- Capture **prompt usage metrics** (tokens, latency, success/failure signals).
- Generate summary reports for prompt iteration and regression debugging.

## Public API

Exports from `agent/prompt_system/observability/__init__.py`:

- `PromptUsageMetric`
- `generate_report`

