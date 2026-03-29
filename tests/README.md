# Tests (`tests/`)

Unit and integration tests for routing, retrieval, editing, **`agent_v2`** runtime phases, and observability.

## Primary runtime adapter

**`tests/utils/runtime_adapter.py`** defines:

- `run_agent(instruction, mode)` → `create_runtime().run(...)` and returns `state`
- `run_controller` / `run_hierarchical` / `run_deterministic` — set `SERENA_PROJECT_DIR`, call **`agent_v2`** runtime, shape legacy dict output

This is the **compatibility layer** for tests and scripts that historically imported a monolithic `run_controller`. It is **not** `agent.orchestrator`.

## Notable suites

| Test | Focus |
|------|--------|
| `test_mode_manager.py` | `ModeManager` never calls `AgentLoop.run` for act/plan/deep_plan |
| `test_agent_v2_phases_live.py` | Live LLM phases for v2 |
| `test_agent_v2_loop_retry.py` | `AgentLoop` retry/stop behavior |
| `integration/` | E2E wiring — see `integration/README.md` |
| `evals/` | Benchmark harnesses |
| `retrieval/` | Retrieval pipeline eval helpers + optional multi-repo **pattern coverage** (see below) |

## Retrieval evaluation (`tests/retrieval/`)

These modules support **`scripts/eval_retrieval_pipeline.py`** and pytest checks around **`run_retrieval_pipeline`** (mid-pipeline retrieval only — not full exploration).

| File | Role |
|------|------|
| `case_generation.py` | Builds `RetrievalEvalCase` rows from real `agent_v2/` symbols and from cloned exploration repos (`build_default_local_cases`, `build_multi_repo_eval_cases`, …). |
| `pattern_sources.json` | **Config-driven manifest**: tiny GitHub repos (`EXPLORATION_TEST_REPOS` names) + one row per **pattern bucket** (concurrency, class lookup, DB, utilities, entrypoints, vague queries, constants, cross-reference, etc.). Paths and symbols are real; updates here are the intended way to extend coverage. |
| `pattern_coverage.py` | Loads the manifest, clones repos via `agent_v2.exploration_test_repos`, sets env for multi-root retrieval (`AGENT_V2_EXPLORATION_TEST_REPOS_JSON`, `RETRIEVAL_EXTRA_PROJECT_ROOTS`), builds cases, optional `index_repo` for `.symbol_graph`. |
| `test_retrieval_pipeline_behavior.py` | Default local / multi-repo harness behavior. |
| `test_pattern_coverage_retrieval.py` | **Optional** pytest: pattern dimensions (off unless enabled). |
| `test_retrieval_external_repos.py` | External repo wiring checks. |

### Pattern coverage pytest (optional)

First-time use needs network to clone; indexing writes under `artifacts/exploration_test_repos/`. Pre-index: `python3 scripts/index_pattern_coverage_repos.py`.

```bash
# Fast check: manifest buckets vs built cases (no engine)
RUN_PATTERN_COVERAGE=1 pytest tests/retrieval/test_pattern_coverage_retrieval.py::test_pattern_coverage_category_tags -v

# Heavier smoke: runs retrieval for every manifest case (rerank off by default in fixture)
RUN_PATTERN_COVERAGE=1 pytest tests/retrieval/test_pattern_coverage_retrieval.py -v
```

- **`test_pattern_coverage_category_tags`** asserts every `required_pattern_buckets` entry in `pattern_sources.json` appears in at least one case — keeps the taxonomy complete.
- **`test_pattern_coverage_pipeline_runs_and_reports`** asserts shape only (top-N paths/scores/slots); **misses outside top-k are not test failures** — they are signals for improving retrieval/rerank.

Marked with **`@pytest.mark.retrieval`** (see root `pyproject.toml` / markers).

### CLI eval (human-readable)

```bash
python3 scripts/eval_retrieval_pipeline.py --patterns --no-rerank --fail-threshold 99
```

`--patterns` reads `pattern_sources.json`. Use `--fail-threshold` when you want a non-zero exit only after many misses. Omit `--no-rerank` for cross-encoder rerank (slower).

## Fixtures

`tests/fixtures/`, `tests/agent_eval/fixtures/` — mini repos and pinned snapshots for retrieval and evals.

### Exploration dynamic eval fixtures

`tests/fixtures/exploration_dynamic_eval_cases.py` contains repo-grounded exploration eval inputs (real `agent_v2/` symbols and files).

- `EXPLORATION_EVAL_CASES`: raw dict fixture format
- `build_dynamic_eval_cases()`: converts dict fixtures into `EvalCase` objects for `run_eval_suite(...)`
- `build_dynamic_eval_suites()`: groups converted `EvalCase` objects by focus area

Example usage:

```python
from tests.fixtures.exploration_dynamic_eval_cases import build_dynamic_eval_cases
from agent_v2.exploration.exploration_behavior_eval_harness import run_eval_suite

cases = build_dynamic_eval_cases()
result = run_eval_suite(cases[:1])
```
