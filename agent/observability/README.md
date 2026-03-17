# Observability Subsystem (`agent/observability/`)

Tracing and metrics for agent execution. This package provides the structured trace logger and helpers used across orchestration, execution, retrieval, and editing.

## Responsibilities

- **Trace lifecycle**: start/finish traces, stage timing, and event logging.
- **Structured events**: record decisions, tool inputs/outputs, errors, and summaries.
- **Reporting**: generate human-readable summaries to support debugging and evaluation.

## Public API

Exports from `agent/observability/__init__.py`:

- Trace: `start_trace`, `finish_trace`, `log_event`, `log_stage`, `trace_stage`
- Summaries: `summarize`
- Constants: `STAGES`

## Invariants

- Every non-trivial agent action must emit trace events (decision, reason, inputs, outputs, result).
- Trace format should be stable enough for replay tooling (`scripts/replay_trace.py`) and evaluation harnesses.

