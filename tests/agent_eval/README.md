# Stage 12 — Software-Agent Benchmark Harness

Repeatable benchmark for the AutoStudio software assistant: **12 tasks** (`core12`) split across

1. **Layer 1** — six checked-in **mini-repo** fixtures under `fixtures/mini_repos/`.
2. **Layer 2** — six tasks over **three pinned upstream snapshots** under `fixtures/pinned_repos/`
   (`click_snapshot`, `typer_snapshot`, `requests_snapshot`). Each snapshot includes
   `SNAPSHOT_SOURCE.json` (repository URL + full commit hash). No network access is required
   at benchmark runtime.

### Execution modes

| Mode | CLI | Behavior |
|------|-----|----------|
| **mocked** (default) | *(default)* | Patches `execution_loop` to a no-op success (fast CI signal). |
| **real** | `--real` or `--execution-mode real` | Runs the real `execution_loop` on a **fixed 6-task audit subset** (see below), with benchmark plan injection + **offline LLM stubs** (no HTTP). |

The default harness invokes `run_hierarchical` with the **mocked `execution_loop`** strategy as
`tests/evals/agent_eval_harness.py` (deterministic, offline). After optional `setup_commands`,
each workspace is **indexed** with `repo_index.indexer.index_repo` into `workspace/.symbol_graph/`
(same pattern as `tests/test_agent_e2e.py`), so retrieval, repo map, and graph stages see real
symbols for the fixture tree. By default `INDEX_EMBEDDINGS` defaults to `0` on first indexer load
from this harness for speed (symbol + graph only). **Validation commands** run in a **copy** of
each fixture workspace so the checked-in trees are never mutated.

## Commands

```bash
# Pytest (schema + smoke + runner isolation)
python3 -m pytest tests/agent_eval -q

# Full suite artifact run — mocked mode, all 12 tasks (timestamped dir + `latest` symlink)
python3 -m tests.agent_eval.runner --suite core12 --output artifacts/agent_eval_runs/latest

# Stage 12.1 — real execution_loop, audit subset only (6 tasks), offline model stubs
python3 -m tests.agent_eval.runner --real --output artifacts/agent_eval_runs/latest
```

### Pre-index fixture trees (optional, Stage 13.0)

To build `.symbol_graph/` **inside** each checked-in `mini_repos/*` and `pinned_repos/*` tree (so
copies include a warm index) and emit a coverage JSON report:

```bash
python3 scripts/index_agent_eval_fixtures.py
# → artifacts/agent_eval/index_coverage/coverage_report.json
```

The benchmark still re-indexes each **copied** workspace in `run_single_task`; this script is for
local verification and optional warm caches.

### Stage 12.1 audit subset (`audit6`)

Exactly **six** tasks (same `TaskSpec` rows as `core12`, no new inventory):

| task_id | Layer |
|---------|-------|
| `core12_mini_repair_calc` | mini |
| `core12_mini_repair_parse` | mini |
| `core12_mini_feature_flags` | mini |
| `core12_pin_typer_repair` | pinned |
| `core12_pin_typer_feature` | pinned |
| `core12_pin_click_multifile` | pinned |

Real mode uses **compat** benchmark plans with **SEARCH → EDIT** steps for these tags (see `real_execution._compat_plan_dict_for_audit`).

Artifacts are written under `artifacts/agent_eval_runs/<timestamp>_<id>/` (gitignored):

- `summary.json`, `summary.md`
- `tasks/<task_id>/outcome.json` — canonical fields + `_audit` (structural success, grading mode, `failure_bucket`, `execution_mode`, index meta)
- `tasks/<task_id>/validation_logs.json`, `indexing.json`, `loop_output_snapshot.json`, `transcript.txt`, `patch.diff`, `changed_files.txt`, `task_summary_snippet.txt`
- `summary.json` includes `failure_bucket_histogram` and `execution_mode`

## Scoring

| Mode | `success` |
|------|-----------|
| `validation_exit_code` | All `validation_commands` exited 0 |
| `explain_artifact` | Expected artifact files exist and contain `explain_required_substrings` |
| `structural_loop` | (reserved) structural `parent_goal_met` / compat loop only |

Primary signal is **validation / rubric**. In **real** mode, `patch.diff` / `files_changed` / `diff_stat`
come from a **git snapshot** of the workspace after the run; `failure_bucket` classifies failed runs
(see `failure_buckets.py`). **Mocked** mode leaves most edit metrics empty.

### Known limitations (Stage 12.1)

- **`execution_loop` kwargs**: `run_deterministic` passes `max_runtime_seconds`; the stock `execution_loop` signature does not accept it. The harness wraps the real loop with `_execution_loop_drop_max_runtime` (benchmark-only shim).
- **Offline models**: `call_reasoning_model` / `call_small_model` are stubbed at multiple import sites so benchmark runs do not call the network; critic/retry responses are synthetic JSON.
- **Heuristics**: `unrelated_files_changed`, `bad_edit_patterns`, `retrieval_miss_signals`, and `failure_bucket` are best-effort and may mislabel edge cases.

## Pinned snapshots (provenance)

| Directory | Upstream | Commit (full) |
|-----------|----------|-----------------|
| `pinned_repos/click_snapshot` | pallets/click | see `SNAPSHOT_SOURCE.json` |
| `pinned_repos/typer_snapshot` | fastapi/typer | see `SNAPSHOT_SOURCE.json` |
| `pinned_repos/requests_snapshot` | psf/requests | see `SNAPSHOT_SOURCE.json` |

## Task inventory (core12)

| task_id | Layer | Category | Repo |
|---------|-------|----------|------|
| core12_mini_explain_arch | mini | explain / architecture | mr01_arch |
| core12_mini_trace_flow | mini | trace | mr02_trace |
| core12_mini_repair_calc | mini | failing-test repair | mr03_calc |
| core12_mini_repair_parse | mini | failing-test repair | mr04_parse |
| core12_mini_feature_flags | mini | small feature | mr05_flags |
| core12_mini_docs_version | mini | docs–code consistency | mr06_version |
| core12_pin_requests_explain_trace | pinned | explain + trace | requests_snapshot |
| core12_pin_click_docs_code | pinned | docs–code consistency | click_snapshot |
| core12_pin_typer_repair | pinned | failing-test repair | typer_snapshot |
| core12_pin_typer_feature | pinned | small feature | typer_snapshot |
| core12_pin_requests_httpbin_doc | pinned | docs–code consistency | requests_snapshot |
| core12_pin_click_multifile | pinned | multi-file consistency | click_snapshot |

## Follow-ups

- Wire **real editing pipeline** output into `patch.diff` / `files_changed` / `diff_stat`.
- Populate **retrieval_miss_signals** and **bad_edit_patterns** from trace and static analysis.
- Optional **stdlib-only** validation mode for environments without `typer` / `click` installed.
- Refresh pinned snapshots on a schedule (re-copy at new commits; update `SNAPSHOT_SOURCE.json`).
