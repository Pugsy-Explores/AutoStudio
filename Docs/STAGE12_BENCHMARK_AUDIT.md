# Stage 12 — Benchmark audit (closeout)

**Date:** 2026-03-20  
**Branch:** `next/stage3-from-stage2-v1`  
**Closeout git SHA (reference):** `5c8efeb` (record at merge; may differ if history is rewritten)

---

## Shipped scope

Stage 12 delivered a **fixed 12-task benchmark corpus** over a checked-in mini-project fixture, a **pytest-driven harness** (`tests/evals/test_software_agent_benchmark.py`), a **runner** (`scripts/run_agent_eval.py`), and **per-run JSON artifacts** (`summary.json` + per-task files). The harness calls **`run_hierarchical`** with **`execution_loop` mocked** to deterministic successful steps so runs are **offline, deterministic, and network-free** (see `tests/evals/agent_eval_harness.py` docstring).

**No production orchestration semantics were changed** for this slice; no new `loop_output` keys; compat path is still asserted against `tests/hierarchical_test_locks.py` after compat runs.

---

## Files added / changed (Stage 12 harness)

| Path | Role |
|------|------|
| `.gitignore` | Ignore `artifacts/agent_eval/` |
| `scripts/run_agent_eval.py` | CLI: runs full benchmark, prints summary JSON |
| `tests/evals/__init__.py` | Package |
| `tests/evals/benchmark_cases.py` | Corpus + validation |
| `tests/evals/agent_eval_harness.py` | Run single/full benchmark, aggregate metrics, write artifacts |
| `tests/evals/test_software_agent_benchmark.py` | Pytest entry (schema, loader, compat/two-phase smoke, full 12-task run, aggregation) |
| `tests/evals/fixtures/mini_projects/sample_app/**` | Mini-project (docs + `src/` + tests) |
| `Docs/STAGE12_BENCHMARK_AUDIT.md` | This closeout |

---

## Benchmark corpus size and categories

**Size:** **12** tasks (`BENCHMARK_CASES` in `tests/evals/benchmark_cases.py`).

**Fixture root:** `tests/evals/fixtures/mini_projects/sample_app` (all tasks).

| Count | `category` (in corpus) | Path mode |
|------|-------------------------|-----------|
| 2 | `explain_architecture` | hierarchical (two-phase) |
| 2 | `trace_flow` | hierarchical |
| 2 | `bug_fix` | compat |
| 2 | `feature_addition` | compat |
| 2 | `add_or_repair_tests` | compat |
| 2 | `multi_file_consistency` | hierarchical |

**Path split:** **6** `compat` / **6** `hierarchical` (non-compat two-phase parent plan in harness).

---

## Artifact location

- **Default directory pattern:** `<repo>/artifacts/agent_eval/run_<timestamp>_<id>/` (from `default_artifact_root()` + `run_full_benchmark()` when `run_dir` is omitted).
- **Contents:** `tasks/<task_id>.json` (per-task capture) and `summary.json` (aggregate).
- **Override:** `python3 scripts/run_agent_eval.py --output-dir <path>` or `run_full_benchmark(run_dir=...)`.

The `artifacts/agent_eval/` tree is **gitignored**; regenerate locally.

---

## Pytest commands and exact pass counts

Recorded on closeout (same machine as harness implementation):

```bash
python3 -m pytest tests/evals/test_software_agent_benchmark.py -q
```

**Result:** **`12 passed`** (runtime ~0.26s).

Hierarchical / orchestration proof suite (unchanged contract tests):

```bash
python3 -m pytest tests/test_parent_plan_schema.py tests/test_run_hierarchical_compatibility.py tests/test_two_phase_execution.py -q
```

**Result:** **`203 passed`** (runtime ~3.76s).

---

## Summary metrics produced by the harness

The following is the **`summary.json`** payload shape produced by `run_full_benchmark` / `scripts/run_agent_eval.py`, **as observed** on a full run:

