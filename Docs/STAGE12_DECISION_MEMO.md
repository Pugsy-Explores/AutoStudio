# Stage 12 — Architecture Decision Memo

**Audience:** Principal engineer / release owner  
**Type:** Principal Engineer Decision Memo  
**Date:** 2026-03-20  
**Branch context:** `next/stage3-from-stage2-v1`  
**Precondition:** Stage 11 closed; Stage 10 REPLAN shipped; Stage 11 docs-only measurement artifacts on disk (`Docs/STAGE11_DECISION_MEMO.md`, `Docs/REQUEST_CLARIFICATION_MEASUREMENT_CRITERIA.md`, `Docs/RETRIEVAL_HANDOFF_MERGE_DESIGN_QUESTIONS.md`, `Docs/REPLAN_*` as referenced).  
**Status:** Decision memo only. **No code change implied by this document.**

---

## Executive statement

**Stage 12 = software-agent benchmark + execution-quality audit — not more orchestration features.**

The hierarchical **control plane** (parent plans, phases, retries, REPLAN, handoff injection, traces) is **sufficient for the next validation step**. Further orchestration work without measuring **execution path** quality is **unfunded engineering**: you cannot prioritize retrieval merge, clarification, or 3+ phases credibly until you know **how well the agent actually executes** on representative tasks.

---

## Why Stage 12 is not orchestration

**Stages 1–10** delivered **orchestration control-plane capability**: parent plans, two-phase decomposition, compat delegation, retries, attempt history, REPLAN, per-phase budgets, traces, and contracts locked in tests.

**Stage 11** closed with **docs-only** gates: measurement criteria for future clarification, open questions for retrieval handoff merge, and the decision memo choosing **hold-and-measure** over new parent-policy or frozen-module work.

**Therefore:** the next bottleneck is **not** “another parent-policy enum” or “merge prior context” by default — it is **whether Mode 1 execution** (planner → `execution_loop` → tools → goal evaluation) **meets the bar** on **defined tasks**. That bottleneck lives in the **execution path** and must be **validated with a benchmark and audit harness**, not with additional orchestration branches.

---

## 1. Repo stage map (Stages 1–11)

| Repo stage | Status | What shipped (short) |
|------------|--------|----------------------|
| **1–5** | Complete | Parent plan schemas; compat delegation; phase loop; retries; `attempt_history` / aggregates |
| **6–8** | Complete | Subgoal connectors; per-phase retry config |
| **9** | Complete | Docs-only: REPLAN measurement + precoding decisions |
| **10** | Complete | **REPLAN** policy; `_build_replan_phase`; `phase_replanned` / `phase_replan_failed` |
| **11** | Complete | **Docs-only:** Stage 11 decision memo; clarification measurement criteria (Stage 12 **gate** for that feature); retrieval handoff **questions** (non-binding) |

Orchestration **feature** work pauses here for Stage 12 unless **benchmark evidence** explicitly justifies a narrow exception (see invariants below).

---

## 2. Roadmap vs repo warning

**Roadmap** documents (e.g. `HIERARCHICAL_PHASED_ORCHESTRATION_EXECUTION_ROADMAP.md`) describe parent policy, clarification, 3+ phases, and retrieval improvements as **product/roadmap** themes. **Repo stages** are **what merged in this branch** — they are **not** 1:1 with roadmap “Stage N” labels.

**Stage 12 (this memo) does *not* approve:**

- A new **orchestration** initiative (additional parent-policy outcomes, new decomposition modes, or contract expansion) **as the default slice**.
- Equating “roadmap Stage X” with “repo Stage 12” in scheduling or review.

**Stage 12** is explicitly **execution-quality measurement**, not an orchestration expansion milestone.

---

## 3. Locked invariants (Stage 12 must preserve)

Any Stage 12 **benchmark harness** or **eval code** must not violate these unless a **separate** architecture exception is approved:

| ID | Invariant |
|----|-----------|
| **S1** | **Compat:** `run_hierarchical(..., compatibility_mode=True)` remains **exact delegation** to `run_deterministic` — same contract as today. |
| **S2** | **No new compat-visible top-level hierarchical keys** on `loop_output` — `tests/hierarchical_test_locks.py` authority unchanged for contract lists. |
| **S3** | **`len(phases) != 2`** guard on non-compat path remains (`NotImplementedError`) unless explicitly re-approved — **no** 3+ phase work in Stage 12 deliverables. |
| **S4** | **`run_deterministic`** — **no edits** as part of Stage 12 default scope. Benchmarks may **call** it; they do not **change** it. |
| **S5** | **Orchestration policy branches** in `deterministic_runner.py` (RETRY / REPLAN / STOP, etc.) — **no edits** unless **benchmark evidence** documents a **targeted** bugfix and it is reviewed as an exception to “measurement-only.” Default: **zero** production orchestration changes. |

Eval harness code should live under **`tests/`** and **`scripts/`** and **docs** — not as a parallel execution engine.

---

## 4. Candidate evaluation

### Candidate 1 — Software-agent benchmark + execution-quality audit harness

**What problem it solves:** There is **no** shared, repeatable signal for “did execution improve?” across planner, retrieval, tools, and goal evaluation. Product and engineering are **guessing** priority order for retrieval merge vs clarification vs planner tuning. A **benchmark + audit** fixes **observability of outcome quality** on a **fixed task corpus**.

**Exact files likely to change (new / extended):**

- `tests/evals/test_software_agent_benchmark.py` (or equivalent under `tests/evals/`)
- `tests/evals/fixtures/...` (tasks, expected rubrics, optional golden snippets — **format TBD in implementation**)
- `scripts/run_agent_eval.py` (or equivalent CLI entrypoint)
- `Docs/STAGE12_BENCHMARK_AUDIT.md` (scorecard template, how to run, what is measured — **not** pre-filled with fake results)

