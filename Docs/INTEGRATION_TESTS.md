# Integration Tests

Integration tests run the full agent pipeline against **real services** with no mocks. They verify end-to-end behavior: planner, retrieval, reranker, diff planner, and patch generation.

## Overview

| Aspect | Integration Tests | Unit / E2E Tests |
|--------|-------------------|-------------------|
| LLM calls | Real (reasoning model) | Mocked or optional |
| Retrieval | Real (graph, vector, BM25, grep) | Mocked |
| Reranker | Real (cross-encoder service) | Mocked |
| Diff planner | Real | Mocked |
| Goal | Pipeline works end-to-end | Deterministic output verification |

## Requirements

- **Reasoning model** API reachable (planner, query rewriter, context ranker, diff planner)
- **Reranker service** (when `RERANKER_ENABLED` and `candidate_count >= RERANK_MIN_CANDIDATES`)
- **Graph index** built for the test repo (indexed executor classes)

## Running Integration Tests

```bash
TEST_MODE=integration pytest tests/integration/ -v
```

Without `TEST_MODE=integration`, integration tests are **skipped**:

```bash
pytest tests/integration/ -v   # → skipped
```

## Test Structure

| File | Test | Instruction |
|------|------|-------------|
| `tests/integration/test_agent_e2e.py` | `test_agent_e2e_add_logging` | "Add logging to all executor classes" |

## Success Criteria

Tests assert:

1. **Agent completes** — no unhandled exceptions, `errors == []`
2. **Retrieval pipeline runs** — `retrieval_metrics` present in `state.context`
3. **At least one SEARCH step** — `search_steps >= 1`
4. **Patch stage** — when EDIT runs, patches or `files_modified` recorded
5. **Reranker** — when `candidate_count >= RERANK_MIN_CANDIDATES`, reranker executes (metrics logged in `rerank_latency_ms`, `rerank_skipped_reason`)

## Debug Output

When tests run, they print:

- `search_steps` — number of SEARCH steps completed
- `candidate_count` — candidates passed to reranker
- `rerank_latency_ms` — reranker latency
- `rerank_skipped_reason` — why reranker was skipped (if any)
- `retrieval_metrics` keys — available telemetry

## Plan Shape

The real planner may produce plans with `plan_id` and steps (Phase 4), e.g.:

- `SEARCH → EDIT`
- `SEARCH → SEARCH → EDIT`

Tests accept either plan shape. They do **not** assert exact LLM outputs (LLMs are non-deterministic).

## Environment

| Variable | Purpose |
|----------|---------|
| `TEST_MODE=integration` | Enables integration tests; without it they are skipped |
| `ENABLE_REAL_LLM` | Optional; integration tests assume real LLM by default |

## Unit Tests Unchanged

Unit tests in `tests/` remain mocked and run separately:

```bash
pytest tests/test_agent_controller.py -v   # unit tests, mocked
pytest tests/test_agent_e2e.py -v --mock    # E2E with mock
TEST_MODE=integration pytest tests/integration/ -v   # integration, real services
```