```json
{
  "total_tasks": 12,
  "pass_count": 12,
  "fail_count": 0,
  "compat_tasks": 6,
  "hierarchical_tasks": 6,
  "average_attempts_total": 2.0,
  "average_retries_used": 0.0,
  "failure_class_histogram": {},
  "tasks_requiring_replan": [],
  "per_task_retrieval_notes": {
    "s12_explain_arch_01": null,
    "s12_explain_arch_02": null,
    "s12_trace_flow_01": null,
    "s12_trace_flow_02": null,
    "s12_bugfix_01": null,
    "s12_bugfix_02": null,
    "s12_feature_01": null,
    "s12_feature_02": null,
    "s12_tests_01": null,
    "s12_tests_02": null,
    "s12_multifile_01": null,
    "s12_multifile_02": null
  }
}
```

**Fields:** `total_tasks`, `pass_count`, `fail_count`, `compat_tasks`, `hierarchical_tasks`, `average_attempts_total`, `average_retries_used`, `failure_class_histogram`, `tasks_requiring_replan`, `per_task_retrieval_notes` (manual annotation hook; all **null** in this run).

---

## Notable findings (blunt)

1. **Compat vs hierarchical on this fixture set**  
   **Both paths reported structural success (`pass_count` 12, `fail_count` 0).** The harness does **not** rank “which orchestration mode is better” for real software outcomes: **`execution_loop` is mocked**, so this is a **wiring / contract** check, not an A/B on retrieval quality, edit quality, or planner decisions.

2. **Retries / replans**  
   **`average_retries_used` was `0.0`.** **`tasks_requiring_replan` was `[]`.** With the current mock, retries and replans are **not** exercised in a way that reflects production stress. **`average_attempts_total` was `2.0`** — consistent with aggregated reporting on the hierarchical path, **not** evidence of user-visible retry storms.

3. **Obvious retrieval misses**  
   **`per_task_retrieval_notes` were all `null`.** Nothing was manually annotated, and the mocked loop **does not** surface retrieval misses from a real indexer/LLM.

4. **Where the bottleneck looks (given this run)**  
   **This run does not localize the product bottleneck.** It **cannot** honestly point to retrieval vs edit quality vs planner policy, because **the execution path that would fail on bad retrieval, bad edits, or bad policy is not run for real**.

**If forced to interpret intent:** Stage 12 succeeded at **measurement infrastructure**; it did **not** yet measure **Mode 1 execution quality** on the corpus instructions. The next bottleneck is **not proven here** — it is **whatever breaks first when `execution_loop` is real** (or partially real).

---

## Explicit non-goals preserved (Stage 12)

- No retrieval handoff merge implementation  
- No `REQUEST_CLARIFICATION` implementation  
- No new parent-policy outcomes  
- No 3+ phases / no widening `_is_two_phase_docs_code_intent`  
- No new compat-visible top-level hierarchical `loop_output` keys (`hierarchical_test_locks.py` unchanged)

---

## Recommended Stage 13 (hard)

**Hard recommendation:** **Edit / verification loop hardening** — extend evaluation so **at least a subset** of the corpus runs with **non-mocked** `execution_loop` (or a controlled stub that still exercises retrieval + edits + goal evaluation), and add **verification** (e.g. pytest on fixture mini-projects, diff checks) **before** treating retrieval merge or clarification as funded.

**Why not the others as the primary Stage 13 slice (based on this audit only):**

| Option | Why defer as *primary* |
|--------|-------------------------|
| **Retrieval handoff merge** | No retrieval-failure signal in this run; merge is still **design-heavy** (`Docs/RETRIEVAL_HANDOFF_MERGE_DESIGN_QUESTIONS.md`). Do it when **two-phase context loss** is evidenced under **real** execution. |
| **Clarification** | Gates in `Docs/REQUEST_CLARIFICATION_MEASUREMENT_CRITERIA.md`; **no** clarification stress in this harness. |
| **Code search improvements** | Same: **no** measured search failure mode from mocked runs. |

**Secondary (parallelizable after Stage 13 has real execution signal):** retrieval handoff merge **or** code search improvements, depending on where failures cluster.

---

*End of Stage 12 benchmark audit.*