**Blast radius:** **Low to medium** — mostly **new** test and script paths; must not alter production contracts if S1–S5 hold.

**Why now:** Orchestration control plane is **in place**; Stage 11 **explicitly deferred** feature expansion pending evidence. **This is the correct sequencing.**

**Why not later:** Every month of orchestration expansion **without** a benchmark **increases** rework when execution quality is finally measured.

---

### Candidate 2 — Retrieval handoff merge (`prior_phase_ranked_context` / related keys)

**What problem it solves:** Phase 1 may under-use Phase 0 context for ranking (handoff injected but not merged in execution pipeline — see `Docs/RETRIEVAL_HANDOFF_MERGE_DESIGN_QUESTIONS.md`).

**Exact files likely to change:** `execution_loop.py`, `step_dispatcher.py`, possibly `replanner.py`, `config/agent_config.py`, plus tests — **frozen / high-risk** surface per prior memos.

**Blast radius:** **High** — affects core execution for hierarchical runs.

**Why now:** Only if benchmark shows **retrieval** is the **dominant** failure mode **after** baselines — **not** as Stage 12 default.

**Why not now (Stage 12 default):** **No merge spec** is locked; no **evidence** that orchestration is the bottleneck. Doing merge **before** benchmark is **guesswork**.

---

### Candidate 3 — `REQUEST_CLARIFICATION` implementation

**What problem it solves:** Terminal failure vs “ask the user” is not first-class in `loop_output` / caller contract.

**Exact files likely to change:** `deterministic_runner.py`, `parent_plan.py` or new schema module, callers, `tests/test_two_phase_execution.py`, likely `hierarchical_test_locks.py` if new top-level keys.

**Blast radius:** **Very high** — caller contract + possible lock expansion.

**Why now:** Only after `Docs/REQUEST_CLARIFICATION_MEASUREMENT_CRITERIA.md` hold-expiry and **Stage 12 precoding** — **not** as Stage 12 core.

**Why not now:** Stage 12 is **measurement**, not **policy expansion**.

---

### Candidate 4 — 3+ phase decomposition

**What problem it solves:** Some instructions might need >2 phases; roadmap discusses broader decomposition.

**Exact files likely to change:** `deterministic_runner.py`, `plan_resolver.py`, tests, possibly schema — **large** change.

**Blast radius:** **Very high** — new control-plane semantics.

**Why now:** **Not** — no benchmark proves two-phase is insufficient for **defined** tasks.

**Why not now:** **Explicitly out of scope** for Stage 12 (see **S3**).

---

## 5. Recommendation

### Chosen Stage 12 slice — **Candidate 1: Software-agent benchmark + execution-quality audit harness**

**Rationale (blunt):**

- Adding **more planner or orchestration features** before a benchmark is **guesswork**. You will optimize the wrong layer and ship **confidence without evidence**.
- **Retrieval merge**, **clarification**, and **3+ phases** are **downstream** of knowing whether **execution** hits a bar on **representative tasks**. Stage 12 **buys the scorecard**; it does not **buy** new product semantics.

---

## 6. Stage 12 deliverables (definition)

| Deliverable | Description |
|-------------|-------------|
| **Benchmark task corpus** | Fixed, versioned set of tasks (categories TBD: e.g. explain, search, edit-smoke — **no invented results in this memo**) |
| **Pytest-driven eval harness** | Automated runs that invoke the **existing** agent entrypoints (`run_deterministic` / `run_hierarchical` as appropriate) **without** forking the execution engine |
| **Artifact capture per run** | Traces, logs, `loop_output` snapshots, optional diff summaries — **schema TBD** in implementation |
| **Scorecard / audit summary doc** | `Docs/STAGE12_BENCHMARK_AUDIT.md` — how to run, what metrics mean, how to regress; **results** filled by **running** the harness, not by this memo |

**This memo does not invent implementation results, pass rates, or metrics.** Those belong in the audit doc **after** the harness exists.

---

## 7. Likely files to touch (illustrative)

| Path | Role |
|------|------|
| `tests/evals/test_software_agent_benchmark.py` | Pytest entrypoints for benchmark cases |
| `tests/evals/fixtures/...` | Task definitions, optional expected artifacts |
| `scripts/run_agent_eval.py` | Optional CLI for CI / local batch runs |
| `Docs/STAGE12_BENCHMARK_AUDIT.md` | Human-readable scorecard + methodology |

Exact layout may vary; names above are **targets**, not a mandate to create files in this memo’s pass.

---

## 8. Do not do yet (Stage 12 scope guards)

| Item | Reason |
|------|--------|
| **Retrieval handoff merge** | Needs merge spec + benchmark evidence — `RETRIEVAL_HANDOFF_MERGE_DESIGN_QUESTIONS.md` |
| **`REQUEST_CLARIFICATION` implementation** | Stage 12 is measurement; see `REQUEST_CLARIFICATION_MEASUREMENT_CRITERIA.md` |
| **3+ phases** | **S3**; roadmap gate |
| **Widening `_is_two_phase_docs_code_intent`** | Detection precision; use traces / benchmark first |
| **Hierarchical contract expansion** (`hierarchical_test_locks.py` new top-level keys) | Not without explicit contract memo |

---

## 9. Closing

**Stage 12 is execution-quality measurement, not orchestration expansion.**

Ship the benchmark and audit harness; **then** use the scorecard to choose among retrieval merge, clarification, planner changes, or narrow orchestration fixes — **in that order of evidence**, not by roadmap appetite.

---

*End of Stage 12 decision memo.*
