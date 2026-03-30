# Stage 13 — Architecture Decision Memo

**Audience:** Principal engineer / release owner  
**Type:** Principal Engineer Decision Memo  
**Date:** 2026-03-20  
**Branch context:** `next/stage3-from-stage2-v1`  
**Precondition:** Stage 12 benchmark audit completed; evidence indicates **retrieval** and **use of phase handoff context** (ranked / merged context across phases) is the **dominant bottleneck** (not parent-policy churn, not phase count, not clarification-by-default).  
**Status:** Decision memo only. **No code change implied by this document.**

---

## Executive recommendation

**Stage 13 = retrieval handoff merge — design plus bounded implementation.**

Produce a **precoding design** (merge semantics, data shape, failure modes, rollback) and then implement **only** what that design commits to—**no** open-ended refactor of Mode 1 execution.

---

## Why this follows from Stage 12 evidence

Stage 12 established a **repeatable benchmark** and audit discipline. When the dominant failure mode is **wrong or missing context** in Phase 1 after Phase 0 (e.g. **docs lane** findings not influencing **code lane** ranking, or handoff injected but **not merged** into the execution pipeline), **more orchestration policy** or **more phases** does not fix the root cause: **retrieval and context handoff** must be **correctly fused** into the path the agent actually uses to reason and edit.

**Therefore:** the next funded slice should **close the handoff gap** between phases **as designed**—not add planner features or expand decomposition.

---

## `REQUEST_CLARIFICATION` — defer unless evidence says otherwise

**`REQUEST_CLARIFICATION`** remains **out of scope** for Stage 13 **unless** Stage 12 (or follow-on eval) shows a **distinct cluster** of failures from **ambiguous instructions** (missing parameters, unstated constraints) **rather than** missing context from retrieval/handoff. Measurement criteria remain in `Docs/REQUEST_CLARIFICATION_MEASUREMENT_CRITERIA.md`.

---

## 3+ phases — deferred

**No** move to **3+ phase** decomposition in Stage 13. The **`len(phases) != 2`** guard and roadmap gates stay in force until handoff merge is **measured** and **stable**.

---

## Frozen modules (unless explicitly approved for this slice)

The following are **frozen** for Stage 13 **by default**:

| Module | Rationale |
|--------|-----------|
| `agent/orchestrator/execution_loop.py` | Core execution surface; high blast radius. |
| `agent/orchestrator/step_dispatcher.py` | Tool dispatch and step routing; high blast radius. |
| `agent/orchestrator/replanner.py` | Replanner behavior; easy to confuse with retrieval fixes. |

**Any** change to these files in Stage 13 requires **explicit written approval** in the Stage 13 **implementation plan** (scope line + reviewer sign-off), and must be **minimal** (e.g. a single call-site to inject merged context) **not** a rewrite.

**Preferred path:** implement handoff merge **behind** narrow, well-tested interfaces **adjacent** to retrieval/context assembly so the frozen modules **do not** change unless unavoidable.

---

## Precoding design before implementation

**No implementation** starts until:

1. **`Docs/RETRIEVAL_HANDOFF_MERGE_DESIGN_QUESTIONS.md`** (or successor) is resolved into a **single design doc** for Stage 13: what keys are merged, **when** merge happens, **idempotency**, and **compat** vs **hierarchical** behavior.
2. **Trace / observability** contract is defined: what is logged **before** and **after** merge for debugging.
3. **Rollback** story: feature flag or config gate if needed.

This memo does **not** substitute for that design; it **authorizes** the slice.

---

## Likely files (illustrative)

Exact paths follow the design; **likely** touchpoints:

| Area | Likely files / modules |
|------|-------------------------|
| Context assembly / retrieval ranker | Modules that build `ranked_context` / `context` for the loop (names may vary under `agent/`). |
| Hierarchical handoff | Where `prior_phase_*` or handoff payloads are produced/consumed (per plan). |
| Config | `config/agent_config.py` — toggles, limits, merge policy. |
| Tests | `tests/` — hierarchical + compat contract tests; **no** loosening of `tests/hierarchical_test_locks.py` without explicit contract update. |

**Nothing in this list is claimed to exist or already be edited.**

---

## Risks

| Risk | Mitigation |
|------|------------|
| **Scope creep into replanner / planner** | **Policy** stays in orchestration; **Stage 13** is **merge semantics** only. |
| **Breaking compat** | Compat path must remain **exact delegation**; merge applies **only** where design says **hierarchical** (or explicitly approved). |
| **Silent ranking regressions** | Before/after traces + **fixture** benchmark reruns. |
| **Touching frozen files** | Default **no**; if unavoidable, smallest diff + dedicated review. |

---

## Invariants to preserve

| ID | Invariant |
|----|-----------|
| **R1** | **Compat:** `run_hierarchical(..., compatibility_mode=True)` remains **exact delegation** to `run_deterministic` unless a **separate** architecture exception is approved. |
| **R2** | **No new compat-visible top-level hierarchical keys** on `loop_output` without lock updates (`tests/hierarchical_test_locks.py`). |
| **R3** | **`len(phases) != 2`** guard unchanged unless **separate** approval. |
| **R4** | **Retrieval pipeline order** unchanged (extend only; never reorder stages). |
| **R5** | **No duplicate retrieval systems** — merge extends existing pipeline outputs. |

---

## Observability needs

Stage 13 must ship **observable** evidence, not only “it feels better”:

- **Structured fields** (or trace events) for: **handoff payload size**, **merge applied** (yes/no), **sources** (phase 0 vs phase 1), **merge key** version.
- **Benchmark reruns** with same corpus; compare **failure_class** or task-level failure notes **before/after** merge.
- **No** shipping without a way to answer: “Did merge run this step?”

---

## Non-goals (Stage 13)

| Non-goal | Notes |
|----------|--------|
| **`REQUEST_CLARIFICATION`** | Unless ambiguity failures are **explicitly** evidenced in benchmark. |
| **3+ phases** | Deferred. |
| **New parent-policy outcomes** | Not the slice. |
| **Widening `_is_two_phase_docs_code_intent`** | Deferred. |
| **Large refactors** of `execution_loop.py` / `step_dispatcher.py` / `replanner.py` | **Frozen** unless explicitly approved. |

---

## Smallest viable slice

1. **Design doc** approved (merge semantics + observability + rollback).  
2. **Bounded implementation** in **non-frozen** modules where possible; **frozen** touch only with approval.  
3. **Tests**: hierarchical + compat regression; **benchmark** subset rerun.  
4. **Short audit** (separate doc) with **before/after** metrics—not this memo.

---

## Closing

**Stage 13 is about making phase handoff context actually usable in retrieval and execution—not about adding orchestration or phases.**

---

*End of Stage 13 decision memo.*
