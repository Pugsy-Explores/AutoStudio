# Stage 12 — Implementation Plan

**Audience:** Principal engineer / engineer implementing the slice  
**Date:** 2026-03-20  
**Branch context:** `next/stage3-from-stage2-v1`  
**Precondition:** `Docs/STAGE12_DECISION_MEMO.md` approved  
**Status:** Implementation plan only. **No code has been changed yet.**

---

## 1. Goal

The goal of Stage 12 is to **measure the current agent** on **realistic software-assistant tasks** **before** any further orchestration, retrieval-merge, clarification, or decomposition work.

This slice is **benchmark-first**: establish a **repeatable signal** (tasks → run → artifacts → score summary) so subsequent changes are **evidence-driven**, not speculative.

---

## 2. Scope

Stage 12 **builds**:

| Deliverable | Purpose |
|-------------|---------|
| **A small fixed benchmark corpus** | Versioned tasks with stable instructions and expected evaluation hooks |
| **A repeatable pytest entrypoint** | CI- and local-friendly runs with deterministic structure |
| **Per-run artifact capture** | Auditable `loop_output` and run metadata for every task |
| **A score summary** | Pass/fail and aggregate metrics for regression and release review |

Stage 12 **does not**:

- Modify **parent-policy** semantics (RETRY / REPLAN / STOP / etc.) beyond what is **strictly necessary** to invoke the harness (see Definition of done).
- Change **compat** output shape or **compat delegation** behavior.
- Change **phase decomposition** (including **no** 3+ phases, **no** widening `_is_two_phase_docs_code_intent`).

---

## 3. Benchmark shape

### First slice: 12 tasks

The benchmark is a **general-purpose software assistant** slice: tasks should resemble real repo work (read, navigate, edit, test) but stay **small** and **bounded**.

| # | Category | Description (illustrative — exact wording lives in corpus) |
|---|----------|--------------------------------------------------------------|
| 1–2 | **Explain architecture from docs + code** | Answer a structured question using both docs and code (e.g. module boundaries, data flow). |
| 3–4 | **Trace a flow through repo files** | Follow a call path or event path across a few files; output a short trace or file list + rationale. |
| 5–6 | **Small bug fix** | Fix a minimal, localized defect in fixture code. |
| 7–8 | **Small feature addition** | Add a small, well-scoped behavior in fixture code. |
| 9–10 | **Add or repair tests** | Add a missing test or fix a broken test in fixture code. |
| 11–12 | **Multi-file consistency edit** | Change an API or constant across multiple files consistently. |

### Fixture strategy

- Benchmark runs should target **fixture repos or fixture mini-projects** checked into **`tests/evals/fixtures/`** (or a subdirectory such as `tests/evals/fixtures/mini_projects/`).
- Tasks reference **paths inside those fixtures** so runs do not depend on the full AutoStudio tree unless a task explicitly requires it (default: **isolate in fixtures** for stability and speed).

---

## 4. Required files (likely layout)

| Path | Role |
|------|------|
| `tests/evals/test_software_agent_benchmark.py` | Pytest entrypoints: schema tests, loader, smoke run, aggregation, compat + two-phase cases |
| `tests/evals/fixtures/` | Mini-projects, golden inputs, optional expected rubrics |
| `tests/evals/benchmark_cases.py` | Case definitions (dataclasses / dicts), loader, constants for task IDs |
| `scripts/run_agent_eval.py` | Optional CLI wrapper around pytest or a thin runner for batch/CI |
| `Docs/STAGE12_BENCHMARK_AUDIT.md` | **Written at closeout** — scorecard template, how to run, metric definitions, first baseline run notes (**not** authored as part of “plan approval”) |

Exact module names may vary; the **responsibilities** above are fixed.

---

## 5. Test-first work

Implement **tests before** (or in tight lockstep with) harness logic. **Minimum** test set:

| Test (conceptual name) | Requirement |
|-------------------------|-------------|
| **Benchmark case schema validation** | Every case must validate against a defined schema (required fields: task id, instruction, fixture root, path mode compat vs hierarchical, evaluation hook type). |
| **Task loader test** | Loader returns N cases, no duplicate IDs, all paths resolve under fixtures. |
| **Artifact directory creation test** | Per-run output directory is created, writable, and isolated (e.g. tmp path or named run id). |
| **One smoke benchmark run test** | Single task or minimal subset runs end-to-end with **stubbed or real** agent invocation per project convention — must prove wiring works. |
| **Score aggregation test** | Given mocked run results, aggregation produces expected totals and averages. |
| **Compat-path benchmark case test** | At least one case exercises **compat** (`compatibility_mode=True` / exact delegation path as defined in code). |
| **Two-phase benchmark case test** | At least one case exercises **non-compat hierarchical** two-phase path (`len(phases)==2` domain for that task). |

**Order recommendation:** schema → loader → artifact dir → aggregation (with mocks) → compat case → two-phase case → smoke run.

---

## 6. Artifact capture

Each benchmark run must write **per-task** artifacts including **at minimum**:

| Field / artifact | Notes |
|------------------|--------|
| **task id** | Stable string from corpus |
| **instruction** | Full prompt/instruction text |
| **path used** | Whether **compat** or **hierarchical** (two-phase) was used |
| **final `loop_output` snapshot** | JSON-serializable capture of final structured output (or explicit subset if size-capped — document in audit doc) |
| **success / failure** | Boolean or enum aligned with harness |
| **`attempts_total`** | From trace / aggregates as available |
| **`retries_used`** | From trace / aggregates as available |
| **`phase_results`** | If present on output |
| **exception text** | If run raised |
| **timestamps / duration** | Start/end or duration per task |

Storage format (JSONL, one file per task, directory per run) is **implementation detail**; **content** above is required.

---

## 7. Metrics to report

The **score summary** (and closeout `Docs/STAGE12_BENCHMARK_AUDIT.md`) must include:

| Metric | Description |
|--------|-------------|
| **total tasks** | Should be **12** for first slice |
| **pass count** | |
| **fail count** | |
| **compat vs hierarchical counts** | Tasks run per path |
| **average `attempts_total`** | Over tasks where defined |
| **average `retries_used`** | Over tasks where defined |
| **`failure_class` histogram** | Coarse categories (e.g. timeout, assertion, tool error, goal mismatch) — **taxonomy defined in harness** |
| **tasks requiring replan** | Count / list from `loop_output` / traces |
| **Obvious retrieval-miss notes** | **Manual annotation allowed in Stage 12** — short free-text per task when the failure looks like bad context |

---

## 8. Definition of done

Stage 12 is **done** when:

1. **Benchmark harness exists** (pytest + supporting modules under `tests/evals/`).
2. **All 12 tasks** run via pytest (or documented equivalent entrypoint).
3. **Artifacts** are written per task per run as specified in §6.
4. **Summary metrics** are generated (§7) for at least one full run.
5. **No production orchestration semantics** are changed **except** a **minimal** hook or import path adjustment **solely** to make the agent **callable** from tests (must be reviewable in isolation).

---

## 9. Explicit non-goals

| Non-goal | Notes |
|----------|--------|
| **Retrieval handoff merge** | Deferred — see `Docs/RETRIEVAL_HANDOFF_MERGE_DESIGN_QUESTIONS.md` |
| **Clarification** (`REQUEST_CLARIFICATION`) | Deferred — see `Docs/REQUEST_CLARIFICATION_MEASUREMENT_CRITERIA.md` |
| **New parent-policy outcomes** | No new enums/branches for parent stop reasons |
| **3+ phases** | `len(phases)!=2` guard remains |
| **New top-level `loop_output` keys** (compat-visible hierarchical) | `hierarchical_test_locks.py` contract unchanged |
| **Widening `_is_two_phase_docs_code_intent`** | No broadening of two-phase detection in Stage 12 |

---

## 10. Rollback

Rollback of Stage 12 work is:

- **Delete** the eval harness files, scripts, and **closeout** documentation under the agreed paths (`tests/evals/**`, `scripts/run_agent_eval.py`, `Docs/STAGE12_BENCHMARK_AUDIT.md`), **unless**
- A **minimal callable-hook** was added to production code: then rollback is **revert that commit** (or restore previous behavior) **plus** deleting eval-only files.

No database migrations or persistent infra are assumed for this slice.

---

*End of Stage 12 implementation plan.*
