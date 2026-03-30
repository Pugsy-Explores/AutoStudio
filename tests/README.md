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
| `evals/` | Benchmark harnesses — see [Exploration LLM eval harness](#exploration-llm-eval-harness-testsevals) |
| `retrieval/` | Retrieval pipeline eval helpers + optional multi-repo **pattern coverage** (see below) |

## Exploration LLM eval harness (`tests/evals/`)

These suites call the **real** reasoning models (not mocks). Each module is **opt-in** via its own environment variable; without it, pytest **skips** the suite (so CI stays fast and credential-free).

Run from the **repository root** with API credentials configured. Use **`-s`** to print coverage, category metrics, trace lines, and warning diagnostics to the terminal; omit **`-s`** if you only care about pass/fail.

| Module | Live gate (`export …=1`) | Pytest marker | Test file |
|--------|-------------------------|----------------|-----------|
| Query intent parser | `QUERY_INTENT_PARSER_EVAL_LIVE` | `query_intent_parser_eval` | `tests/evals/test_query_intent_parser_eval.py` |
| Exploration scoper | `SCOPER_EVAL_LIVE` | `scoper_eval` | `tests/evals/test_scoper_eval.py` |
| Selector batch | `SELECTOR_BATCH_EVAL_LIVE` | `selector_batch_eval` | `tests/evals/test_selector_batch_eval.py` |
| Understanding analyzer | `ANALYZER_EVAL_LIVE` | `analyzer_eval` | `tests/evals/test_analyzer_eval.py` |

### Recording eval output to log files

From the **repository root**, with the venv activated, you can tee each suite’s stdout (including `-s` workflow lines) into a timestamped directory under `.eval_logs/`:

```bash
cd /path/to/AutoStudio
source .venv/bin/activate
LOGDIR=".eval_logs/$(date +%Y%m%d_%H%M%S)"
mkdir -p "$LOGDIR"

export ANALYZER_EVAL_LIVE=1 && pytest tests/evals/test_analyzer_eval.py -v -s -m analyzer_eval 2>&1 | tee "$LOGDIR/analyzer_eval.log"
export SELECTOR_BATCH_EVAL_LIVE=1 && pytest tests/evals/test_selector_batch_eval.py -v -s -m selector_batch_eval 2>&1 | tee "$LOGDIR/selector_batch_eval.log"
export SCOPER_EVAL_LIVE=1 && pytest tests/evals/test_scoper_eval.py -v -s -m scoper_eval 2>&1 | tee "$LOGDIR/scoper_eval.log"
export QUERY_INTENT_PARSER_EVAL_LIVE=1 && pytest tests/evals/test_query_intent_parser_eval.py -v -s -m query_intent_parser_eval 2>&1 | tee "$LOGDIR/query_intent_parser_eval.log"
```

Logs are written to `$LOGDIR` (for example `.eval_logs/20260330_182916/`). Adjust `cd` to your clone path if needed.

**Per module (typical):**

```bash
export QUERY_INTENT_PARSER_EVAL_LIVE=1
python3 -m pytest tests/evals/test_query_intent_parser_eval.py -v -s -m query_intent_parser_eval
```

```bash
export SCOPER_EVAL_LIVE=1
python3 -m pytest tests/evals/test_scoper_eval.py -v -s -m scoper_eval
```

```bash
export SELECTOR_BATCH_EVAL_LIVE=1
python3 -m pytest tests/evals/test_selector_batch_eval.py -v -s -m selector_batch_eval
```

```bash
export ANALYZER_EVAL_LIVE=1
python3 -m pytest tests/evals/test_analyzer_eval.py -v -s -m analyzer_eval
```

**All exploration evals in one run** (every gate must be set, or that suite is skipped):

```bash
export QUERY_INTENT_PARSER_EVAL_LIVE=1 SCOPER_EVAL_LIVE=1 SELECTOR_BATCH_EVAL_LIVE=1 ANALYZER_EVAL_LIVE=1
python3 -m pytest tests/evals/ -v -s -m "query_intent_parser_eval or scoper_eval or selector_batch_eval or analyzer_eval"
```

Case YAML lives under `tests/evals/<module>/` (tier files and `edge_cases.yaml`). Marker definitions are in the root `pyproject.toml`.

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
